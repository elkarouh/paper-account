"""REST endpoints mirroring the IBKR Client Portal Web API (/v1/api/...).

Only the subset a trading bot typically needs is implemented, but request and
response shapes follow the real gateway so client code can later be pointed at
https://localhost:5001/v1/api unchanged.
"""
import re
import time
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request

from . import engine, marketdata, mybot_data
from .db import get_db

bp = Blueprint("ibkr", __name__, url_prefix="/v1/api")

# ---------------------------------------------------------------- session ---

@bp.get("/iserver/auth/status")
@bp.post("/iserver/auth/status")
def auth_status():
    return jsonify({
        "authenticated": True,
        "competing": False,
        "connected": True,
        "message": "",
        "MAC": "paper",
        "serverInfo": {"serverName": "paper-account", "serverVersion": "sim"},
    })


@bp.post("/tickle")
def tickle():
    return jsonify({
        "session": "paper-session",
        "ssoExpires": 3600000,
        "iserver": {"authStatus": {"authenticated": True, "connected": True,
                                   "competing": False}},
    })


@bp.post("/logout")
def logout():
    return jsonify({"status": True})


@bp.post("/iserver/reauthenticate")
def reauthenticate():
    return jsonify({"message": "triggered"})


# --------------------------------------------------------------- accounts ---

def _account_id():
    return current_app.config["ACCOUNT_ID"]


@bp.get("/iserver/accounts")
def iserver_accounts():
    acct = _account_id()
    return jsonify({"accounts": [acct], "selectedAccount": acct,
                    "aliases": {acct: "Paper Trading"}})


@bp.get("/portfolio/accounts")
def portfolio_accounts():
    acct = get_db().execute(
        "SELECT * FROM accounts WHERE account_id = ?", (_account_id(),)
    ).fetchone()
    return jsonify([{
        "id": acct["account_id"], "accountId": acct["account_id"],
        "accountVan": acct["account_id"], "accountTitle": "Paper Trading",
        "displayName": acct["account_id"], "currency": acct["currency"],
        "type": "DEMO", "tradingType": "STKNOPT",
    }])


def _portfolio_value(db, account):
    value = 0.0
    rows = db.execute(
        "SELECT p.conid, p.quantity FROM positions p WHERE p.account_id=? AND p.quantity != 0",
        (account["account_id"],),
    ).fetchall()
    for row in rows:
        quote = marketdata.get_quote(row["conid"], refresh=False)
        if quote and quote["last"]:
            value += row["quantity"] * quote["last"]
    return value


@bp.get("/portfolio/<account_id>/summary")
def portfolio_summary(account_id):
    db = get_db()
    acct = db.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    if acct is None:
        return jsonify({"error": "account not found"}), 404
    market_value = _portfolio_value(db, acct)
    net = acct["cash"] + market_value

    def amt(v):
        return {"amount": round(v, 2), "currency": acct["currency"], "isNull": False,
                "timestamp": int(time.time() * 1000)}

    return jsonify({
        "availablefunds": amt(acct["cash"]),
        "buyingpower": amt(acct["cash"]),
        "totalcashvalue": amt(acct["cash"]),
        "netliquidation": amt(net),
        "grosspositionvalue": amt(market_value),
        "unrealizedpnl": amt(net - acct["starting_cash"]),
        "equitywithloanvalue": amt(net),
    })


@bp.get("/portfolio/<account_id>/ledger")
def portfolio_ledger(account_id):
    db = get_db()
    acct = db.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    if acct is None:
        return jsonify({"error": "account not found"}), 404
    market_value = _portfolio_value(db, acct)
    return jsonify({
        "USD": {
            "cashbalance": round(acct["cash"], 2),
            "settledcash": round(acct["cash"], 2),
            "stockmarketvalue": round(market_value, 2),
            "netliquidationvalue": round(acct["cash"] + market_value, 2),
            "currency": "USD",
            "key": "LedgerList",
            "timestamp": int(time.time()),
        }
    })


@bp.get("/portfolio/<account_id>/positions/<int:page_id>")
def positions(account_id, page_id):
    db = get_db()
    rows = db.execute(
        "SELECT p.*, c.symbol, c.sec_type, c.currency FROM positions p"
        " JOIN contracts c ON c.conid = p.conid"
        " WHERE p.account_id=? AND p.quantity != 0 ORDER BY c.symbol"
        " LIMIT 100 OFFSET ?",
        (account_id, page_id * 100),
    ).fetchall()
    out = []
    for row in rows:
        quote = marketdata.get_quote(row["conid"], refresh=False)
        last = quote["last"] if quote and quote["last"] else row["avg_cost"]
        mkt_value = row["quantity"] * last
        out.append({
            "acctId": account_id,
            "conid": row["conid"],
            "contractDesc": row["symbol"],
            "ticker": row["symbol"],
            "assetClass": row["sec_type"],
            "position": row["quantity"],
            "mktPrice": round(last, 4),
            "mktValue": round(mkt_value, 2),
            "avgCost": round(row["avg_cost"], 4),
            "avgPrice": round(row["avg_cost"], 4),
            "currency": row["currency"],
            "unrealizedPnl": round((last - row["avg_cost"]) * row["quantity"], 2),
            "realizedPnl": round(row["realized_pnl"], 2),
        })
    return jsonify(out)


# -------------------------------------------------------------- contracts ---

@bp.get("/iserver/secdef/search")
def secdef_search():
    symbol = request.args.get("symbol", "")
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    contract = marketdata.find_or_create_contract(symbol)
    return jsonify([{
        "conid": str(contract["conid"]),
        "symbol": contract["symbol"],
        "companyName": contract["symbol"],
        "description": contract["exchange"],
        "secType": contract["sec_type"],
        "currency": contract["currency"],
    }])


@bp.get("/trsrv/secdef")
def trsrv_secdef():
    conids = [int(c) for c in request.args.get("conids", "").split(",") if c.strip()]
    db = get_db()
    out = []
    for conid in conids:
        row = db.execute("SELECT * FROM contracts WHERE conid=?", (conid,)).fetchone()
        if row:
            out.append({"conid": row["conid"], "ticker": row["symbol"],
                        "symbol": row["symbol"], "secType": row["sec_type"],
                        "currency": row["currency"], "listingExchange": row["exchange"]})
    return jsonify({"secdef": out})


# ------------------------------------------------------------ market data ---

# Client Portal numeric field codes: 31=last, 84=bid, 86=ask, 55=symbol
SNAPSHOT_FIELDS = {"31": "last", "84": "bid", "86": "ask"}


@bp.get("/iserver/marketdata/snapshot")
def snapshot():
    conids = [int(c) for c in request.args.get("conids", "").split(",") if c.strip()]
    fields = [f for f in request.args.get("fields", "31,84,86").split(",") if f.strip()]
    db = get_db()
    out = []
    for conid in conids:
        quote = marketdata.get_quote(conid, refresh=True)
        entry = {"conid": conid, "_updated": int(time.time() * 1000)}
        contract = db.execute("SELECT symbol FROM contracts WHERE conid=?", (conid,)).fetchone()
        if "55" in fields and contract:
            entry["55"] = contract["symbol"]
        if quote:
            for code, col in SNAPSHOT_FIELDS.items():
                if code in fields and quote[col] is not None:
                    entry[code] = str(quote[col])
        out.append(entry)
    return jsonify(out)


def _period_days(period):
    """IBKR period string ("5d", "2w", "6m", "1y") → days, default 1y."""
    m = re.fullmatch(r"(\d+)([dwmy])", (period or "").strip().lower())
    if not m:
        return 365
    return int(m.group(1)) * {"d": 1, "w": 7, "m": 31, "y": 366}[m.group(2)]


@bp.get("/iserver/marketdata/history")
def history():
    conid = int(request.args.get("conid", 0))
    bar = request.args.get("bar", "1min")
    bar_secs = {"1min": 60, "5min": 300, "15min": 900, "1h": 3600, "1d": 86400}.get(bar, 60)
    if bar == "1d":
        # Real daily OHLCV via my_bot's cached Yahoo fetcher, when wired.
        contract = get_db().execute(
            "SELECT symbol FROM contracts WHERE conid=?", (conid,)
        ).fetchone()
        if contract is not None:
            start = (datetime.now() - timedelta(days=_period_days(request.args.get("period")))
                     ).strftime("%Y-%m-%d")
            data = mybot_data.daily_bars(contract["symbol"], start_date=start,
                                         end_date=datetime.now().strftime("%Y-%m-%d"))
            if data:
                return jsonify({"serverId": "paper", "symbol": contract["symbol"],
                                "data": data, "points": len(data),
                                "barLength": bar_secs, "mdAvailability": "D"})
    rows = get_db().execute(
        "SELECT price, ts FROM price_history WHERE conid=? ORDER BY ts", (conid,)
    ).fetchall()
    bars = {}
    for row in rows:
        bucket = int(row["ts"] // bar_secs) * bar_secs
        if bucket not in bars:
            bars[bucket] = {"t": bucket * 1000, "o": row["price"], "h": row["price"],
                            "l": row["price"], "c": row["price"], "v": 0}
        b = bars[bucket]
        b["h"] = max(b["h"], row["price"])
        b["l"] = min(b["l"], row["price"])
        b["c"] = row["price"]
    data = [bars[k] for k in sorted(bars)]
    return jsonify({"serverId": "paper", "symbol": str(conid), "data": data,
                    "points": len(data), "barLength": bar_secs})


# ------------------------------------------------------------------ orders ---

def _order_json(order, db):
    contract = db.execute(
        "SELECT symbol FROM contracts WHERE conid=?", (order["conid"],)
    ).fetchone()
    return {
        "orderId": order["order_id"],
        "order_id": str(order["order_id"]),
        "acct": order["account_id"],
        "account": order["account_id"],
        "conid": order["conid"],
        "ticker": contract["symbol"] if contract else None,
        "side": order["side"],
        "orderType": order["order_type"],
        "totalSize": order["quantity"],
        "filledQuantity": order["filled_qty"],
        "remainingQuantity": order["quantity"] - order["filled_qty"],
        "price": order["limit_price"],
        "stop_price": order["stop_price"],
        "avgPrice": order["avg_fill_price"],
        "tif": order["tif"],
        "status": order["status"],
        "order_status": order["status"],
        "order_ref": order["coid"],
        "rejectReason": order["reject_reason"],
        "lastExecutionTime_r": int(order["updated_at"] * 1000),
    }


@bp.get("/iserver/account/orders")
def list_orders():
    db = get_db()
    rows = db.execute("SELECT * FROM orders ORDER BY order_id DESC LIMIT 200").fetchall()
    return jsonify({"orders": [_order_json(r, db) for r in rows], "snapshot": True})


@bp.get("/iserver/account/order/status/<int:order_id>")
def order_status(order_id):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if order is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify(_order_json(order, db))


@bp.post("/iserver/account/<account_id>/orders")
def place_orders(account_id):
    payload = request.get_json(force=True, silent=True) or {}
    orders_in = payload.get("orders") or ([payload] if payload.get("conid") else [])
    if not orders_in:
        return jsonify({"error": "no orders in request"}), 400
    db = get_db()
    out = []
    for spec in orders_in:
        conid = spec.get("conid")
        if conid is None and spec.get("ticker"):
            conid = marketdata.find_or_create_contract(spec["ticker"])["conid"]
        if conid is None:
            return jsonify({"error": "order missing conid"}), 400
        order = engine.place_order(
            account_id=account_id,
            conid=int(conid),
            side=spec.get("side", "BUY"),
            order_type=spec.get("orderType", "MKT"),
            quantity=float(spec.get("quantity", 0)),
            limit_price=spec.get("price"),
            stop_price=spec.get("auxPrice") or spec.get("stop_price"),
            tif=spec.get("tif", "DAY"),
            coid=spec.get("cOID"),
        )
        resp = {"order_id": str(order["order_id"]),
                "order_status": order["status"],
                "local_order_id": order["coid"],
                "encrypt_message": "1"}
        if order["status"] == "Rejected":
            resp["text"] = order["reject_reason"]
        out.append(resp)
    return jsonify(out)


@bp.post("/iserver/reply/<reply_id>")
def order_reply(reply_id):
    # The real gateway asks confirmation questions; the simulator never does,
    # but bots that unconditionally confirm should still get a sane response.
    return jsonify([{"order_id": reply_id, "order_status": "Submitted"}])


@bp.delete("/iserver/account/<account_id>/order/<int:order_id>")
def cancel(account_id, order_id):
    order = engine.cancel_order(order_id)
    if order is None:
        return jsonify({"error": "order not found or not open",
                        "order_id": order_id}), 400
    return jsonify({"order_id": str(order_id), "msg": "Request was submitted",
                    "order_status": order["status"]})


@bp.get("/iserver/account/trades")
def trades():
    db = get_db()
    rows = db.execute(
        "SELECT t.*, c.symbol FROM trades t JOIN contracts c ON c.conid=t.conid"
        " ORDER BY t.trade_id DESC LIMIT 200"
    ).fetchall()
    return jsonify([{
        "execution_id": str(r["trade_id"]),
        "order_id": r["order_id"],
        "account": r["account_id"],
        "conid": r["conid"],
        "symbol": r["symbol"],
        "ticker": r["symbol"],
        "side": "B" if r["side"] == "BUY" else "S",
        "size": r["quantity"],
        "price": str(r["price"]),
        "commission": str(r["commission"]),
        "net_amount": round(r["quantity"] * r["price"], 2),
        "trade_time_r": int(r["executed_at"] * 1000),
    } for r in rows])


# ----------------------------------------------- paper-only admin helpers ---

paper_bp = Blueprint("paper", __name__, url_prefix="/paper")


@paper_bp.post("/price")
def set_manual_price():
    """Set a manual price: {"symbol": "AAPL", "last": 190.5} (or conid)."""
    payload = request.get_json(force=True, silent=True) or {}
    conid = payload.get("conid")
    if conid is None:
        symbol = payload.get("symbol")
        if not symbol:
            return jsonify({"error": "symbol or conid required"}), 400
        conid = marketdata.find_or_create_contract(symbol)["conid"]
    last = payload.get("last")
    if last is None:
        return jsonify({"error": "last price required"}), 400
    marketdata.set_price(int(conid), float(last),
                         bid=payload.get("bid"), ask=payload.get("ask"))
    quote = marketdata.get_quote(int(conid), refresh=False)
    return jsonify({"conid": conid, "last": quote["last"], "bid": quote["bid"],
                    "ask": quote["ask"]})


@paper_bp.post("/reset")
def reset_account():
    """Wipe positions/orders/trades and restore starting cash."""
    db = get_db()
    for table in ("positions", "orders", "trades", "price_history"):
        db.execute(f"DELETE FROM {table}")
    db.execute("UPDATE accounts SET cash = starting_cash")
    db.commit()
    return jsonify({"status": "reset"})
