"""Tests for the dashboard HTML routes and read-only API endpoints.

Uses temporary databases and the Flask test client. No USB hardware, LAN, or
running server is required.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from server import create_app
from server.db import init_db

TOKEN = "dashboard-token"
DASHBOARD_TOKEN = "dashboard-read-token"
SECRET_KEY = "test-secret-key-0123456789abcdef"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture()
def database_path(tmp_path):
    return str(tmp_path / "dashboard.db")


@pytest.fixture()
def app(database_path):
    application = create_app({
        "DATABASE_PATH": database_path,
        "AUTH_TOKEN": TOKEN,
        "DASHBOARD_TOKEN": DASHBOARD_TOKEN,
        "SECRET_KEY": SECRET_KEY,
        "STALE_AFTER_SECONDS": 90,
    })
    init_db(database_path)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _signed_in(client):
    """Sign the test client into a dashboard session for every test.

    Authentication behaviour itself is covered in ``tests/test_security.py``;
    here we only need an authorised client to exercise the read paths.
    """
    with client.session_transaction() as session:
        session["dashboard_authenticated"] = True
    yield


def _insert_event(database_path, event_id, computer_id, event_type,
                  occurred_at, device_category=None, device_name=None,
                  received_at=None, last_seen=None):
    """Insert an event (and its computer) directly, bypassing the API."""
    received_at = received_at or occurred_at
    conn = sqlite3.connect(database_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO computers (computer_id, created_at) VALUES (?, ?)",
            (computer_id, received_at),
        )
        conn.execute(
            "UPDATE computers SET last_seen = ? WHERE computer_id = ?",
            (last_seen or received_at, computer_id),
        )
        conn.execute(
            """
            INSERT INTO events (
                event_id, computer_id, event_type, device_category, device_name,
                occurred_at, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, computer_id, event_type, device_category, device_name,
             occurred_at, received_at),
        )
        conn.commit()
    finally:
        conn.close()


def _set_heartbeat(database_path, computer_id, last_heartbeat):
    """Register a computer if needed and set its last heartbeat directly."""
    conn = sqlite3.connect(database_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO computers (computer_id, created_at) VALUES (?, ?)",
            (computer_id, _iso(_now())),
        )
        conn.execute(
            "UPDATE computers SET last_heartbeat = ? WHERE computer_id = ?",
            (last_heartbeat, computer_id),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 1-3. GET /api/computers
# --------------------------------------------------------------------------- #

def test_computers_empty_database(client):
    response = client.get("/api/computers")
    assert response.status_code == 200
    body = response.get_json()
    assert body["computers"] == []
    assert body["summary"] == {
        "total_computers": 0, "online": 0, "stale": 0,
        "never_reported": 0, "total_events": 0,
    }


def test_computers_multiple_sorted_with_counts(client, database_path):
    now = _now()
    _insert_event(database_path, "e1", "LAB-PC-02", "connected", _iso(now),
                  device_category="usb_storage")
    _insert_event(database_path, "e2", "LAB-PC-01", "connected", _iso(now),
                  device_category="usb_storage")
    _insert_event(database_path, "e3", "LAB-PC-01", "disconnected", _iso(now))

    body = client.get("/api/computers").get_json()
    ids = [row["computer_id"] for row in body["computers"]]
    assert ids == ["LAB-PC-01", "LAB-PC-02"]  # sorted
    counts = {row["computer_id"]: row["event_count"] for row in body["computers"]}
    assert counts == {"LAB-PC-01": 2, "LAB-PC-02": 1}
    assert body["summary"]["total_computers"] == 2
    assert body["summary"]["total_events"] == 3
    assert body["stale_after_seconds"] == 90


def test_computers_event_counts_and_timestamps(client, database_path):
    older = _now() - timedelta(minutes=10)
    newer = _now() - timedelta(seconds=5)
    _insert_event(database_path, "e1", "LAB-PC-01", "connected", _iso(older))
    _insert_event(database_path, "e2", "LAB-PC-01", "disconnected", _iso(newer))

    row = client.get("/api/computers").get_json()["computers"][0]
    assert row["event_count"] == 2
    assert row["last_event_at"] == _iso(newer)
    # Events register the computer but do not set a heartbeat.
    assert row["last_heartbeat"] is None
    assert row["status"] == "never"


def test_computers_status_distinguishes_recent_stale_and_never(client, database_path):
    recent = _now() - timedelta(seconds=5)
    old = _now() - timedelta(minutes=30)
    _set_heartbeat(database_path, "LAB-PC-01", _iso(recent))
    _set_heartbeat(database_path, "LAB-PC-02", _iso(old))

    conn = sqlite3.connect(database_path)
    try:
        conn.execute(
            "INSERT INTO computers (computer_id, created_at) VALUES (?, ?)",
            ("LAB-PC-03", _iso(_now())),
        )
        conn.commit()
    finally:
        conn.close()

    body = client.get("/api/computers").get_json()
    statuses = {row["computer_id"]: row["status"] for row in body["computers"]}
    assert statuses["LAB-PC-01"] == "recent"
    assert statuses["LAB-PC-02"] == "stale"
    assert statuses["LAB-PC-03"] == "never"
    assert body["summary"]["online"] == 1
    assert body["summary"]["stale"] == 1
    assert body["summary"]["never_reported"] == 1


def test_heartbeat_endpoint_marks_computer_recent(client):
    response = client.post(
        "/api/heartbeat",
        json={"computer_id": "LAB-PC-01"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200

    rows = client.get("/api/computers").get_json()["computers"]
    row = next(r for r in rows if r["computer_id"] == "LAB-PC-01")
    assert row["status"] == "recent"
    assert row["last_heartbeat"] is not None


# --------------------------------------------------------------------------- #
# 4-5. GET /api/events pagination and ordering
# --------------------------------------------------------------------------- #

def test_events_empty_database(client):
    body = client.get("/api/events").get_json()
    assert body["events"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["per_page"] == 20
    assert body["total_pages"] == 0


def test_events_pagination_and_stable_ordering(client, database_path):
    same_time = _iso(_now())
    for index in range(1, 6):
        _insert_event(database_path, f"e{index}", "LAB-PC-01", "connected", same_time)

    first = client.get("/api/events?page=1&per_page=2").get_json()
    second = client.get("/api/events?page=2&per_page=2").get_json()
    third = client.get("/api/events?page=3&per_page=2").get_json()

    assert first["total"] == 5
    assert first["total_pages"] == 3
    # Newest first: highest insertion id wins the tie-break.
    assert [e["event_id"] for e in first["events"]] == ["e5", "e4"]
    assert [e["event_id"] for e in second["events"]] == ["e3", "e2"]
    assert [e["event_id"] for e in third["events"]] == ["e1"]


# --------------------------------------------------------------------------- #
# 6-9. Filters and search
# --------------------------------------------------------------------------- #

def test_events_filter_by_computer(client, database_path):
    _insert_event(database_path, "e1", "LAB-PC-01", "connected", _iso(_now()))
    _insert_event(database_path, "e2", "LAB-PC-02", "connected", _iso(_now()))

    body = client.get("/api/events?computer_id=LAB-PC-01").get_json()
    assert body["total"] == 1
    assert body["events"][0]["computer_id"] == "LAB-PC-01"


def test_events_filter_by_event_type(client, database_path):
    _insert_event(database_path, "e1", "LAB-PC-01", "connected", _iso(_now()))
    _insert_event(database_path, "e2", "LAB-PC-01", "disconnected", _iso(_now()))

    body = client.get("/api/events?event_type=disconnected").get_json()
    assert body["total"] == 1
    assert body["events"][0]["event_type"] == "disconnected"


def test_events_filter_by_device_category(client, database_path):
    _insert_event(database_path, "e1", "LAB-PC-01", "connected", _iso(_now()),
                  device_category="usb_storage")
    _insert_event(database_path, "e2", "LAB-PC-01", "connected", _iso(_now()),
                  device_category="usb_peripheral")

    body = client.get("/api/events?device_category=usb_storage").get_json()
    assert body["total"] == 1
    assert body["events"][0]["device_category"] == "usb_storage"


def test_events_search_matches_computer_and_device(client, database_path):
    _insert_event(database_path, "e1", "LAB-PC-01", "connected", _iso(_now()),
                  device_name="SanDisk Cruzer")
    _insert_event(database_path, "e2", "LAB-PC-02", "connected", _iso(_now()),
                  device_name="Logitech Mouse")

    by_device = client.get("/api/events?q=SanDisk").get_json()
    assert by_device["total"] == 1
    assert by_device["events"][0]["event_id"] == "e1"

    by_computer = client.get("/api/events?q=LAB-PC-02").get_json()
    assert by_computer["total"] == 1
    assert by_computer["events"][0]["event_id"] == "e2"


def test_events_search_treats_wildcards_literally(client, database_path):
    _insert_event(database_path, "e1", "LAB-PC-01", "connected", _iso(_now()),
                  device_name="100% genuine")
    _insert_event(database_path, "e2", "LAB-PC-02", "connected", _iso(_now()),
                  device_name="plain device")

    body = client.get("/api/events?q=%25").get_json()  # literal '%'
    assert body["total"] == 1
    assert body["events"][0]["event_id"] == "e1"


def test_unknown_computer_filter_returns_empty(client, database_path):
    _insert_event(database_path, "e1", "LAB-PC-01", "connected", _iso(_now()))
    response = client.get("/api/events?computer_id=DOES-NOT-EXIST")
    assert response.status_code == 200
    assert response.get_json()["events"] == []
    assert response.get_json()["total"] == 0


# --------------------------------------------------------------------------- #
# 10-11. Invalid parameters
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("query", [
    "page=0", "page=-1", "page=abc", "per_page=0", "per_page=-5",
    "per_page=abc", "per_page=1000",
])
def test_invalid_pagination_returns_400(client, query):
    response = client.get(f"/api/events?{query}")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_invalid_event_type_filter_returns_400(client):
    response = client.get("/api/events?event_type=exploded")
    assert response.status_code == 400
    assert "event_type" in response.get_json()["error"]


# --------------------------------------------------------------------------- #
# 13-14. Existing ingestion contracts still hold
# --------------------------------------------------------------------------- #

def _post_event(client, event_id="evt-1"):
    return client.post(
        "/api/events",
        json={
            "event_id": event_id,
            "computer_id": "LAB-PC-01",
            "event_type": "connected",
            "device_category": "usb_storage",
            "occurred_at": _iso(_now()),
        },
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def test_post_events_authentication_still_enforced(client):
    assert client.post("/api/events", json={}).status_code == 401
    assert _post_event(client).status_code == 201


def test_duplicate_event_behavior_still_works(client, database_path):
    assert _post_event(client, "dup-1").status_code == 201
    duplicate = _post_event(client, "dup-1")
    assert duplicate.status_code == 200
    assert duplicate.get_json()["status"] == "duplicate"
    conn = sqlite3.connect(database_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


# --------------------------------------------------------------------------- #
# 15. Dashboard pages render
# --------------------------------------------------------------------------- #

def test_dashboard_pages_render_empty(client):
    index = client.get("/")
    events = client.get("/events")
    assert index.status_code == 200
    assert events.status_code == 200
    assert b"USB Device Monitoring System" in index.data
    assert b"Event History" in events.data


def test_dashboard_pages_render_with_data(client, database_path):
    _insert_event(database_path, "e1", "LAB-PC-01", "connected", _iso(_now()),
                  device_category="usb_storage", device_name="Cruzer")
    assert client.get("/").status_code == 200
    assert client.get("/events?computer_id=LAB-PC-01").status_code == 200


def test_browser_404_returns_html_and_api_404_returns_json(client):
    browser = client.get("/no-such-page", headers={"Accept": "text/html"})
    api = client.get("/api/no-such-endpoint")
    assert browser.status_code == 404
    assert browser.mimetype == "text/html"
    assert api.status_code == 404
    assert api.is_json


# --------------------------------------------------------------------------- #
# 16. Unavailable database handled gracefully
# --------------------------------------------------------------------------- #

def test_unavailable_database_is_handled(tmp_path):
    # Pointing the database at a directory makes sqlite fail to open it.
    app = create_app({
        "DATABASE_PATH": str(tmp_path),
        "AUTH_TOKEN": TOKEN,
        "DASHBOARD_TOKEN": DASHBOARD_TOKEN,
        "SECRET_KEY": SECRET_KEY,
    })
    client = app.test_client()
    with client.session_transaction() as session:
        session["dashboard_authenticated"] = True

    api = client.get("/api/computers")
    page = client.get("/")
    assert api.status_code == 500
    assert api.get_json() == {"error": "internal server error"}
    assert page.status_code == 200  # shell renders; JS shows the error state
