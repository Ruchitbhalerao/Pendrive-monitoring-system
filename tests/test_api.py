"""Tests for the event ingestion API.

Each test uses a fresh temporary SQLite database and a known test token, so no
real development database is touched and no physical USB hardware is required.
"""

import sqlite3

import pytest

from server import create_app
from server.db import init_db

TEST_TOKEN = "test-token-123"


def _event(**overrides):
    """Build a valid event payload, allowing individual fields to be overridden."""
    payload = {
        "event_id": "evt-0001",
        "computer_id": "LAB-PC-01",
        "event_type": "connected",
        "device_category": "usb_storage",
        "vendor_id": "0781",
        "product_id": "5567",
        "device_name": "SanDisk Cruzer",
        "occurred_at": "2026-10-11T14:07:00Z",
    }
    payload.update(overrides)
    return payload


def _auth_header(token=TEST_TOKEN):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def database_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture()
def app(database_path):
    application = create_app({
        "DATABASE_PATH": database_path,
        "AUTH_TOKEN": TEST_TOKEN,
    })
    init_db(database_path)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def _count_rows(database_path, table):
    conn = sqlite3.connect(database_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_health_endpoint_works(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_valid_event_is_accepted(client, database_path):
    response = client.post("/api/events", json=_event(), headers=_auth_header())
    assert response.status_code == 201
    assert response.get_json()["status"] == "created"
    assert _count_rows(database_path, "events") == 1
    assert _count_rows(database_path, "computers") == 1


def test_missing_token_is_rejected(client, database_path):
    response = client.post("/api/events", json=_event())
    assert response.status_code == 401
    assert _count_rows(database_path, "events") == 0


def test_invalid_token_is_rejected(client):
    response = client.post("/api/events", json=_event(), headers=_auth_header("wrong"))
    assert response.status_code == 401


def test_malformed_authorization_header_is_rejected(client):
    response = client.post(
        "/api/events",
        json=_event(),
        headers={"Authorization": TEST_TOKEN},  # missing "Bearer " prefix
    )
    assert response.status_code == 401


def test_invalid_json_is_rejected(client):
    response = client.post(
        "/api/events",
        data="not json",
        content_type="application/json",
        headers=_auth_header(),
    )
    assert response.status_code == 400


def test_missing_required_field_is_rejected(client, database_path):
    payload = _event()
    del payload["computer_id"]
    response = client.post("/api/events", json=payload, headers=_auth_header())
    assert response.status_code == 400
    assert _count_rows(database_path, "events") == 0


def test_invalid_timestamp_is_rejected(client):
    response = client.post(
        "/api/events",
        json=_event(occurred_at="not-a-date"),
        headers=_auth_header(),
    )
    assert response.status_code == 400


def test_unsupported_event_type_is_rejected(client, database_path):
    response = client.post(
        "/api/events",
        json=_event(event_type="exploded"),
        headers=_auth_header(),
    )
    assert response.status_code == 400
    assert _count_rows(database_path, "events") == 0


def test_duplicate_event_id_does_not_create_second_record(client, database_path):
    first = client.post("/api/events", json=_event(), headers=_auth_header())
    second = client.post("/api/events", json=_event(), headers=_auth_header())

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()["status"] == "duplicate"
    assert _count_rows(database_path, "events") == 1


def test_two_computers_are_registered_independently(client, database_path):
    client.post(
        "/api/events",
        json=_event(event_id="evt-a", computer_id="LAB-PC-01"),
        headers=_auth_header(),
    )
    client.post(
        "/api/events",
        json=_event(event_id="evt-b", computer_id="LAB-PC-02"),
        headers=_auth_header(),
    )
    assert _count_rows(database_path, "computers") == 2
    assert _count_rows(database_path, "events") == 2


def test_authentication_not_configured_fails_safely(database_path):
    app = create_app({"DATABASE_PATH": database_path, "AUTH_TOKEN": None})
    init_db(database_path)
    client = app.test_client()
    response = client.post("/api/events", json=_event(), headers=_auth_header())
    assert response.status_code == 401


def test_database_errors_do_not_expose_internal_details(client, monkeypatch):
    import server.routes.api as api_module

    def broken_db():
        raise sqlite3.OperationalError("secret internal database path /etc/passwd")

    monkeypatch.setattr(api_module, "get_db", broken_db)
    response = client.post("/api/events", json=_event(), headers=_auth_header())

    assert response.status_code == 500
    body = response.get_json()
    assert body == {"error": "internal server error"}
    assert "passwd" not in response.get_data(as_text=True)


# --------------------------------------------------------------------------- #
# Heartbeat endpoint
# --------------------------------------------------------------------------- #

def _heartbeat(**overrides):
    payload = {"computer_id": "LAB-PC-01"}
    payload.update(overrides)
    return payload


def test_heartbeat_requires_token(client, database_path):
    assert client.post("/api/heartbeat", json=_heartbeat()).status_code == 401
    assert _count_rows(database_path, "computers") == 0


def test_heartbeat_invalid_token_is_rejected(client):
    response = client.post("/api/heartbeat", json=_heartbeat(), headers=_auth_header("wrong"))
    assert response.status_code == 401


def test_heartbeat_invalid_json_is_rejected(client):
    response = client.post(
        "/api/heartbeat",
        data="not json",
        content_type="application/json",
        headers=_auth_header(),
    )
    assert response.status_code == 400


@pytest.mark.parametrize("payload", [
    {},
    {"computer_id": ""},
    {"computer_id": "   "},
    {"computer_id": 123},
    {"computer_id": "L" * 65},
])
def test_heartbeat_invalid_payload_is_rejected(client, database_path, payload):
    response = client.post("/api/heartbeat", json=payload, headers=_auth_header())
    assert response.status_code == 400
    assert _count_rows(database_path, "computers") == 0


def test_heartbeat_registers_computer_and_records_timestamp(client, database_path):
    response = client.post("/api/heartbeat", json=_heartbeat(), headers=_auth_header())
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["computer_id"] == "LAB-PC-01"
    assert body["last_heartbeat"]

    conn = sqlite3.connect(database_path)
    try:
        row = conn.execute(
            "SELECT last_heartbeat FROM computers WHERE computer_id = ?",
            ("LAB-PC-01",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == body["last_heartbeat"]


def test_heartbeat_updates_existing_computer(client, database_path):
    first = client.post("/api/heartbeat", json=_heartbeat(), headers=_auth_header()).get_json()
    second = client.post("/api/heartbeat", json=_heartbeat(), headers=_auth_header()).get_json()
    assert _count_rows(database_path, "computers") == 1
    assert second["last_heartbeat"] >= first["last_heartbeat"]


def test_heartbeat_creates_no_events(client, database_path):
    client.post("/api/heartbeat", json=_heartbeat(), headers=_auth_header())
    assert _count_rows(database_path, "events") == 0


def test_heartbeat_does_not_expose_token(client):
    response = client.post("/api/heartbeat", json=_heartbeat(), headers=_auth_header())
    assert TEST_TOKEN not in response.get_data(as_text=True)


def test_heartbeat_database_errors_do_not_expose_internal_details(client, monkeypatch):
    import server.routes.api as api_module

    def broken_db():
        raise sqlite3.OperationalError("secret internal database path /etc/passwd")

    monkeypatch.setattr(api_module, "get_db", broken_db)
    response = client.post("/api/heartbeat", json=_heartbeat(), headers=_auth_header())

    assert response.status_code == 500
    assert response.get_json() == {"error": "internal server error"}
    assert "passwd" not in response.get_data(as_text=True)


def test_migration_adds_last_heartbeat_to_existing_database(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE computers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                computer_id TEXT NOT NULL UNIQUE,
                last_seen TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                computer_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                device_category TEXT,
                vendor_id TEXT,
                product_id TEXT,
                device_name TEXT,
                occurred_at TEXT NOT NULL,
                received_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO computers (computer_id, created_at) VALUES (?, ?)",
            ("LAB-PC-09", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    init_db(path)
    init_db(path)  # repeatable without error

    conn = sqlite3.connect(path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(computers)")}
        count = conn.execute("SELECT COUNT(*) FROM computers").fetchone()[0]
    finally:
        conn.close()
    assert "last_heartbeat" in columns
    assert count == 1  # pre-existing data preserved
