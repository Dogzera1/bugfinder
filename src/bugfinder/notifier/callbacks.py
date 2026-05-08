"""
Processa callback_query do Telegram (cliques nos botões inline) e atualiza
o status do candidato no DB.

Persiste o offset do getUpdates em <data_dir>/.tg_offset.json pra não reprocessar
o mesmo update após restart.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .telegram import CALLBACK_ACTIONS, TelegramNotifier, _escape_md


def _load_offset(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        v = data.get("offset")
        return int(v) if v is not None else None
    except Exception:
        return None


def _save_offset(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": offset}), encoding="utf-8")


def drain_callbacks(
    *,
    notifier: TelegramNotifier,
    storage,  # Storage — evita import circular
    offset_path: Path,
    long_poll_timeout: int = 0,
    on_event: Callable[[str, dict], None] | None = None,
) -> int:
    """
    Lê todos os callbacks pendentes do Telegram e aplica:
      - atualiza status do candidato no DB
      - confirma o clique (answerCallbackQuery)
      - edita a mensagem appendando "✓ marcado como X"
      - remove os botões pra evitar duplo clique

    Retorna a quantidade de callbacks processados.
    """
    offset = _load_offset(offset_path)
    next_offset = offset
    updates = notifier.get_updates(
        offset=offset,
        timeout=long_poll_timeout,
        allowed_updates=["callback_query"],
    )
    if not updates:
        return 0

    processed = 0
    for upd in updates:
        update_id = int(upd.get("update_id", 0))
        next_offset = update_id + 1  # avança mesmo se o update for irrelevante
        cq = upd.get("callback_query")
        if not cq:
            continue

        cb_id = cq.get("id")
        data = cq.get("data") or ""
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        message_id = msg.get("message_id")
        original_text = msg.get("text") or ""

        # Parse "buy:42" → ("buy", 42)
        try:
            action, raw_id = data.split(":", 1)
            candidate_id = int(raw_id)
        except (ValueError, AttributeError):
            if cb_id:
                try:
                    notifier.answer_callback_query(cb_id, text="callback inválido")
                except Exception:
                    pass
            continue

        mapping = CALLBACK_ACTIONS.get(action)
        if not mapping:
            if cb_id:
                try:
                    notifier.answer_callback_query(cb_id, text="ação desconhecida")
                except Exception:
                    pass
            continue
        new_status, label = mapping

        # 1) atualiza DB
        try:
            storage.update_candidate_status(candidate_id, new_status)
        except Exception as e:
            if cb_id:
                try:
                    notifier.answer_callback_query(
                        cb_id, text=f"erro ao salvar: {e}", show_alert=True,
                    )
                except Exception:
                    pass
            if on_event:
                on_event("error", {"candidate_id": candidate_id, "error": str(e)})
            continue

        # 2) confirma o clique no UI
        if cb_id:
            try:
                notifier.answer_callback_query(cb_id, text=f"marcado: {label}")
            except Exception:
                pass

        # 3) edita a mensagem: append rodapé + remove botões
        if chat_id is not None and message_id is not None:
            footer = f"✓ marcado como *{_escape_md(label)}*"
            try:
                notifier.append_action_footer(
                    chat_id=chat_id, message_id=message_id,
                    original_text=original_text, footer=footer,
                )
            except Exception:
                # se a edição falhar (ex: msg já editada), só tenta tirar os botões
                try:
                    notifier.edit_message_reply_markup(
                        chat_id=chat_id, message_id=message_id, reply_markup=None,
                    )
                except Exception:
                    pass

        processed += 1
        if on_event:
            on_event("callback_applied", {
                "candidate_id": candidate_id,
                "action": action,
                "status": new_status,
            })

    if next_offset is not None:
        _save_offset(offset_path, next_offset)
    return processed
