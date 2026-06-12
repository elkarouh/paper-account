"""SQLite storage for the paper broker."""
import os
import sqlite3
import time

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id    TEXT PRIMARY KEY,
    currency      TEXT NOT NULL DEFAULT 'USD',
    starting_cash REAL NOT NULL,
    cash          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS contracts (
    conid    INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT NOT NULL UNIQUE,
    sec_type TEXT NOT NULL DEFAULT 'STK',
    currency TEXT NOT NULL DEFAULT 'USD',
    exchange TEXT NOT NULL DEFAULT 'SMART'
);

CREATE TABLE IF NOT EXISTS positions (
    account_id   TEXT NOT NULL,
    conid        INTEGER NOT NULL,
    quantity     REAL NOT NULL DEFAULT 0,
    avg_cost     REAL NOT NULL DEFAULT 0,
    realized_pnl REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, conid)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     TEXT NOT NULL,
    conid          INTEGER NOT NULL,
    side           TEXT NOT NULL,            -- BUY / SELL
    order_type     TEXT NOT NULL,            -- MKT / LMT / STP
    quantity       REAL NOT NULL,
    limit_price    REAL,
    stop_price     REAL,
    tif            TEXT NOT NULL DEFAULT 'DAY',
    status         TEXT NOT NULL,            -- Submitted / Filled / Cancelled / Rejected
    filled_qty     REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL,
    reject_reason  TEXT,
    coid           TEXT,                     -- customer order id (cOID)
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL,
    account_id  TEXT NOT NULL,
    conid       INTEGER NOT NULL,
    side        TEXT NOT NULL,
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    commission  REAL NOT NULL DEFAULT 0,
    executed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS prices (
    conid      INTEGER PRIMARY KEY,
    last       REAL,
    bid        REAL,
    ask        REAL,
    source     TEXT,                          -- manual / yfinance
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS price_history (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    conid INTEGER NOT NULL,
    price REAL NOT NULL,
    ts    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history ON price_history (conid, ts);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    os.makedirs(os.path.dirname(app.config["DATABASE"]) or ".", exist_ok=True)
    db = sqlite3.connect(app.config["DATABASE"])
    db.executescript(SCHEMA)
    account_id = app.config["ACCOUNT_ID"]
    cash = app.config["STARTING_CASH"]
    db.execute(
        "INSERT OR IGNORE INTO accounts (account_id, currency, starting_cash, cash) "
        "VALUES (?, 'USD', ?, ?)",
        (account_id, cash, cash),
    )
    db.commit()
    db.close()


def now():
    return time.time()
