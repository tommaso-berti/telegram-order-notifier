# ====================================================
# TELEGRAM ORDER NOTIFIER (TON) – VPS SETUP STEPS
# ====================================================

# ----------------------------------------------------
# 0) PREREQUISITES (run as root or sudo)
# ----------------------------------------------------
timedatectl set-timezone Europe/Rome
apt-get update
apt-get install -y python3 python3-venv git

# ----------------------------------------------------
# 1) CREATE SYSTEM USER AND FOLDERS
# ----------------------------------------------------
adduser --system --home /opt/telegram-order-notifier --group ton
mkdir -p /opt/telegram-order-notifier/app /opt/telegram-order-notifier/run
chown -R ton:ton /opt/telegram-order-notifier
chmod -R 750 /opt/telegram-order-notifier

# ----------------------------------------------------
# 2) CLONE YOUR REPO INTO /opt (run as user ton)
# ----------------------------------------------------
sudo -u ton bash -lc '
cd /opt/telegram-order-notifier/app
git clone <YOUR_REPO_SSH_URL> . || true
git fetch --all --prune
git reset --hard origin/master   # or origin/maing
'

# ----------------------------------------------------
# 3) CREATE .env WITH TELEGRAM CREDENTIALS
# ----------------------------------------------------
nano /opt/telegram-order-notifier/.env
# TELEGRAM_BOT_TOKEN=123456:ABC...
# TELEGRAM_CHAT_ID=987654321

chown ton:ton /opt/telegram-order-notifier/.env
chmod 600 /opt/telegram-order-notifier/.env

# ----------------------------------------------------
# 4) VIRTUAL ENVIRONMENT + PYTHON DEPENDENCIES
# ----------------------------------------------------
sudo -u ton bash -lc '
python3 -m venv /opt/telegram-order-notifier/app/.venv
/opt/telegram-order-notifier/app/.venv/bin/pip install --upgrade pip
if [ -f /opt/telegram-order-notifier/app/requirements.txt ]; then
/opt/telegram-order-notifier/app/.venv/bin/pip install -r requirements.txt
else
/opt/telegram-order-notifier/app/.venv/bin/pip install yfinance python-telegram-bot pyyaml pandas
fi
'

# ----------------------------------------------------
# 5) INSTALL SYSTEMD SERVICE AND TIMER (files are in repo)
# ----------------------------------------------------
cp /opt/telegram-order-notifier/app/orderbot.service /etc/systemd/system/
cp /opt/telegram-order-notifier/app/orderbot.timer   /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now orderbot.timer

# Optional first run:
systemctl start orderbot.service
journalctl -u orderbot.service -f

# ----------------------------------------------------
# 6) SETUP SSH FOR GITHUB ACTIONS (run on VPS)
# ----------------------------------------------------
sudo -u ton mkdir -p ~ton/.ssh
sudo -u ton chmod 700 ~ton/.ssh

echo "ssh-ed25519 AAAA...YOUR_GITHUB_ACTION_PUBLIC_KEY" | sudo -u ton tee -a ~ton/.ssh/authorized_keys >/dev/null
sudo -u ton chmod 600 ~ton/.ssh/authorized_keys

# Test:
# ssh -i /path/to/private_key ton@<VPS_HOST> "echo ok"

# ----------------------------------------------------
# 7) ADD THESE SECRETS IN GITHUB → Settings → Secrets → Actions
# ----------------------------------------------------
# VPS_HOST        = <server_ip_or_hostname>
# VPS_USER        = ton
# SSH_PRIVATE_KEY = <private_key_that_matches_authorized_keys>

# (REPO_URL is NOT needed if you already cloned manually)

# ----------------------------------------------------
# 8) HOW CI/CD DEPLOY WORKS
# ----------------------------------------------------
# - Push or merge to master on GitHub
# - GitHub Action does:
#     git fetch & reset → install deps → restart service

# ----------------------------------------------------
# 9) USEFUL COMMANDS (VPS)
# ----------------------------------------------------
systemctl status orderbot.service --no-pager
systemctl status orderbot.timer --no-pager
journalctl -u orderbot.service -n 200 --no-pager

systemctl restart orderbot.service

ls -lah /opt/telegram-order-notifier/run

# ----------------------------------------------------
# 10) VPS DIRECTORY STRUCTURE (FINAL)
# ----------------------------------------------------
# /opt/telegram-order-notifier/
# ├── app/          ← git repo, code, .venv
# ├── run/          ← logs & CSV output
# └── .env          ← secrets (not in git)
#
# /etc/systemd/system/
# ├── orderbot.service
# └── orderbot.timer
