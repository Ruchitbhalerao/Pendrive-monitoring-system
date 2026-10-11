"""Persistent local outbox for USB events.

Events are written to a local SQLite database *before* any delivery attempt, so
nothing is lost if the server is down or the client process exits. Each pending
event keeps the same event_id across retries; the backend's duplicate handling
therefore makes retries safe.
"""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

ALLOWED_EVENT_TYPES = {"connected", "disconnected"}

# Columns that make up the outbox queue (event payload plus bookkeeping).
SCHEMA = """
CREATE TABLE IF NOT EXISTS outgoing_events (
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
    created_at      TEXT    NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_outgoing_pending ON outgoing_events (id);
"""

PAYLOAD_FIELDS = (
    "event_id", "computer_id", "event_type", "device_category",
    "vendor_id", "product_id", "device_name", "occurred_at",
)


def utc_now_iso():
    """Current UTC time as an ISO-8601 string with a Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Outbox:
    """SQLite-backed FIFO queue of events awaiting delivery."""

    def __init__(self, database_path):
        self.database_path = database_path

    def _connect(self):
        # A fresh connection per operation keeps the queue thread-safe
        # (connections are not shared between the monitor and delivery threads).
        conn = sqlite3.connect(self.database_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        """Create the outbox table if it does not exist. Safe to call twice."""
        with self._connection() as conn:
            if self.database_path != ":memory:":
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.Error:
                    pass
            conn.executescript(SCHEMA)

    @staticmethod
    def generate_event_id():
        """Generate a unique event identifier using uuid."""
        return str(uuid.uuid4())

    def enqueue(self, event_id, computer_id, event_type, occurred_at,
                device_category=None, vendor_id=None, product_id=None,
                device_name=None):
        """Persist a pending event. Returns True if inserted, False if it
        already exists. Rejects unsupported event types before queueing."""
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(
                f"invalid event_type {event_type!r}; expected 'connected' or 'disconnected'"
            )
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO outgoing_events (
                    event_id, computer_id, event_type, device_category,
                    vendor_id, product_id, device_name, occurred_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, computer_id, event_type, device_category,
                    vendor_id, product_id, device_name, occurred_at, utc_now_iso(),
                ),
            )
            return cursor.rowcount == 1

    def pending(self):
        """Return pending events in insertion order as list of dicts."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM outgoing_events ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_count(self):
        with self._connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM outgoing_events"
            ).fetchone()[0]

    def mark_delivered(self, event_id):
        """Remove an event from the queue after the server confirms receipt."""
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM outgoing_events WHERE event_id = ?", (event_id,)
            )
            return cursor.rowcount > 0

    def record_failure(self, event_id, error_message):
        """Record a failed attempt for diagnostics; the event is retained."""
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE outgoing_events
                SET attempts = attempts + 1, last_error = ?
                WHERE event_id = ?
                """,
                (str(error_message)[:500], event_id),
            )
