"""
M15 trading bot — EMA trend + RSI pullback, ATR-based stop/target.
Runs on 15-minute candles via ccxt (Binance, Bybit, Kraken, etc.).

DRY_RUN=true (default) = paper trading only. No real orders are sent.
"""
import os
import time
import logging
from datetime import datetime, timezone

import ccxt
import pandas as pd

# ---------- Config (set these as Railway variables) ----------
EXCHANGE = os.getenv("EXCHANGE", "binance")
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = "15m"
RISK_PCT = float(os.getenv("RISK_PCT", "1.0"))       # % of balance risked per trade
ATR_SL = float(os.getenv("ATR_SL", "1.5"))           # stop = 1.5 x ATR
ATR_TP = float(os.getenv("ATR_TP", "3.0"))           # target = 3 x ATR (1:2 R:R)
DRY_RUN = os.getenv("DRY_RUN", "true").lower() != "false"
PAPER_BALANCE = float(os.getenv("PAPER_BALANCE", "1000"))
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("m15")


# ---------- Indicators ----------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema_fast"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_trend"] = df["close"].ewm(span=200, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return df


def signal(df: pd.DataFrame) -> str | None:
    """Look at the last CLOSED candle only."""
    c, p = df.iloc[-1], df.iloc[-2]
    uptrend = c.close > c.ema_trend and c.ema_fast > c.ema_slow
    downtrend = c.close < c.ema_trend and c.ema_fast < c.ema_slow
    # Pullback: RSI dipped and is turning back with the trend
    if uptrend and p.rsi < 45 <= c.rsi:
        return "long"
    if downtrend and p.rsi > 55 >= c.rsi:
        return "short"
    return None


# ---------- Exchange helpers ----------
def make_exchange():
    ex = getattr(ccxt, EXCHANGE)({
        "apiKey": API_KEY, "secret": API_SECRET, "enableRateLimit": True,
    })
    ex.load_markets()
    return ex


def fetch_candles(ex, limit=300) -> pd.DataFrame:
    raw = ex.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.iloc[:-1].reset_index(drop=True)  # drop the still-forming candle


def sleep_to_next_candle():
    now = time.time()
    nxt = (now // 900 + 1) * 900 + 5  # 5s after each 15-min close
    time.sleep(max(1, nxt - now))


# ---------- Main loop ----------
def main():
    ex = make_exchange()
    balance = PAPER_BALANCE
    pos = None  # dict(side, entry, sl, tp, qty)
    log.info("Starting %s %s %s | DRY_RUN=%s", EXCHANGE, SYMBOL, TIMEFRAME, DRY_RUN)

    while True:
        try:
            df = add_indicators(fetch_candles(ex))
            c = df.iloc[-1]

            # Manage open position (checks candle high/low against SL/TP)
            if pos:
                hit_sl = c.low <= pos["sl"] if pos["side"] == "long" else c.high >= pos["sl"]
                hit_tp = c.high >= pos["tp"] if pos["side"] == "long" else c.low <= pos["tp"]
                if hit_sl or hit_tp:
                    exit_px = pos["sl"] if hit_sl else pos["tp"]
                    sign = 1 if pos["side"] == "long" else -1
                    pnl = (exit_px - pos["entry"]) * pos["qty"] * sign
                    balance += pnl
                    log.info("EXIT %s @ %.2f (%s) PnL %.2f | balance %.2f",
                             pos["side"], exit_px, "SL" if hit_sl else "TP", pnl, balance)
                    if not DRY_RUN:
                        ex.create_market_order(SYMBOL, "sell" if pos["side"] == "long" else "buy",
                                               pos["qty"], params={"reduceOnly": True})
                    pos = None

            # Look for new entry
            if not pos:
                sig = signal(df)
                if sig:
                    stop_dist = c.atr * ATR_SL
                    qty = (balance * RISK_PCT / 100) / stop_dist
                    qty = float(ex.amount_to_precision(SYMBOL, qty))
                    entry = c.close
                    sl = entry - stop_dist if sig == "long" else entry + stop_dist
                    tp = entry + c.atr * ATR_TP if sig == "long" else entry - c.atr * ATR_TP
                    pos = dict(side=sig, entry=entry, sl=sl, tp=tp, qty=qty)
                    log.info("ENTRY %s qty %.6f @ %.2f SL %.2f TP %.2f",
                             sig, qty, entry, sl, tp)
                    if not DRY_RUN:
                        ex.create_market_order(SYMBOL, "buy" if sig == "long" else "sell", qty)
                else:
                    log.info("No signal | close %.2f RSI %.1f", c.close, c.rsi)
        except Exception as e:
            log.exception("Loop error: %s", e)

        sleep_to_next_candle()


if __name__ == "__main__":
    main()
