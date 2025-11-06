sudo -u deploy tee /opt/telegram-order-notifier/app/order_bot.py >/dev/null <<'PY'
import os, sys, math, asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml
import pandas as pd
import yfinance as yf
from telegram import Bot

CONFIG_PATH = "/opt/telegram-order-notifier/app/orders_config.yaml"

def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}

def get_env_vars(cfg: dict):
    notify = cfg.get("notify", {}) or {}
    token_env = notify.get("token_env", "TELEGRAM_BOT_TOKEN")
    chat_env  = notify.get("chat_id_env", "TELEGRAM_CHAT_ID")
    token = os.getenv(token_env)
    chat_id = os.getenv(chat_env)
    if not token or not chat_id:
        raise RuntimeError(f"Missing env vars: {token_env} / {chat_env}")
    return token, int(chat_id)

def last_close_and_currency(ticker: str):
    t = yf.Ticker(ticker)
    try:
        currency = getattr(t.fast_info, "currency", None)
    except Exception:
        currency = None
    if not currency:
        try:
            currency = (t.info or {}).get("currency")
        except Exception:
            currency = None
    hist = t.history(period="10d", interval="1d", auto_adjust=False, actions=False)
    if hist is None or hist.empty or "Close" not in hist.columns:
        raise RuntimeError(f"No daily close for {ticker}")
    close = float(hist["Close"].dropna().iloc[-1])
    close_date = hist.index[-1].to_pydatetime().date()
    if not currency:
        currency = "EUR" if ticker.endswith(".DE") else "USD"
    return close, currency, close_date

def fx_to_eur(asset_ccy: str) -> float:
    asset = asset_ccy.upper()
    if asset == "EUR":
        return 1.0
    # Prefer direct quote USDEUR=X, GBPEUR=X, etc. If missing, invert EURXXX=X
    direct = f"{asset}EUR=X"   # e.g. USDEUR=X -> EUR per 1 USD
    inverse = f"EUR{asset}=X"  # e.g. EURUSD=X -> USD per 1 EUR, need inverse
    def _px(sym):
        try:
            h = yf.Ticker(sym).history(period="10d", interval="1d")
            if h is not None and not h.empty:
                return float(h["Close"].dropna().iloc[-1])
        except Exception:
            pass
        return None
    px = _px(direct)
    if px and px > 0:
        return px
    px_inv = _px(inverse)
    if px_inv and px_inv > 0:
        return 1.0 / px_inv
    raise RuntimeError(f"Cannot fetch FX {asset}->EUR")

def compute_levels(close: float, buy_off_pct: float, tp_pct: float, sl_pct: float):
    entry = close * (1 + buy_off_pct / 100.0)
    tp    = entry * (1 + tp_pct / 100.0)
    sl    = entry * (1 + sl_pct / 100.0)
    return entry, tp, sl

def fmt_eur(x: float) -> str:
    return f"{x:.2f} EUR"

async def main():
    cfg = load_config(CONFIG_PATH)
    tz = ZoneInfo(cfg["general"]["timezone"])
    base_ccy = cfg["general"]["base_currency"].upper()
    if base_ccy != "EUR":
        raise RuntimeError("This script is configured to always output EUR.")
    out_dir = cfg["general"]["out_dir"]
    csv_path_template = cfg["general"]["csv_path"]
    do_csv = bool(cfg["general"].get("log_csv", True))

    buy_off = float(cfg["strategy"]["buy_offset_pct"])
    tp_pct  = float(cfg["strategy"]["take_profit_pct"])
    sl_pct  = float(cfg["strategy"]["stop_loss_pct"])
    tickers = list(cfg["universe"]["tickers"])

    token, chat_id = get_env_vars(cfg)

    rows = []
    message_lines = []
    # We will base "as_of" on each ticker's last close; also keep a display header date
    header_date = None

    for tk in tickers:
        try:
            close_ccy, asset_ccy, close_date = last_close_and_currency(tk)
            if header_date is None:
                header_date = close_date
            entry_ccy, tp_ccy, sl_ccy = compute_levels(close_ccy, buy_off, tp_pct, sl_pct)
            rate = fx_to_eur(asset_ccy)  # EUR per 1 asset_ccy

            close_eur = close_ccy * rate
            entry_eur = entry_ccy * rate
            tp_eur    = tp_ccy * rate
            sl_eur    = sl_ccy * rate

            # round to 2 decimals for display and CSV
            rec = {
                "date": close_date.isoformat(),
                "ticker": tk,
                "close_eur": round(close_eur, 2),
                "entry_eur": round(entry_eur, 2),
                "take_profit_eur": round(tp_eur, 2),
                "stop_loss_eur": round(sl_eur, 2),
                "buy_offset_pct": buy_off,
                "take_profit_pct": tp_pct,
                "stop_loss_pct": sl_pct,
            }
            rows.append(rec)

            block = [
                f"Ticker: {tk}",
                f"Close:      {fmt_eur(rec['close_eur'])}",
                f"Buy Limit:  {fmt_eur(rec['entry_eur'])} ({buy_off:+.1f}%)",
                f"Take Profit:{fmt_eur(rec['take_profit_eur'])} ({tp_pct:+.1f}%)",
                f"Stop Loss:  {fmt_eur(rec['stop_loss_eur'])} ({sl_pct:+.1f}%)",
                ""
            ]
            message_lines.append("\n".join(block))
        except Exception as e:
            message_lines.append(f"Ticker: {tk}\nERROR: {e}\n")

    header_date_str = header_date.isoformat() if header_date else datetime.now(tz).date().isoformat()
    header = f"Daily Order Levels for {header_date_str} (based on previous close)\n"
    text = header + "\n".join(message_lines)

    # Save CSV if requested
    saved_path = None
    if do_csv:
        os.makedirs(out_dir, exist_ok=True)
        csv_path = datetime.now(tz).strftime(csv_path_template)
        df = pd.DataFrame(rows)
        df.to_csv(csv_path, index=False)
        saved_path = csv_path
        text += f"\nSaved to {csv_path}"

    # Send Telegram message (async, PTB v20+)
    async with Bot(token=token) as bot:
        await bot.send_message(chat_id=chat_id, text=text)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
PY
sudo chown deploy:deploy /opt/telegram-order-notifier/app/order_bot.py
sudo chmod 750 /opt/telegram-order-notifier/app/order_bot.py