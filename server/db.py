"""SQLite database helpers.

Two tables are used:

* ``computers`` - one row per lab computer that has ever submitted an event.
* ``events``    - one row per USB connection/disconnection event.

All queries elsewhere in the project are parameterized; nothing here builds SQL
from user input.
"""

import os
import sqlite3

from flask import current_app, g

# How long (milliseconds) a connection waits for a lock before erroring.
DEFAULT_BUSY_TIMEOUT_MS = 5000

# Schema is idempotent so initialising an existing database is safe.
SCHEMA = """
CREATE TABLE IF NOT EXISTS computers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    computer_id    TEXT    NOT NULL UNIQUE,
    last_seen      TEXT,
    last_heartbeat TEXT,
    created_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_computers_last_heartbeat
    ON computers (last_heartbeat);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT    NOT NULL UNIQUE,
    computer_id     TEXT    NOT NULL,
    event_type      TEXT    NOT NULL
                            CHECK (event_type IN ('connected', 'disconnected')),
    device_category TEXT,
    vendor_id       TEXT,
    product_id      TEXT,
    device_name     TEXT,
    occurred_at     TEXT    NOT NULL,
    received_at     TEXT    NOT NULL,
    FOREIGN KEY (computer_id) REFERENCES computers (computer_id)
);

CREATE INDEX IF NOT EXISTS idx_events_computer_id     ON events (computer_id);
CREATE INDEX IF NOT EXISTS idx_events_event_type      ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at     ON events (occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_device_category ON events (device_category);
CREATE INDEX IF NOT EXISTS idx_events_computer_occurred
    ON events (computer_id, occurred_at);
"""


def _connect(database_path, busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS):
    """Open a connection with consistent, hardened settings.

    Foreign keys stay ON and transactions are unchanged, as before. WAL mode
    (for on-disk databases) lets readers and a writer proceed concurrently,
    which matters once the dashboard and clients hit the file at the same
    time. The busy timeout makes concurrent writers wait instead of failing
    immediately with "database is locked".
    """
    # ``timeout`` is the sqlite3-level busy timeout in seconds.
    conn = sqlite3.connect(database_path, timeout=busy_timeout_ms / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if database_path != ":memory:":
        # WAL is persistent for the database file; NORMAL is the usual safe
        # durability trade-off when WAL is enabled.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db():
    """Return the per-request connection, creating it on first use."""
    if "db" not in g:
        g.db = _connect(
            current_app.config["DATABASE_PATH"],
            busy_timeout_ms=current_app.config.get(
                "SQLITE_BUSY_TIMEOUT_MS", DEFAULT_BUSY_TIMEOUT_MS
            ),
        )
    return g.db


def close_db(exception=None):
    """Close the per-request connection at the end of the request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _migrate_schema(conn):
    """Add columns introduced after the initial release.

    Idempotent and safe on both fresh and pre-existing databases. Runs before
    ``SCHEMA`` so that indexes defined there can reference the new columns.
    """
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "computers" in tables:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(computers)")}
        if "last_heartbeat" not in existing:
            conn.execute("ALTER TABLE computers ADD COLUMN last_heartbeat TEXT")


def init_db(database_path, busy_timeout_ms=DEFAULT_BUSY_TIMEOUT_MS):
    """Create the tables and indexes. Safe to call repeatedly."""
    if database_path != ":memory:":
        parent = os.path.dirname(os.path.abspath(database_path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

    conn = _connect(database_path, busy_timeout_ms=busy_timeout_ms)
    try:
        _migrate_schema(conn)
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
