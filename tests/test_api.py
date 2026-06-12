"""End-to-end test of the IBKR-compatible API against a temp database."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from paperbroker import create_app  # noqa: E402


class ApiTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        app = create_app({"DATABASE": self.db_path, "TESTING": True,
                          "STARTING_CASH": 100000.0, "COMMISSION_PER_ORDER": 1.0,
                          "SYNTHETIC_SPREAD": 0.0, "USE_MYBOT": False})
        self.client = app.test_client()
        self.acct = app.config["ACCOUNT_ID"]

    def tearDown(self):
        os.unlink(self.db_path)

    def set_price(self, symbol, last):
        res = self.client.post("/paper/price", json={"symbol": symbol, "last": last})
        self.assertEqual(res.status_code, 200)
        return res.get_json()["conid"]

    def place(self, **order):
        res = self.client.post(f"/v1/api/iserver/account/{self.acct}/orders",
                               json={"orders": [order]})
        self.assertEqual(res.status_code, 200)
        return res.get_json()[0]

    def test_auth_and_accounts(self):
        self.assertTrue(self.client.get("/v1/api/iserver/auth/status")
                        .get_json()["authenticated"])
        self.assertEqual(self.client.get("/v1/api/iserver/accounts")
                         .get_json()["selectedAccount"], self.acct)

    def test_market_order_fills_and_updates_position(self):
        conid = self.set_price("AAPL", 200.0)
        res = self.place(conid=conid, side="BUY", orderType="MKT", quantity=10)
        self.assertEqual(res["order_status"], "Filled")

        positions = self.client.get(
            f"/v1/api/portfolio/{self.acct}/positions/0").get_json()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["position"], 10)
        self.assertAlmostEqual(positions[0]["avgCost"], 200.0)

        summary = self.client.get(
            f"/v1/api/portfolio/{self.acct}/summary").get_json()
        # 100000 - 10*200 - 1 commission
        self.assertAlmostEqual(summary["totalcashvalue"]["amount"], 97999.0)
        self.assertAlmostEqual(summary["netliquidation"]["amount"], 99999.0)

    def test_limit_order_rests_then_fills_on_price_move(self):
        conid = self.set_price("MSFT", 400.0)
        res = self.place(conid=conid, side="BUY", orderType="LMT",
                         quantity=5, price=390.0)
        self.assertEqual(res["order_status"], "Submitted")

        self.set_price("MSFT", 389.0)
        orders = self.client.get("/v1/api/iserver/account/orders").get_json()["orders"]
        self.assertEqual(orders[0]["status"], "Filled")
        self.assertLessEqual(orders[0]["avgPrice"], 390.0)

    def test_cancel_order(self):
        conid = self.set_price("TSLA", 300.0)
        res = self.place(conid=conid, side="BUY", orderType="LMT",
                         quantity=1, price=100.0)
        order_id = res["order_id"]
        cancel = self.client.delete(
            f"/v1/api/iserver/account/{self.acct}/order/{order_id}")
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.get_json()["order_status"], "Cancelled")

    def test_sell_without_position_rejected(self):
        conid = self.set_price("NVDA", 100.0)
        res = self.place(conid=conid, side="SELL", orderType="MKT", quantity=5)
        self.assertEqual(res["order_status"], "Rejected")

    def test_round_trip_realizes_pnl(self):
        conid = self.set_price("AMD", 100.0)
        self.place(conid=conid, side="BUY", orderType="MKT", quantity=10)
        self.set_price("AMD", 110.0)
        self.place(conid=conid, side="SELL", orderType="MKT", quantity=10)

        trades = self.client.get("/v1/api/iserver/account/trades").get_json()
        self.assertEqual(len(trades), 2)
        summary = self.client.get(
            f"/v1/api/portfolio/{self.acct}/summary").get_json()
        # +100 gross PnL, -2 commissions
        self.assertAlmostEqual(summary["totalcashvalue"]["amount"], 100098.0)

    def test_order_by_ticker_and_insufficient_funds(self):
        self.set_price("GOOG", 150.0)
        res = self.place(ticker="GOOG", side="BUY", orderType="MKT",
                         quantity=1000000)
        self.assertEqual(res["order_status"], "Rejected")

    def test_snapshot_and_history(self):
        conid = self.set_price("SPY", 500.0)
        snap = self.client.get(
            f"/v1/api/iserver/marketdata/snapshot?conids={conid}&fields=31,84,86,55"
        ).get_json()
        self.assertEqual(snap[0]["31"], "500.0")
        self.assertEqual(snap[0]["55"], "SPY")
        hist = self.client.get(
            f"/v1/api/iserver/marketdata/history?conid={conid}&bar=1min").get_json()
        self.assertGreaterEqual(hist["points"], 1)


if __name__ == "__main__":
    unittest.main()
