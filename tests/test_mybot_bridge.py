"""Tests for the my_bot trading_utils bridge, using a stubbed module (no network)."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from paperbroker import create_app, mybot_data  # noqa: E402


class FakeDailyDF:
    """Mimics the DataFrame contract daily_bars relies on: len() + iterrows()."""

    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def iterrows(self):
        return iter(self.rows)


class FakeTradingUtils:
    def get_real_time_price(self, ticker):
        return 123.45

    def get_stock_prices(self, ticker, *, start_date=None, end_date=None,
                         use_cache=True, save_to_file=False):
        bar = {"Open": 100.0, "High": 110.0, "Low": 99.0,
               "Close": 105.0, "Volume": 1000.0}
        return FakeDailyDF([
            (datetime(2024, 1, 2, tzinfo=timezone.utc), bar),
            (datetime(2024, 1, 3, tzinfo=timezone.utc), bar),
        ])


class MybotBridgeTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        app = create_app({"DATABASE": self.db_path, "TESTING": True,
                          "STARTING_CASH": 100000.0, "COMMISSION_PER_ORDER": 1.0,
                          "SYNTHETIC_SPREAD": 0.0, "USE_MYBOT": True,
                          "PRICE_TTL": 0.0})
        self.client = app.test_client()
        mybot_data._tu = FakeTradingUtils()
        mybot_data._tried = True

    def tearDown(self):
        mybot_data._tu = None
        mybot_data._tried = False
        os.unlink(self.db_path)

    def conid_for(self, symbol):
        res = self.client.get(f"/v1/api/iserver/secdef/search?symbol={symbol}")
        return int(res.get_json()[0]["conid"])

    def test_stale_quote_refreshes_from_mybot(self):
        conid = self.conid_for("AAPL")
        # PRICE_TTL=0 → no price yet, snapshot must pull from the bridge.
        snap = self.client.get(
            f"/v1/api/iserver/marketdata/snapshot?conids={conid}&fields=31"
        ).get_json()
        self.assertEqual(snap[0]["31"], "123.45")

    def test_daily_history_served_from_mybot(self):
        conid = self.conid_for("MSFT")
        hist = self.client.get(
            f"/v1/api/iserver/marketdata/history?conid={conid}&bar=1d&period=1m"
        ).get_json()
        self.assertEqual(hist["points"], 2)
        self.assertEqual(hist["symbol"], "MSFT")
        self.assertEqual(hist["data"][0]["o"], 100.0)
        self.assertEqual(hist["data"][0]["c"], 105.0)
        self.assertEqual(hist["barLength"], 86400)

    def test_intraday_history_still_uses_tick_tape(self):
        res = self.client.post("/paper/price", json={"symbol": "TSLA", "last": 300.0})
        conid = res.get_json()["conid"]
        hist = self.client.get(
            f"/v1/api/iserver/marketdata/history?conid={conid}&bar=1min"
        ).get_json()
        self.assertGreaterEqual(hist["points"], 1)
        self.assertEqual(hist["data"][0]["c"], 300.0)

    def test_disabled_bridge_falls_back(self):
        # With USE_MYBOT off, daily history comes from the tick tape.
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            app = create_app({"DATABASE": db_path, "TESTING": True,
                              "STARTING_CASH": 100000.0,
                              "COMMISSION_PER_ORDER": 1.0,
                              "SYNTHETIC_SPREAD": 0.0, "USE_MYBOT": False})
            client = app.test_client()
            res = client.post("/paper/price", json={"symbol": "NVDA", "last": 100.0})
            conid = res.get_json()["conid"]
            hist = client.get(
                f"/v1/api/iserver/marketdata/history?conid={conid}&bar=1d"
            ).get_json()
            self.assertEqual(hist["points"], 1)
            self.assertEqual(hist["data"][0]["c"], 100.0)
        finally:
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
