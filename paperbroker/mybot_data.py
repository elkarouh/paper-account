"""Optional bridge to my_bot's LIBS/trading_utils.py.

When a my_bot checkout is reachable (PAPER_MYBOT_PATH, or a sibling ./my_bot
directory), quotes come from its `get_real_time_price` and daily history from
its `get_stock_prices`, sharing its CACHE/<TICKER>_cache.* files so the bot
and the simulator see the same data. Without my_bot (or with PAPER_USE_MYBOT=0)
everything falls back to yfinance / manual prices exactly as before.
"""
import os
import sys

from flask import current_app

_tu = None
_tried = False


def _candidate_paths():
    configured = current_app.config.get("MYBOT_PATH")
    if configured:
        yield configured
    # sibling checkout: <parent>/paper-account and <parent>/my_bot
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yield os.path.join(os.path.dirname(repo_root), "my_bot")


def trading_utils():
    """Import LIBS.trading_utils from the my_bot checkout, once. None if unavailable."""
    global _tu, _tried
    if not current_app.config.get("USE_MYBOT", False):
        return None
    if _tried:
        return _tu
    _tried = True
    for path in _candidate_paths():
        if not os.path.isfile(os.path.join(path, "LIBS", "trading_utils.py")):
            continue
        if path not in sys.path:
            sys.path.insert(0, path)
        try:
            from LIBS import trading_utils as tu
        except Exception:
            return None
        # Share my_bot's price cache instead of growing a second one in our CWD.
        tu.CACHE_DIR = current_app.config.get("MYBOT_CACHE") or os.path.join(path, "CACHE")
        _tu = tu
        break
    return _tu


def last_price(symbol):
    """Real-time last price via my_bot, or None."""
    tu = trading_utils()
    if tu is None:
        return None
    try:
        price = tu.get_real_time_price(symbol)
        return float(price) if price else None
    except Exception:
        return None


def daily_bars(symbol, start_date, end_date=None):
    """Daily OHLCV bars via my_bot's cached fetcher, as IBKR history dicts, or None.

    Dates are "YYYY-MM-DD" strings (same contract as get_stock_prices).
    """
    tu = trading_utils()
    if tu is None:
        return None
    try:
        df = tu.get_stock_prices(symbol, start_date=start_date, end_date=end_date)
    except Exception:
        return None
    if df is None or len(df) == 0:
        return None
    return [
        {
            "t": int(ts.timestamp() * 1000),
            "o": float(row["Open"]),
            "h": float(row["High"]),
            "l": float(row["Low"]),
            "c": float(row["Close"]),
            "v": float(row["Volume"]),
        }
        for ts, row in df.iterrows()
    ]
