"""
Trading bot (15m / 1h / 4h) - EMA trend + RSI pullback, ATR-based stop/target.
DRY_RUN=true (default) = paper trading only. No real orders are sent.
"""
import os
import time
import logging

import ccxt
import pandas as pd

# ---------- Config (set these as Railway variables) ----------
EXCHANGE = os.getenv("EXCHANGE", "kraken")
SYMBOL = os.getenv("SYMBOL", "BTC/USD")
TIMEFRAME = os.getenv("TIMEFRAME", "15m")              # 15m, 1h or 4h
PERIOD = {"15m": 900, "1h": 3600, "4h": 14400}[TIMEFRAME]
RISK_PCT = float(os.getenv("RISK_PCT", "1.0"))
ATR_SL = float(os.getenv("ATR_SL", "1.5"))
ATR_TP = float(os.getenv("ATR_TP", "3.0"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() != "false"
PAPER_BALANCE = float(os.getenv("PAPER_BALANCE", "1000"))
FEE_PCT = float(os.getenv("FEE_PCT", "0.4"))
MAX_LEVERAGE = float(os.getenv("MAX_LEVERAGE", "1"))
ALLOW_SHORTS = os.getenv("ALLOW_SHORTS", "true").lower() != "false"
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("m15")


def add_indicators(df):
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


def signal(df):
    c, p = df.iloc[-1], df.iloc[-2]
    uptrend = c.close > c.ema_trend and c.ema_fast > c.ema_slow
    downtrend = c.close < c.ema_trend and c.ema_fast < c.ema_slow
    if uptrend and p.rsi < 45 <= c.rsi:
        return "long"
    if downtrend and p.rsi > 55 >= c.rsi:
        return "short"
    return None


def open_position(sig, c, balance):
    stop_dist = c.atr * ATR_SL
    qty = (balance * RISK_PCT / 100) / stop_dist
    qty = min(qty, balance * MAX_LEVERAGE / c.close)
    entry = c.close
    sl = entry - stop_dist if sig == "long" else entry + stop_dist
    tp = entry + c.atr * ATR_TP if sig == "long" else entry - c.atr * ATR_TP
    return dict(side=sig, entry=entry, sl=sl, tp=tp, qty=qty)


def check_exit(pos, c):
    long_ = pos["side"] == "long"
    hit_sl = c.low <= pos["sl"] if long_ else c.high >= pos["sl"]
    hit_tp = c.high >= pos["tp"] if long_ else c.low <= pos["tp"]
    if hit_sl:
        return pos["sl"], "SL"
    if hit_tp:
        return pos["tp"], "TP"
    return None


def trade_pnl(pos, exit_px):
    sign = 1 if pos["side"] == "long" else -1
    gross = (exit_px - pos["entry"]) * pos["qty"] * sign
    fees = (pos["entry"] + exit_px) * pos["qty"] * FEE_PCT / 100
    return gross - fees, gross, fees


def wanted(sig):
    return sig and (sig == "long" or ALLOW_SHORTS)


def make_exchange():
    ex = getattr(ccxt, EXCHANGE)({"apiKey": API_KEY, "secret": API_SECRET, "enableRateLimit": True})
    ex.load_markets()
    return ex


def fetch_candles(ex, limit=300):
    raw = ex.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.iloc[:-1].reset_index(drop=True)


def sleep_to_next_candle():
    now = time.time()
    nxt = (now // PERIOD + 1) * PERIOD + 5
    time.sleep(max(1, nxt - now))


def main():
    log.info("Bot version 3 (with settings test)")
    if os.getenv("RUN_OPTIMIZE", "false").lower() == "true":
        try:
            from optimize import run_optimize
            run_optimize()
        except Exception as e:
            log.exception("Settings test failed: %s", e)
    ex = make_exchange()
    balance = PAPER_BALANCE
    pos = None
    log.info("Starting %s %s %s | DRY_RUN=%s", EXCHANGE, SYMBOL, TIMEFRAME, DRY_RUN)

    while True:
        try:
            df = add_indicators(fetch_candles(ex))
            c = df.iloc[-1]
            if pos:
                hit = check_exit(pos, c)
                if hit:
                    exit_px, reason = hit
                    pnl, gross, fees = trade_pnl(pos, exit_px)
                    balance += pnl
                    log.info("EXIT %s @ %.2f (%s) gross %.2f fees %.2f NET %.2f | balance %.2f",
                             pos["side"], exit_px, reason, gross, fees, pnl, balance)
                    if not DRY_RUN:
                        ex.create_market_order(SYMBOL, "sell" if pos["side"] == "long" else "buy",
                                               pos["qty"], params={"reduceOnly": True})
                    pos = None
                else:
                    unreal, _, _ = trade_pnl(pos, c.close)
                    log.info("IN TRADE %s | close %.2f | SL %.2f TP %.2f | unrealised net %.2f",
                             pos["side"], c.close, pos["sl"], pos["tp"], unreal)
            if not pos:
                sig = signal(df)
                if wanted(sig):
                    pos = open_position(sig, c, balance)
                    pos["qty"] = float(ex.amount_to_precision(SYMBOL, pos["qty"]))
                    log.info("ENTRY %s qty %.6f ($%.0f) @ %.2f SL %.2f TP %.2f",
                             sig, pos["qty"], pos["qty"] * pos["entry"],
                             pos["entry"], pos["sl"], pos["tp"])
                    if not DRY_RUN:
                        ex.create_market_order(SYMBOL, "buy" if sig == "long" else "sell", pos["qty"])
                else:
                    log.info("No signal | close %.2f RSI %.1f | balance %.2f", c.close, c.rsi, balance)
        except Exception as e:
            log.exception("Loop error: %s", e)
        sleep_to_next_candle()


if __name__ == "__main__":
    main()
