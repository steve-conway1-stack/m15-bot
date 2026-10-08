"""
Trend-following test: breakout entries + ATR trailing stop, vs buy-and-hold.
Uses the coin in the SYMBOL variable (e.g. TAO/USD), fees included, no leverage.
Runs once at bot startup when the Railway variable RUN_OPTIMIZE=true.
"""
import os
import logging
import itertools

import numpy as np
import pandas as pd
import ccxt

from bot import add_indicators

log = logging.getLogger("m15")

DAYS = int(os.getenv("OPT_DAYS", "730"))
MIN_DAYS = 180
FEE = 0.4
BASE = os.getenv("SYMBOL", "BTC/USD").split("/")[0]
SOURCES = [("coinbaseexchange", f"{BASE}/USD"), ("kucoin", f"{BASE}/USDT"),
           ("okx", f"{BASE}/USDT"), ("gateio", f"{BASE}/USDT"),
           ("bitstamp", f"{BASE}/USD"), ("kraken", f"{BASE}/USD")]
HOUR = 3_600_000


def fetch_history(days=DAYS):
    for ex_id, sym in SOURCES:
        try:
            ex = getattr(ccxt, ex_id)({"enableRateLimit": True})
            ex.load_markets()
            if sym not in ex.markets:
                log.info("OPT %s doesn't list %s, trying next source", ex_id, sym)
                continue
            since = ex.milliseconds() - days * 86400 * 1000
            rows = []
            while True:
                batch = ex.fetch_ohlcv(sym, "1h", since=since, limit=300)
                if not batch:
                    if not rows and since < ex.milliseconds() - 300 * HOUR:
                        since += 300 * HOUR
                        continue
                    break
                if batch[-1][0] < since:
                    break
                rows += batch
                since = batch[-1][0] + HOUR
                if since >= ex.milliseconds() - HOUR:
                    break
            if len(rows) >= MIN_DAYS * 24:
                df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
                df = df.drop_duplicates("ts").sort_values("ts")
                df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
                log.info("OPT data: %s, %d days of 1h candles from %s (%s to %s)",
                         sym, len(df) // 24, ex_id, df.index[0].date(), df.index[-1].date())
                return df
            log.info("OPT %s returned only %d days, trying next source", ex_id, len(rows) // 24)
        except Exception as e:
            log.info("OPT %s failed: %s", ex_id, e)
    raise RuntimeError("No exchange returned enough history")


def resample(df, rule):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df[list(agg)].resample(rule).agg(agg).dropna()


START = 60


def simulate(df, lookback, atr_mult, shorts, fee_pct=FEE, start=1000.0):
    d = add_indicators(df.copy())
    o, h, l, c, atr = d.open.values, d.high.values, d.low.values, d.close.values, d.atr.values
    hi = pd.Series(h).rolling(lookback).max().shift(1).values
    lo = pd.Series(l).rolling(lookback).min().shift(1).values

    bal, peak, max_dd = start, start, 0.0
    pos = None
    pnls, half_bal = [], None
    half = START + (len(c) - START) // 2
    for i in range(START, len(c)):
        if pos:
            side, entry, qty, stop, best = pos
            exit_px = None
            if side == 1 and l[i] <= stop:
                exit_px = min(o[i], stop)
            elif side == -1 and h[i] >= stop:
                exit_px = max(o[i], stop)
            if exit_px is not None:
                net = (exit_px - entry) * qty * side - (entry + exit_px) * qty * fee_pct / 100
                bal += net
                pnls.append(net)
                pos = None
            else:
                if side == 1:
                    best = max(best, c[i]); stop = max(stop, best - atr_mult * atr[i])
                else:
                    best = min(best, c[i]); stop = min(stop, best + atr_mult * atr[i])
                pos = [side, entry, qty, stop, best]
        if not pos:
            side = 1 if c[i] > hi[i] else (-1 if (shorts and c[i] < lo[i]) else 0)
            if side:
                qty = bal / c[i] / (1 + fee_pct / 100)
                pos = [side, c[i], qty, c[i] - side * atr_mult * atr[i], c[i]]
        eq = bal + ((c[i] - pos[1]) * pos[2] * pos[0] if pos else 0)
        if i == half:
            half_bal = eq
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
    if pos:
        side, entry, qty = pos[0], pos[1], pos[2]
        net = (c[-1] - entry) * qty * side - (entry + c[-1]) * qty * fee_pct / 100
        bal += net
        pnls.append(net)
    half_bal = half_bal or bal
    wins = sum(p > 0 for p in pnls)
    return dict(trades=len(pnls), win=wins / max(1, len(pnls)),
                ret=(bal / start - 1) * 100, h1=(half_bal / start - 1) * 100,
                h2=(bal / half_bal - 1) * 100, dd=max_dd * 100)


def buy_and_hold(df, fee_pct=FEE):
    c = df.close.values[START:]
    half = len(c) // 2
    f = (1 - fee_pct / 100) ** 2
    dd = (1 - c / np.maximum.accumulate(c)).max() * 100
    return dict(ret=(c[-1] / c[0] * f - 1) * 100, h1=(c[half] / c[0] - 1) * 100,
                h2=(c[-1] / c[half] - 1) * 100, dd=dd)


ROW = "OPT %-3s %-4s %-4s %-6s | %6s %5s | %8s %8s %8s | %6s"


def run_optimize():
    log.info("OPT ===== trend-following test starting =====")
    raw = fetch_history()
    frames = {tf: resample(raw, rule) for tf, rule in (("1h", "1h"), ("4h", "4h"), ("1d", "1D"))}
    results = []
    for tf, lb, k, shorts in itertools.product(frames, (20, 55), (2.0, 3.0, 4.0), (False, True)):
        r = simulate(frames[tf], lb, k, shorts)
        r.update(tf=tf, lb=lb, k=k, shorts=shorts)
        results.append(r)
    results.sort(key=lambda r: r["ret"], reverse=True)

    log.info(ROW, "TF", "LOOK", "ATR", "shorts", "trades", "win", "return", "1st", "2nd", "maxDD")
    for r in results[:12]:
        log.info("OPT %-3s %-4d %-4.1f %-6s | %6d %4.0f%% | %+7.1f%% %+7.1f%% %+7.1f%% | %5.1f%%",
                 r["tf"], r["lb"], r["k"], "yes" if r["shorts"] else "no",
                 r["trades"], r["win"] * 100, r["ret"], r["h1"], r["h2"], r["dd"])
    bh = buy_and_hold(frames["1d"])
    log.info("OPT ----- buy and hold %s for comparison: -----", BASE)
    log.info("OPT %-20s |              | %+7.1f%% %+7.1f%% %+7.1f%% | %5.1f%%",
             "just hold", bh["ret"], bh["h1"], bh["h2"], bh["dd"])
    log.info("OPT ===== done. %d settings tested, best 12 shown. =====", len(results))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run_optimize()
