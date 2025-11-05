import os
import sys
import math
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
import pandas as pd
import yfinance as yf
from telegram import Bot

CONFIG_PATH = "/opt/telegram-order-notifier/app/orders_config.yaml"

def load_config(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}

def get_env_token_and_chat(config: dict):
    # Sezione notify permette override dei nomi variabili, ma di default usiamo TELEGRAM_*
    token_env = (config.get("notify", {}) or {}).get("token_env", "TELEGRAM_BOT_TOKEN")
    chat_env  = (config.get("notify", {}) or {}).get("chat_id_env", "TELEGRAM_CHAT_ID")
    token = os.getenv(token_env)
    chat_id = os.getenv(chat_env)
    if not token or not chat_id:
        raise RuntimeError(f"Missing env vars: {token_env} / {chat_env}")
    return token, chat_id

def get_close_and_currency(ticker: str):
    """
    Ritorna (last_close, currency) per un ticker Yahoo.
    Usa l'ultimo Close disponibile (preferibilmente il più recente a mercato chiuso).
    """
    t = yf.Ticker(ticker)
    # Prova fast_info
    try:
        c = getattr(t.fast_info, "currency", None) or t.info.get("currency")
    except Exception:
        c = None

    # Storico 5 giorni per avere un close affidabile
    hist = t.history(period="5d", interval="1d", auto_adjust=False, actions=False)
    if hist is None or hist.empty or "Close" not in hist.columns:
        raise RuntimeError(f"No price history for {ticker}")
    # ultimo valore non NaN
    close = float(hist["Close"].dropna().iloc[-1])
    if not c:
        # fallback: prova a dedurre da ISIN/market, ma se non c'è lascia "USD" per AAPL e "EUR" per .DE
        c = "EUR" if ticker.endswith(".DE") else "USD"
    return close, c

def get_fx_rate_to_base(asset_ccy: str, base_ccy: str) -> float:
    """
    Converte 1 unità di asset_ccy in base_ccy.
    Esempio: asset=USD, base=EUR -> ritorna quante EUR vale 1 USD (≈ 1 / EURUSD=X)
    """
    asset_ccy = asset_ccy.upper()
    base_ccy = base_ccy.upper()
    if asset_ccy == base_ccy:
        return 1.0

    # Prova coppia DIRETTA: BASEASSET=X (es. EURUSD=X -> USD per EUR)
    # Noi vogliamo asset->base, cioè quanti BASE per 1 ASSET
    # Se abbiamo EURUSD=X = USD per 1 EUR, allora 1 USD = 1 / (EURUSD) EUR
    pair_direct = f"{base_ccy}{asset_ccy}=X"   # es. EURUSD=X
    pair_inverse = f"{asset_ccy}{base_ccy}=X" # es. USDEUR=X

    def _last_close(symbol: str) -> float | None:
        try:
            h = yf.Ticker(symbol).history(period="5d", interval="1d")
            if h is None or h.empty:
                return None
            return float(h["Close"].dropna().iloc[-1])
        except Exception:
            return None

    px_direct = _last_close(pair_direct)
    if px_direct and px_direct > 0:
        # px_direct = ASSET per 1 BASE? No, è asset_ccy per 1 base_ccy (es. USD per 1 EUR)
        # Noi vogliamo BASE per 1 ASSET -> 1 / px_direct
        return 1.0 / px_direct

    px_inverse = _last_close(pair_inverse)
    if px_inverse and px_inverse > 0:
        # px_inverse = base_ccy per 1 asset_ccy -> esattamente quello che vogliamo
        return px_inverse

    raise RuntimeError(f"Cannot fetch FX {asset_ccy}->{base_ccy}")

def compute_levels(close: float, buy_off_pct: float, tp_pct: float, sl_pct: float):
    entry = close * (1.0 + buy_off_pct / 100.0)
    tp    = entry * (1.0 + tp_pct / 100.0)
    sl    = entry * (1.0 + sl_pct / 100.0)
    return entry, tp, sl

def size_position(position_size_eur: float, entry_price_asset_ccy: float, fx_to_eur: float) -> int:
    """
    position_size_eur: budget in EUR
    entry_price_asset_ccy: prezzo entry nella valuta del titolo
    fx_to_eur: quanti EUR vale 1 unità di valuta del titolo
    """
    entry_in_eur = entry_price_asset_ccy * fx_to_eur
    if entry_in_eur <= 0:
        return 0
    qty = math.floor(position_size_eur / entry_in_eur)
    return max(qty, 0)

def format_money(x: float, ccy: str) -> str:
    # formattazione semplice, 2-4 decimali a seconda del valore
    if x >= 100:
        return f"{x:,.2f} {ccy}"
    elif x >= 1:
        return f"{x:,.4f} {ccy}"
    else:
        return f"{x:,.6f} {ccy}"

async def main():
    # 1) load config
    cfg = load_config(CONFIG_PATH)
    tz = ZoneInfo(cfg["general"]["timezone"])
    base_ccy = cfg["general"]["base_currency"].upper()
    pos_eur = float(cfg["strategy"]["position_size_eur"])
    buy_off = float(cfg["strategy"]["buy_offset_pct"])
    tp_pct  = float(cfg["strategy"]["take_profit_pct"])
    sl_pct  = float(cfg["strategy"]["stop_loss_pct"])
    tickers = list(cfg["universe"]["tickers"])

    # 2) env vars
    token, chat_id = get_env_token_and_chat(cfg)

    # 3) compute per-ticker
    lines = []
    header = f"📊 Order levels — {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} {cfg['general']['timezone']}\nBase: {base_ccy} | Position size: {pos_eur:.2f} EUR\n"
    lines.append(header)

    for tk in tickers:
        try:
            close, asset_ccy = get_close_and_currency(tk)
            entry, tp, sl = compute_levels(close, buy_off, tp_pct, sl_pct)
            fx_to_eur = get_fx_rate_to_base(asset_ccy, base_ccy)  # EUR per 1 asset_ccy
            qty = size_position(pos_eur, entry, fx_to_eur)
            entry_eur = entry * fx_to_eur
            total_eur = qty * entry_eur

            block = [
                f"— {tk} ({asset_ccy})",
                f"  Close: {format_money(close, asset_ccy)}",
                f"  Entry: {format_money(entry, asset_ccy)}",
                f"  TP:    {format_money(tp, asset_ccy)}",
                f"  SL:    {format_money(sl, asset_ccy)}",
                f"  Qty:   {qty}  (~{format_money(entry_eur, base_ccy)} each, total ~{format_money(total_eur, base_ccy)})",
            ]
            lines.append("\n".join(block))
        except Exception as e:
            lines.append(f"— {tk}: ERROR {e}")

    text = "\n\n".join(lines)

    # 4) send via Telegram (async API v20+)
    async with Bot(token=token) as bot:
        await bot.send_message(chat_id=int(chat_id), text=text)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)