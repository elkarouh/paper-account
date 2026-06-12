"""Order matching engine: validates, fills against the current quote, books trades."""
from flask import current_app

from .db import get_db, now

OPEN_STATUSES = ("Submitted", "PreSubmitted")


def _get_position(db, account_id, conid):
    row = db.execute(
        "SELECT * FROM positions WHERE account_id = ? AND conid = ?",
        (account_id, conid),
    ).fetchone()
    return row


def place_order(account_id, conid, side, order_type, quantity, limit_price=None,
                stop_price=None, tif="DAY", coid=None):
    """Insert the order and attempt an immediate fill. Returns the order row."""
    db = get_db()
    side = side.upper()
    order_type = order_type.upper()
    ts = now()
    error = _validate(db, account_id, conid, side, order_type, quantity, limit_price)
    status = "Rejected" if error else "Submitted"
    cur = db.execute(
        "INSERT INTO orders (account_id, conid, side, order_type, quantity, limit_price,"
        " stop_price, tif, status, reject_reason, coid, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, conid, side, order_type, quantity, limit_price, stop_price,
         tif, status, error, coid, ts, ts),
    )
    db.commit()
    order_id = cur.lastrowid
    if not error:
        try_fill_open_orders(conid)
    return db.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()


def _validate(db, account_id, conid, side, order_type, quantity, limit_price):
    if side not in ("BUY", "SELL"):
        return f"Unsupported side {side!r}"
    if order_type not in ("MKT", "LMT", "STP"):
        return f"Unsupported order type {order_type!r}"
    if quantity is None or quantity <= 0:
        return "Quantity must be positive"
    if order_type == "LMT" and (limit_price is None or limit_price <= 0):
        return "Limit order requires a positive price"
    if side == "SELL" and not current_app.config["ALLOW_SHORT"]:
        pos = _get_position(db, account_id, conid)
        held = pos["quantity"] if pos else 0
        open_sells = db.execute(
            "SELECT COALESCE(SUM(quantity - filled_qty), 0) AS q FROM orders "
            "WHERE account_id=? AND conid=? AND side='SELL' AND status IN (?, ?)",
            (account_id, conid, *OPEN_STATUSES),
        ).fetchone()["q"]
        if quantity + open_sells > held + 1e-9:
            return "Insufficient position to sell (shorting disabled)"
    return None


def cancel_order(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    if order is None or order["status"] not in OPEN_STATUSES:
        return None
    db.execute(
        "UPDATE orders SET status='Cancelled', updated_at=? WHERE order_id=?",
        (now(), order_id),
    )
    db.commit()
    return db.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()


def try_fill_open_orders(conid):
    """Match every resting order on this contract against the latest quote."""
    from .marketdata import get_quote
    db = get_db()
    quote = get_quote(conid, refresh=False)
    if quote is None or quote["last"] is None:
        # No price yet — try to pull one (manual or yfinance).
        quote = get_quote(conid, refresh=True)
        if quote is None or quote["last"] is None:
            return
    orders = db.execute(
        "SELECT * FROM orders WHERE conid=? AND status IN (?, ?) ORDER BY order_id",
        (conid, *OPEN_STATUSES),
    ).fetchall()
    for order in orders:
        price = _fill_price(order, quote)
        if price is not None:
            _execute_fill(db, order, price)


def _fill_price(order, quote):
    """Price the order fills at against this quote, or None if it doesn't fill."""
    last, bid, ask = quote["last"], quote["bid"] or quote["last"], quote["ask"] or quote["last"]
    side, otype = order["side"], order["order_type"]
    if otype == "MKT":
        return ask if side == "BUY" else bid
    if otype == "LMT":
        limit = order["limit_price"]
        if side == "BUY" and ask <= limit:
            return min(ask, limit)
        if side == "SELL" and bid >= limit:
            return max(bid, limit)
        return None
    if otype == "STP":
        stop = order["stop_price"]
        if side == "BUY" and last >= stop:
            return ask
        if side == "SELL" and last <= stop:
            return bid
    return None


def _execute_fill(db, order, price):
    account_id, conid = order["account_id"], order["conid"]
    qty = order["quantity"] - order["filled_qty"]
    commission = current_app.config["COMMISSION_PER_ORDER"]
    signed = qty if order["side"] == "BUY" else -qty
    ts = now()

    account = db.execute(
        "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()
    cost = signed * price + commission
    if order["side"] == "BUY" and cost > account["cash"]:
        db.execute(
            "UPDATE orders SET status='Rejected', reject_reason='Insufficient funds',"
            " updated_at=? WHERE order_id=?",
            (ts, order["order_id"]),
        )
        db.commit()
        return

    pos = _get_position(db, account_id, conid)
    old_qty = pos["quantity"] if pos else 0.0
    old_avg = pos["avg_cost"] if pos else 0.0
    realized = pos["realized_pnl"] if pos else 0.0

    if old_qty * signed >= 0:
        # Adding to (or opening) a position: blend average cost.
        new_qty = old_qty + signed
        new_avg = (abs(old_qty) * old_avg + abs(signed) * price) / abs(new_qty) if new_qty else 0.0
    else:
        # Reducing / closing / flipping: realize PnL on the closed portion.
        closed = min(abs(signed), abs(old_qty))
        direction = 1 if old_qty > 0 else -1
        realized += closed * (price - old_avg) * direction
        new_qty = old_qty + signed
        new_avg = old_avg if new_qty * old_qty > 0 else (price if new_qty else 0.0)

    db.execute(
        "INSERT INTO positions (account_id, conid, quantity, avg_cost, realized_pnl)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(account_id, conid) DO UPDATE SET quantity=excluded.quantity,"
        " avg_cost=excluded.avg_cost, realized_pnl=excluded.realized_pnl",
        (account_id, conid, new_qty, new_avg, realized),
    )
    db.execute(
        "UPDATE accounts SET cash = cash - ? WHERE account_id = ?", (cost, account_id)
    )
    db.execute(
        "INSERT INTO trades (order_id, account_id, conid, side, quantity, price,"
        " commission, executed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (order["order_id"], account_id, conid, order["side"], qty, price, commission, ts),
    )
    db.execute(
        "UPDATE orders SET status='Filled', filled_qty=?, avg_fill_price=?, updated_at=?"
        " WHERE order_id=?",
        (order["quantity"], price, ts, order["order_id"]),
    )
    db.commit()
