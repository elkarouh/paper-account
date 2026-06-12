"""Price source: manual overrides first, then yfinance (delayed quotes) if available."""
import time

from flask import current_app

from .db import get_db, now

try:
    import yfinance
except ImportError:  # the app runs fine on manual prices alone
    yfinance = None


def get_contract(conid):
    return get_db().execute(
        "SELECT * FROM contracts WHERE conid = ?", (conid,)
    ).fetchone()


def find_or_create_contract(symbol, sec_type="STK", currency="USD", exchange="SMART"):
    symbol = symbol.upper().strip()
    db = get_db()
    row = db.execute("SELECT * FROM contracts WHERE symbol = ?", (symbol,)).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO contracts (symbol, sec_type, currency, exchange) VALUES (?, ?, ?, ?)",
            (symbol, sec_type, currency, exchange),
        )
        db.commit()
        row = db.execute("SELECT * FROM contracts WHERE symbol = ?", (symbol,)).fetchone()
    return row


def set_price(conid, last, bid=None, ask=None, source="manual"):
    db = get_db()
    spread = current_app.config["SYNTHETIC_SPREAD"]
    if bid is None:
        bid = round(last * (1 - spread), 4)
    if ask is None:
        ask = round(last * (1 + spread), 4)
    db.execute(
        "INSERT INTO prices (conid, last, bid, ask, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(conid) DO UPDATE SET last=excluded.last, bid=excluded.bid, "
        "ask=excluded.ask, source=excluded.source, updated_at=excluded.updated_at",
        (conid, last, bid, ask, source, now()),
    )
    db.execute(
        "INSERT INTO price_history (conid, price, ts) VALUES (?, ?, ?)",
        (conid, last, now()),
    )
    db.commit()
    # A fresh price may cross resting limit/stop orders.
    from .engine import try_fill_open_orders
    try_fill_open_orders(conid)


def _fetch_yfinance(symbol):
    if yfinance is None:
        return None
    try:
        tkr = yfinance.Ticker(symbol)
        info = tkr.fast_info
        last = info.get("last_price") or info.get("lastPrice")
        return float(last) if last else None
    except Exception:
        return None


def get_quote(conid, refresh=True):
    """Return the prices row for conid, refreshing from yfinance when stale.

    A manual price is treated as authoritative until it goes stale (TTL),
    so a bot can drive the tape deterministically in tests.
    """
    db = get_db()
    row = db.execute("SELECT * FROM prices WHERE conid = ?", (conid,)).fetchone()
    ttl = current_app.config["PRICE_TTL"]
    fresh = row is not None and row["updated_at"] and (time.time() - row["updated_at"]) < ttl
    if fresh or not refresh:
        return row
    contract = get_contract(conid)
    if contract is not None:
        last = _fetch_yfinance(contract["symbol"])
        if last is not None:
            set_price(conid, last, source="yfinance")
            row = db.execute("SELECT * FROM prices WHERE conid = ?", (conid,)).fetchone()
    return row
