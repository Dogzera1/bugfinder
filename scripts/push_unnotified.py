"""Envia candidatos pendentes via Telegram (validação E2E do formato)."""
from bugfinder.config import CONFIG
from bugfinder.notifier import TelegramNotifier
from bugfinder.storage import Storage

storage = Storage(CONFIG.db_full_path)
rows = storage.list_unnotified(
    min_roi_pct=15.0, min_match_confidence=0.5, limit=10,
)
print(f"unnotified: {len(rows)}")
if not rows:
    raise SystemExit(0)

with TelegramNotifier(CONFIG.telegram_bot_token, CONFIG.telegram_chat_id) as tg:
    sent = []
    for r in rows:
        try:
            tg.send_candidate(r)
            sent.append(r["id"])
            print(f"  ✓ #{r['id']} ROI={r['via_roi_pct']:.0f}% — {r['title'][:50]}")
        except Exception as e:
            print(f"  ✗ #{r['id']}: {e}")
    storage.mark_notified(sent)
print(f"enviados e marcados: {len(sent)}")
