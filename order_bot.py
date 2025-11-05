import os
from datetime import datetime
import yaml
from telegram import Bot

# Carica variabili dal sistema (systemd le prenderà da .env)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    raise Exception("Manca TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID nel file .env")

# Carica il file di configurazione ordini
with open("orders_config.yaml") as f:
    CONFIG = yaml.safe_load(f)

def main():
    bot = Bot(TOKEN)

    # Esempio: messaggio semplice per test
    msg = f"✅ Bot eseguito alle {datetime.now().strftime('%H:%M:%S')}"
    bot.send_message(chat_id=CHAT_ID, text=msg)

if __name__ == "__main__":
    main()
