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


# Mapa entre callback_data prefix e (status_no_db, label_humano)
CALLBACK_ACTIONS: dict[str, tuple[str, str]] = {
    "buy":    ("bought",  "comprei"),
    "seen":   ("seen",    "visto"),
    "ign":    ("ignored", "ignorado"),
}


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

    def _api(self, method: str, payload: dict, *, timeout: float | None = None) -> dict:
        url = f"{self.BASE}/bot{self.bot_token}/{method}"
        r = self._client.post(url, json=payload, timeout=timeout or self._client.timeout)
        if r.status_code >= 400:
            raise RuntimeError(
                f"Telegram {method} falhou ({r.status_code}): {r.text[:300]}"
            )
        return r.json()

    def send_text(self, text: str, *, parse_mode: str = "MarkdownV2",
                  disable_preview: bool = True,
                  reply_markup: dict | None = None) -> dict:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
            "link_preview_options": {"is_disabled": disable_preview},
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._api("sendMessage", payload)

    def send_test(self) -> dict:
        return self.send_text(
            "🤖 *Bug Finder ativo* — notificações chegando aqui\\.",
            parse_mode="MarkdownV2",
        )

    @staticmethod
    def _build_action_keyboard(candidate_id: int) -> dict:
        return {
            "inline_keyboard": [[
                {"text": "✅ Comprei",  "callback_data": f"buy:{candidate_id}"},
                {"text": "👁 Visto",    "callback_data": f"seen:{candidate_id}"},
                {"text": "🚫 Ignorar",  "callback_data": f"ign:{candidate_id}"},
            ]]
        }

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

        # Sinal de histórico — só aparece quando temos dados (>=2 pontos)
        try:
            hist_count = c["hist_count"]
            hist_p50 = c["hist_p50"]
            hist_min = c["hist_min"]
            hist_outlier = c["hist_is_outlier"]
        except (IndexError, KeyError):
            hist_count = None
            hist_p50 = hist_min = hist_outlier = None
        if hist_count and hist_count >= 2:
            if hist_outlier:
                hist_line = (
                    f"📉 *outlier histórico* "
                    f"\\(mín {_escape_md(_fmt_brl(hist_min))} em "
                    f"{hist_count} obs\\)"
                )
            elif hist_p50 and c["price"] >= hist_p50:
                hist_line = (
                    f"⚠️ preço {_escape_md(_fmt_brl(c['price']))} ≥ mediana "
                    f"histórica \\({_escape_md(_fmt_brl(hist_p50))}, "
                    f"{hist_count} obs\\) — old\\_price pode estar inflado"
                )
            else:
                hist_line = (
                    f"📊 histórico: mediana "
                    f"{_escape_md(_fmt_brl(hist_p50))} em {hist_count} obs"
                )
            lines.append(hist_line)

        if coupon:
            lines.append(f"🎟 cupom: `{_escape_md(coupon)}`")

        text = "\n".join(lines)
        markup = self._build_action_keyboard(int(c["id"]))
        return self.send_text(text, reply_markup=markup)

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

    # ---- Callback handling (inline buttons) ----

    def get_updates(self, *, offset: int | None = None,
                    timeout: int = 0,
                    allowed_updates: list[str] | None = None) -> list[dict]:
        """
        Long-polling de updates. timeout=0 = curto (sem long polling),
        timeout>0 deixa o servidor segurar a conexão até chegar update.
        Retorna a lista de updates crus.
        """
        payload = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        # http timeout precisa ser maior que o long poll do Telegram
        http_timeout = max(self._client.timeout.read or 15.0, timeout + 5)
        data = self._api("getUpdates", payload, timeout=http_timeout)
        return data.get("result") or []

    def answer_callback_query(self, callback_query_id: str, text: str = "",
                              show_alert: bool = False) -> dict:
        """Confirma o clique do botão (faz o spinner sumir no app)."""
        return self._api("answerCallbackQuery", {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
        })

    def edit_message_reply_markup(self, *, chat_id: int | str,
                                  message_id: int,
                                  reply_markup: dict | None) -> dict:
        """Edita só os botões (ou remove). Útil pra travar uma mensagem após ação."""
        payload = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._api("editMessageReplyMarkup", payload)

    def edit_message_caption(self, *, chat_id: int | str, message_id: int,
                             caption: str) -> dict:
        return self._api("editMessageCaption", {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": caption,
        })

    def append_action_footer(self, *, chat_id: int | str, message_id: int,
                             original_text: str, footer: str) -> dict:
        """Edita texto da mensagem appendando um rodapé com a ação tomada."""
        new_text = f"{original_text}\n\n{footer}"
        return self._api("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": new_text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
            "link_preview_options": {"is_disabled": True},
        })
