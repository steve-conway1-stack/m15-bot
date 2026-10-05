# M15 Trading Bot (Railway)

**Strategy (15-minute candles):**
- Trend filter: price above EMA200 and EMA20 > EMA50 → longs only (reverse for shorts)
- Entry: RSI pulls back below 45 then crosses back up (above 55 then down for shorts)
- Stop: 1.5 × ATR · Target: 3 × ATR (1:2 risk/reward) · Risk: 1% per trade
- Acts only on closed candles

## Deploy to Railway
1. Push this folder to a GitHub repo.
2. railway.app → New Project → Deploy from GitHub repo.
3. Variables tab — add:
   | Variable | Example |
   |---|---|
   | EXCHANGE | binance (or bybit, kraken…) |
   | SYMBOL | BTC/USDT |
   | DRY_RUN | true |
   | RISK_PCT | 1.0 |
   | API_KEY / API_SECRET | only when going live |
4. Deploy, then watch the **Logs** tab — entries/exits are logged every 15 minutes.

## Before going live
- Run `python backtest.py` locally and paper trade (DRY_RUN=true) for a few weeks.
- Use API keys with **trading only — withdrawals disabled**.
- Binance blocks some US cloud IPs; set the Railway region to EU/Asia or use another exchange if you get 451 errors.
- Not financial advice — trading carries real risk of loss.
