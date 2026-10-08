"""
Strategy settings test on ~6 months of real price history, fees included.
Uses the coin in the SYMBOL variable (e.g. TAO/USD).
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

DAYS = int(os.getenv("OPT_DAYS", "180"))
MIN_DAYS = 60
BASE = os.getenv("SYMBOL", "BTC/USD").split("/")[0]
SOURCES = [("coinbaseexchange", f"{BASE}/USD"), ("kucoin", f"{BASE}/USDT"),
           ("okx", f"{BASE}/USDT"), ("gateio", f"{BASE}/USDT"),
           ("bitstamp", f"{BASE}/USD"), ("kraken", f"{BASE}/USD")]


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
                batch = ex.fetch_ohlcv(sym, "15m", since=since, limit=300)
                if not batch or batch[-1][0] < since:
                    break
                rows += batch
                since = batch[-1][0] + 900_000
                if since >= ex.milliseconds() - 900_000:
                    break
            if len(rows) >= MIN_DAYS * 96:
                df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
                df = df.drop_duplicates("ts").sort_values("ts")
                df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
                log.info("OPT data: %s, %d x 15m candles from %s (%s to %s)",
                         sym, len(df), ex_id, df.index[0].date(), df.index[-1].date())
                return df
            log.info("OPT %s returned only %d days, trying next source", ex_id, len(rows) // 96)
        except Exception as e:
            log.info("OPT %s failed: %s", ex_id, e)
    raise RuntimeError("No exchange returned enough history")


def resample(df, rule):
    if rule == "15m":
        return df[["open", "high", "low", "close", "volume"]].copy()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(rule.replace("m", "min")).agg(agg).dropna()


def simulate(df, atr_sl, atr_tp, fee_pct, allow_shorts=True, risk_pct=1.0, start=1000.0):
    d = add_indicators(df.copy())
    c, h, l = d.close.values, d.high.values, d.low.values
    ef, es, et, rsi, atr = (d.ema_fast.values, d.ema_slow.values, d.ema_trend.values,
                            d.rsi.values, d.atr.values)
    up = (c > et) & (ef > es)
    dn = (c < et) & (ef < es)
    prsi = np.roll(rsi, 1)
    long_sig = up & (prsi < 45) & (rsi >= 45)
    short_sig = dn & (prsi > 55) & (rsi <= 55) & allow_shorts

    bal, peak, max_dd = start, start, 0.0
    pos = None
    pnls, half_bal = [], None
    half = len(c) // 2
    for i in range(200, len(c)):
        if i == half:
            half_bal = bal
        if pos:
            side, entry, sl, tp, qty = pos
            if side == 1:
                hit = sl if l[i] <= sl else (tp if h[i] >= tp else None)
            else:
                hit = sl if h[i] >= sl else (tp if l[i] <= tp else None)
            if hit is not None:
                net = (hit - entry) * qty * side - (entry + hit) * qty * fee_pct / 100
                bal += net
                pnls.append(net)
                peak = max(peak, bal)
                max_dd = max(max_dd, (peak - bal) / peak)
                pos = None
        if not pos and (long_sig[i] or short_sig[i]):
            side = 1 if long_sig[i] else -1
            dist = atr[i] * atr_sl
            qty = min(bal * risk_pct / 100 / dist, bal / c[i])
            pos = (side, c[i], c[i] - side * dist, c[i] + side * atr[i] * atr_tp, qty)
    half_bal = half_bal or bal
    wins = sum(p > 0 for p in pnls)
    return dict(trades=len(pnls), win=wins / max(1, len(pnls)),
                ret=(bal / start - 1) * 100,
                h1=(half_bal / start - 1) * 100,
                h2=(bal / half_bal - 1) * 100,
                dd=max_dd * 100)


def run_optimize():
    log.info("OPT ===== settings test starting =====")
    raw = fetch_history()
    results = []
    for tf in ("15m", "1h", "4h"):
        df = resample(raw, tf)
        for sl, rr, fee, shorts in itertools.product(
                (1.5, 2.0, 3.0), (2, 3), (0.4, 0.25), (True, False)):
            r = simulate(df, sl, sl * rr, fee, shorts)
            r.update(tf=tf, sl=sl, tp=sl * rr, fee=fee, shorts=shorts)
            results.append(r)
    results.sort(key=lambda r: r["ret"], reverse=True)

    log.info("OPT %-4s %-4s %-4s %-5s %-6s | %6s %5s | %7s %7s %7s | %6s",
             "TF", "SL", "TP", "fee", "shorts", "trades", "win", "return", "1st", "2nd", "maxDD")
    current = [r for r in results if r["tf"] == "15m" and r["sl"] == 1.5 and r["tp"] == 3.0
               and r["fee"] == 0.4 and r["shorts"]]
    for r in results[:15] + [None] + current:
        if r is None:
            log.info("OPT ----- your current settings (rank %d of %d): -----",
                     results.index(current[0]) + 1, len(results))
            continue
        log.info("OPT %-4s %-4.1f %-4.1f %-5.2f %-6s | %6d %4.0f%% | %+6.1f%% %+6.1f%% %+6.1f%% | %5.1f%%",
                 r["tf"], r["sl"], r["tp"], r["fee"], "yes" if r["shorts"] else "no",
                 r["trades"], r["win"] * 100, r["ret"], r["h1"], r["h2"], r["dd"])
    log.info("OPT ===== done. Best rows are at the top. =====")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run_optimize()
