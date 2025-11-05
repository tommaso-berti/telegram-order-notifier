import os, sys, yaml, asyncio
from datetime import datetime
from telegram import Bot

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    print("Env non presenti: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID", file=sys.stderr)
    sys.exit(1)

CFG_PATH = "/opt/telegram-order-notifier/app/orders_config.yaml"

async def main():
    # carica config (se esiste)
    try:
        cfg = yaml.safe_load(open(CFG_PATH)) if os.path.exists(CFG_PATH) else {}
    except Exception as e:
        print(f"Warning config: {e}", file=sys.stderr)
        cfg = {}

    msg = f"✅ TON ok {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | cfg keys: {list(cfg.keys())}"
    async with Bot(token=TOKEN) as bot:
        await bot.send_message(chat_id=int(CHAT_ID), text=msg)

if __name__ == "__main__":
    asyncio.run(main())
