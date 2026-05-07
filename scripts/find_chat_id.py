"""Tenta achar o chat_id chamando /getUpdates do bot."""
import json
import httpx

from bugfinder.config import CONFIG

token = CONFIG.telegram_bot_token
if not token:
    print("ERRO: TELEGRAM_BOT_TOKEN ausente do .env")
    raise SystemExit(1)

# 1) verifica que o bot está online
me = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10).json()
if not me.get("ok"):
    print("ERRO no getMe:", me)
    raise SystemExit(1)
b = me["result"]
print(f"bot online: @{b.get('username')} ({b.get('first_name')})")

# 2) lista updates
upd = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10).json()
if not upd.get("ok"):
    print("ERRO no getUpdates:", upd)
    raise SystemExit(1)
results = upd.get("result", [])
print(f"updates pendentes: {len(results)}")

chat_ids = set()
for r in results:
    msg = r.get("message") or r.get("channel_post") or r.get("edited_message") or {}
    chat = msg.get("chat") or {}
    if chat.get("id"):
        chat_ids.add((chat["id"], chat.get("type"),
                      chat.get("first_name") or chat.get("title") or ""))

if chat_ids:
    print("\nchat_ids encontrados:")
    for cid, kind, name in chat_ids:
        print(f"  {cid}  ({kind})  {name}")
    print(f"\n>>> use o primeiro: TELEGRAM_CHAT_ID={list(chat_ids)[0][0]}")
else:
    print("\nNENHUM update — você precisa mandar uma mensagem pro bot primeiro.")
    print(f"  1. abre @{b.get('username')} no Telegram")
    print(f"  2. clica em 'Start' (ou manda 'oi')")
    print(f"  3. roda esse script de novo")
