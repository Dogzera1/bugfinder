"""
Notificador Telegram via Bot API HTTP (sem SDK adicional, só httpx).

Setup:
  1. Falar com @BotFather no Telegram, criar bot novo, receber TOKEN
  2. Achar seu chat_id: mande qualquer msg pro seu bot, depois acesse
       https://api.telegram.org/bot<TOKEN>/getUpdates
     e copie o "id" do "chat" no JSON.
  3. Coloca no .env:
       TELEGRAM_BOT_TOKEN=...
       TELEGRAM_CHAT_ID=...
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

import httpx


class TelegramConfigError(RuntimeError):
    pass


def _fmt_brl(v: float | None) -> str:
    if v is None:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


_MD2_SPECIAL = set(r"_*[]()~`>#+-=|{}.!\\")


def _escape_md(s: str) -> str:
    """
    MarkdownV2 do Telegram. Escapa char-by-char (loop sobre `replace` quebra:
    o `\\` que adicionamos no escape de `-` é re-escapado depois).
    """
    if not s:
        return ""
    out = []
    for ch in s:
        if ch in _MD2_SPECIAL:
            out.append("\\")
        out.append(ch)
    return "".join(out)


class TelegramNotifier:
    BASE = "https://api.telegram.org"

    def __init__(self, bot_token: str | None, chat_id: str | None,
                 timeout: float = 15.0) -> None:
        if not bot_token or not chat_id:
            raise TelegramConfigError(
                "TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID precisam estar no .env. "
                "Ver instruções em src/bugfinder/notifier/telegram.py"
            )
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()

    def close(self):
        self._client.close()

    # ---- API ----

    def send_text(self, text: str, *, parse_mode: str = "MarkdownV2",
                  disable_preview: bool = True) -> dict:
        url = f"{self.BASE}/bot{self.bot_token}/sendMessage"
        r = self._client.post(url, json={
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
            "link_preview_options": {"is_disabled": disable_preview},
        })
        if r.status_code >= 400:
            raise RuntimeError(
                f"Telegram sendMessage falhou ({r.status_code}): "
                f"{r.text[:300]}"
            )
        return r.json()

    def send_test(self) -> dict:
        return self.send_text(
            "🤖 *Bug Finder ativo* — notificações chegando aqui\\.",
            parse_mode="MarkdownV2",
        )

    def send_candidate(self, c: sqlite3.Row) -> dict:
        """Mensagem rica pra um candidato (linha do storage.list_unnotified)."""
        title = c["title"]
        url = c["url"]
        store = c["store_name"] or "—"
        price = _fmt_brl(c["price"])
        old = _fmt_brl(c["old_price"]) if c["old_price"] else None
        disc = c["discount_pct"]
        roi = c["via_roi_pct"]
        margin = _fmt_brl(c["via_margin_brl"]) if c["via_margin_brl"] is not None else None
        ml_p25 = _fmt_brl(c["ml_p25"]) if c["ml_p25"] else None
        coupon = c["coupon_code"]
        match_conf = c["ml_match_confidence"]

        # Emoji de ROI
        if roi is None:
            roi_emoji = "❓"
            roi_str = "—"
        elif roi >= 30:
            roi_emoji = "🟢🔥"
            roi_str = f"\\+{roi:.0f}%"
        elif roi >= 10:
            roi_emoji = "🟢"
            roi_str = f"\\+{roi:.0f}%"
        elif roi >= 0:
            roi_emoji = "🟡"
            roi_str = f"\\+{roi:.0f}%"
        else:
            roi_emoji = "🔴"
            roi_str = f"{roi:.0f}%"

        lines = [
            f"{roi_emoji} *ROI estimado: {roi_str}*",
            f"[{_escape_md(title)}]({url})",
            f"🏬 {_escape_md(store)}  💰 *{_escape_md(price)}*"
            + (f"  ~{_escape_md(old)}~" if old else "")
            + f"  \\({disc:.0f}% off\\)",
        ]
        if ml_p25:
            extra = f"📊 ML p25: {_escape_md(ml_p25)}"
            if margin and roi is not None and roi >= 0:
                extra += f"  ➕ margem: {_escape_md(margin)}"
            elif margin:
                extra += f"  ➖ déficit: {_escape_md(margin)}"
            if match_conf is not None:
                extra += f"  \\(match {match_conf*100:.0f}%\\)"
            lines.append(extra)
        if coupon:
            lines.append(f"🎟 cupom: `{_escape_md(coupon)}`")

        text = "\n".join(lines)
        return self.send_text(text)

    def send_candidates_batch(self, rows: Iterable[sqlite3.Row]) -> int:
        n = 0
        for r in rows:
            try:
                self.send_candidate(r)
                n += 1
            except Exception:
                # não interrompe o lote por erro individual
                continue
        return n
