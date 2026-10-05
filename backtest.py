"""Quick backtest of the M15 strategy on recent candles. Run: python backtest.py"""
import ccxt
import pandas as pd
from bot import add_indicators, signal, EXCHANGE, SYMBOL, ATR_SL, ATR_TP, RISK_PCT


def run(df, balance=1000.0):
    df = add_indicators(df)
    pos, trades = None, []
    for i in range(200, len(df)):
        win = df.iloc[: i + 1]
        c = win.iloc[-1]
        if pos:
            long_ = pos["side"] == "long"
            hit_sl = c.low <= pos["sl"] if long_ else c.high >= pos["sl"]
            hit_tp = c.high >= pos["tp"] if long_ else c.low <= pos["tp"]
            if hit_sl or hit_tp:
                px = pos["sl"] if hit_sl else pos["tp"]  # assume SL first if both
                pnl = (px - pos["entry"]) * pos["qty"] * (1 if long_ else -1)
                balance += pnl
                trades.append(pnl)
                pos = None
        if not pos:
            sig = signal(win)
            if sig:
                d = c.atr * ATR_SL
                qty = balance * RISK_PCT / 100 / d
                sl = c.close - d if sig == "long" else c.close + d
                tp = c.close + c.atr * ATR_TP if sig == "long" else c.close - c.atr * ATR_TP
                pos = dict(side=sig, entry=c.close, sl=sl, tp=tp, qty=qty)
    wins = sum(t > 0 for t in trades)
    print(f"Trades: {len(trades)} | Win rate: {wins / max(1, len(trades)):.0%} | "
          f"Final balance: {balance:.2f}")
    return balance, trades


if __name__ == "__main__":
    ex = getattr(ccxt, EXCHANGE)()
    raw = ex.fetch_ohlcv(SYMBOL, "15m", limit=1000)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    run(df)
