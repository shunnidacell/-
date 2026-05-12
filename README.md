# Crypto Arbitrage Bot

Public market-data crypto arbitrage scanner with a web dashboard, demo trading, and guarded live-mode preparation.

This is intentionally built as a safe first version: it does not place real orders. It compares best bid/ask prices across exchanges, subtracts estimated taker fees and slippage, and prints opportunities that clear your configured minimum net profit.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python .\arb_bot.py
```

## Web dashboard

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn web_app:app --reload --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

## Configuration

Edit `.env`:

- `EXCHANGES`: comma-separated ccxt exchange ids, for example `binance,okx,bitget`
- `SYMBOLS`: pairs to scan, for example `BTC/USDT,ETH/USDT,SOL/USDT`
- `TRADE_SIZE_QUOTE`: trade size used when auto-optimization is disabled
- `MAX_OPTIMIZED_TRADE_QUOTE`: upper bound for automatic trade-size optimization in the web dashboard
- `MIN_NET_PROFIT_PCT`: minimum net profit after fees and slippage
- `DEFAULT_TAKER_FEE_PCT`: fallback taker fee per side
- `SLIPPAGE_PCT`: estimated slippage per side
- `POLL_SECONDS`: scan interval
- `ORDERBOOK_LIMIT`: order book levels used for weighted executable prices
- `MODE`: `demo` or `live`

Live mode is locked unless all of these are true:

- `.env` has `LIVE_TRADING=true`
- the web form confirmation field is `I_UNDERSTAND_REAL_ORDERS`
- API key and secret are configured for every selected exchange
- `MAX_LIVE_TRADE_QUOTE` allows the requested quote size

Live execution assumes you already hold quote currency on the buy exchange and the base asset on the sell exchange. It sends simultaneous market buy/sell orders through ccxt and caps order size with `MAX_LIVE_TRADE_QUOTE`.

## Important

Real arbitrage is harder than the spread suggests. Before adding live orders, account for:

- withdrawal and transfer time if using cross-exchange inventory rebalancing
- maker/taker fee tiers
- partial fills
- order book depth
- API rate limits
- price movement during execution
- exchange outages, symbol differences, and stablecoin depegs

The next safe step is adding persistent logging and backtesting from captured order books.
