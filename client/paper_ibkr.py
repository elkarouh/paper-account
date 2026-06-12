"""Minimal client for the IBKR Client Portal Web API.

Works against this paper simulator (http://127.0.0.1:5000/v1/api) and against
the real IBKR Client Portal Gateway (https://localhost:5001/v1/api) — switch by
changing base_url. Designed to be imported from a trading bot, e.g.:

    from paper_ibkr import IBKRClient
    ib = IBKRClient("http://127.0.0.1:5000/v1/api")
    account = ib.account_id()
    conid = ib.conid_for("AAPL")
    ib.place_order(conid, side="BUY", quantity=10, order_type="MKT")
    print(ib.positions(), ib.trades())

Only the Python standard library is required.
"""
import json
import ssl
import urllib.request


class IBKRClient:
    def __init__(self, base_url="http://127.0.0.1:5000/v1/api", verify_ssl=False):
        self.base_url = base_url.rstrip("/")
        # The real CP Gateway serves a self-signed certificate on localhost.
        self._ssl_ctx = ssl.create_default_context()
        if not verify_ssl:
            self._ssl_ctx.check_hostname = False
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

    def _request(self, method, path, payload=None):
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, context=self._ssl_ctx) as resp:
            return json.loads(resp.read().decode() or "null")

    def get(self, path):
        return self._request("GET", path)

    def post(self, path, payload=None):
        return self._request("POST", path, payload or {})

    def delete(self, path):
        return self._request("DELETE", path)

    # ------------------------------------------------------------- session --

    def auth_status(self):
        return self.post("/iserver/auth/status")

    def tickle(self):
        return self.post("/tickle")

    # ------------------------------------------------------------- account --

    def account_id(self):
        return self.get("/iserver/accounts")["selectedAccount"]

    def summary(self, account=None):
        return self.get(f"/portfolio/{account or self.account_id()}/summary")

    def positions(self, account=None, page=0):
        return self.get(f"/portfolio/{account or self.account_id()}/positions/{page}")

    # ----------------------------------------------------------- contracts --

    def conid_for(self, symbol):
        results = self.get(f"/iserver/secdef/search?symbol={symbol}")
        return int(results[0]["conid"])

    def snapshot(self, conids, fields="31,84,86"):
        ids = ",".join(str(c) for c in conids)
        return self.get(f"/iserver/marketdata/snapshot?conids={ids}&fields={fields}")

    def last_price(self, conid):
        snap = self.snapshot([conid])[0]
        return float(snap["31"]) if "31" in snap else None

    # -------------------------------------------------------------- orders --

    def place_order(self, conid, side, quantity, order_type="MKT", price=None,
                    stop_price=None, tif="DAY", account=None, coid=None):
        order = {"conid": int(conid), "side": side, "quantity": quantity,
                 "orderType": order_type, "tif": tif}
        if price is not None:
            order["price"] = price
        if stop_price is not None:
            order["auxPrice"] = stop_price
        if coid is not None:
            order["cOID"] = coid
        result = self.post(
            f"/iserver/account/{account or self.account_id()}/orders",
            {"orders": [order]},
        )
        # The real gateway may answer with a confirmation question; reply yes.
        while isinstance(result, list) and result and "id" in result[0]:
            result = self.post(f"/iserver/reply/{result[0]['id']}",
                               {"confirmed": True})
        return result[0] if isinstance(result, list) else result

    def orders(self):
        return self.get("/iserver/account/orders")["orders"]

    def cancel_order(self, order_id, account=None):
        return self.delete(
            f"/iserver/account/{account or self.account_id()}/order/{order_id}")

    def trades(self):
        return self.get("/iserver/account/trades")
