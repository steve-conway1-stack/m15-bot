"""Backtest of the M15 strategy on recent candles, with fees and leverage cap.
Run: python backtest.py"""
import ccxt
import pandas as pd
from bot import (add_indicators, signal, open_position, check_exit, trade_pnl,
                 wanted, EXCHANGE, SYMBOL)
 
 
def run(df, balance=1000.0, verbose=True):
    df = add_indicators(df)
    pos, trades, fees_paid = None, [], 0.0
    for i in range(200, len(df)):
        win = df.iloc[: i + 1]
        c = win.iloc[-1]
        if pos:
            hit = check_exit(pos, c)
            if hit:
                pnl, _, fees = trade_pnl(pos, hit[0])
                balance += pnl
                fees_paid += fees
                trades.append(pnl)
                pos = None
        if not pos:
            sig = signal(win)
            if wanted(sig):
                pos = open_position(sig, c, balance)
    if verbose:
        wins = sum(t > 0 for t in trades)
        print(f"Trades: {len(trades)} | Win rate: {wins / max(1, len(trades)):.0%} | "
              f"Fees paid: {fees_paid:.2f} | Final balance: {balance:.2f}")
    return balance, trades
 
 
if __name__ == "__main__":
    ex = getattr(ccxt, EXCHANGE)()
    raw = ex.fetch_ohlcv(SYMBOL, "15m", limit=720)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    run(df)
