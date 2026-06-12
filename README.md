# paper-account

A self-hosted paper-trading simulator with an **IBKR Client Portal Web API-compatible**
REST interface, a Python (Flask) backend, SQLite storage, and a web dashboard.

The point: develop and test a trading bot against this simulator at
`http://127.0.0.1:5000/v1/api`, then go live by pointing the *same* bot code at the
real [IBKR Client Portal Gateway](https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/)
at `https://localhost:5001/v1/api`. Request/response shapes follow the real gateway.

## Quick start

```bash
pip install -r requirements.txt   # yfinance is optional (delayed real quotes)
python run.py                     # dashboard at http://127.0.0.1:5000
```

The dashboard shows account summary, positions, orders and trades, and lets you
place orders, set manual prices, and reset the account.

> GitHub Pages can't run Python, so the app runs locally (or on any small VPS).
> This repo is just the code host.

## my_bot integration (trading_utils.py)

When a [my_bot](https://github.com/elkarouh/my_bot) checkout is reachable, the
simulator sources market data through its `LIBS/trading_utils.py` instead of
raw yfinance — same fetcher, same `CACHE/<TICKER>_cache.*` files the backtests
use, so the bot and the simulator always see identical data:

- **Quotes**: stale prices refresh via `get_real_time_price` (price row
  `source` becomes `my_bot`).
- **Daily history**: `GET /v1/api/iserver/marketdata/history?conid=&bar=1d&period=1y`
  returns real daily OHLCV from `get_stock_prices` (cache-merged, market-open
  guard included) instead of bars rebuilt from the simulator's tick tape.
  Intraday bars (`1min` … `1h`) still come from the tick tape.

Discovery: `PAPER_MYBOT_PATH` if set, otherwise a sibling `../my_bot` checkout.
Disable with `PAPER_USE_MYBOT=0`. Requires my_bot's data deps (`pandas`,
`pytz`, `requests`) on the same Python; without them the simulator silently
falls back to yfinance / manual prices.

## Hooking up your bot

Use `client/paper_ibkr.py` (stdlib-only) from your bot, or call the REST API
directly from your existing `trading_utils.py`:

```python
from paper_ibkr import IBKRClient

ib = IBKRClient("http://127.0.0.1:5000/v1/api")        # paper
# ib = IBKRClient("https://localhost:5001/v1/api")     # live, via CP Gateway

conid = ib.conid_for("AAPL")
print(ib.last_price(conid))
ib.place_order(conid, side="BUY", quantity=10, order_type="LMT", price=185.0)
print(ib.positions())
print(ib.trades())
```

## Implemented endpoints (Client Portal API subset)

| Endpoint | Purpose |
|---|---|
| `GET/POST /v1/api/iserver/auth/status`, `POST /v1/api/tickle` | session (always authenticated here) |
| `GET /v1/api/iserver/accounts`, `GET /v1/api/portfolio/accounts` | account discovery |
| `GET /v1/api/portfolio/{acct}/summary` / `ledger` / `positions/{page}` | balances & positions |
| `GET /v1/api/iserver/secdef/search?symbol=` | symbol → conid (auto-creates contracts) |
| `GET /v1/api/trsrv/secdef?conids=` | conid → contract details |
| `GET /v1/api/iserver/marketdata/snapshot?conids=&fields=31,84,86,55` | quotes (31=last, 84=bid, 86=ask) |
| `GET /v1/api/iserver/marketdata/history?conid=&bar=` | OHLC bars built from the tick history |
| `POST /v1/api/iserver/account/{acct}/orders` | place orders (MKT / LMT / STP, `cOID` supported) |
| `POST /v1/api/iserver/reply/{id}` | order confirmations (no-op here, needed live) |
| `GET /v1/api/iserver/account/orders`, `.../order/status/{id}` | order status |
| `DELETE /v1/api/iserver/account/{acct}/order/{id}` | cancel |
| `GET /v1/api/iserver/account/trades` | executions |

Simulator-only helpers (no IBKR equivalent):

| Endpoint | Purpose |
|---|---|
| `POST /paper/price` `{"symbol":"AAPL","last":190.5}` | set a manual price (drives fills deterministically) |
| `POST /paper/reset` | wipe positions/orders/trades, restore starting cash |

## How fills work

- Prices come from manual overrides first; when a price is stale (default 15 s)
  a delayed real quote is fetched — via my_bot's `trading_utils` when wired,
  otherwise via `yfinance` if installed. Bid/ask are synthesized around last
  with a configurable spread.
- `MKT` orders fill immediately at ask (buy) / bid (sell).
- `LMT` / `STP` orders rest and are matched on every price update.
- Buys are rejected on insufficient cash; sells beyond your position are
  rejected unless shorting is enabled.
- A flat per-order commission is charged (default $1, IBKR-ish minimum).

## Configuration (environment variables)

| Variable | Default | |
|---|---|---|
| `PAPER_DB` | `paper.db` | SQLite file |
| `PAPER_ACCOUNT_ID` | `DU0000001` | account id (IBKR paper accounts start with `DU`) |
| `PAPER_STARTING_CASH` | `100000` | |
| `PAPER_COMMISSION` | `1.0` | per filled order |
| `PAPER_SPREAD` | `0.0005` | synthetic half-spread around last |
| `PAPER_PRICE_TTL` | `15` | seconds before a cached price is refreshed |
| `PAPER_ALLOW_SHORT` | `0` | set `1` to allow short selling |
| `PAPER_USE_MYBOT` | `1` | set `0` to disable the my_bot trading_utils bridge |
| `PAPER_MYBOT_PATH` | *(auto)* | my_bot checkout; defaults to sibling `../my_bot` |
| `PAPER_MYBOT_CACHE` | `<my_bot>/CACHE` | override the shared price-cache directory |

## Tests

```bash
python -m unittest discover -s tests
```

## Going live later (checklist)

1. Run IBKR's Client Portal Gateway and log in with your (paper, then live) IBKR account.
2. Change the bot's base URL to `https://localhost:5001/v1/api` (self-signed TLS —
   `IBKRClient` already handles that).
3. Real differences to handle: session auth can expire (poll `/tickle`,
   re-authenticate), order placement may return confirmation prompts (reply via
   `/iserver/reply/{id}` — the client does this), market data requires
   subscriptions, and conids are IBKR's real ones (always resolve via
   `/iserver/secdef/search`, never hardcode the simulator's).
