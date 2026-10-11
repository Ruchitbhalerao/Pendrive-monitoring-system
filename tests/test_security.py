"""Security, configuration, and deployment-hardening tests.

Covers dashboard/read authentication, CSRF-protected login/logout, separation
of the ingestion and dashboard secrets, secret non-leakage, production config
validation, and SQLite WAL/busy-timeout hardening. Runs entirely on macOS with
temporary databases; no USB hardware, LAN, or server is required.
"""

import sqlite3

import pytest

from server import create_app
from server.config import ConfigError, load_config, validate_config
from server.db import get_db, init_db

INGESTION_TOKEN = "ingest-token-aaaa"
DASHBOARD_TOKEN = "dashboard-token-bbbb"
SECRET_KEY = "unit-test-secret-key-0123456789"


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def database_path(tmp_path):
    return str(tmp_path / "security.db")


@pytest.fixture()
def app(database_path):
    application = create_app({
        "ENVIRONMENT": "development",
        "DATABASE_PATH": database_path,
        "AUTH_TOKEN": INGESTION_TOKEN,
        "DASHBOARD_TOKEN": DASHBOARD_TOKEN,
        "SECRET_KEY": SECRET_KEY,
        "STALE_AFTER_SECONDS": 90,
    })
    init_db(database_path)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(client, password=DASHBOARD_TOKEN, next_target=""):
    """Log the test client in through the real CSRF-protected form."""
    client.get("/login")  # seeds the session CSRF token
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    return client.post(
        "/login",
        data={"password": password, "csrf_token": csrf, "next": next_target},
    )


# --------------------------------------------------------------------------- #
# Read API access control
# --------------------------------------------------------------------------- #

def test_read_apis_require_authentication(client):
    for path in ("/api/computers", "/api/events"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.is_json
        assert response.get_json() == {"error": "authentication required"}


def test_read_apis_accept_dashboard_bearer_token(client):
    for path in ("/api/computers", "/api/events"):
        response = client.get(path, headers=_bearer(DASHBOARD_TOKEN))
        assert response.status_code == 200


def test_read_apis_reject_ingestion_token(client):
    response = client.get("/api/computers", headers=_bearer(INGESTION_TOKEN))
    assert response.status_code == 401


def test_read_apis_reject_wrong_token(client):
    response = client.get("/api/events", headers=_bearer("nope"))
    assert response.status_code == 401


def test_read_apis_accept_session_cookie(client):
    assert _login(client).status_code == 302
    assert client.get("/api/computers").status_code == 200
    assert client.get("/api/events").status_code == 200


# --------------------------------------------------------------------------- #
# HTML route protection and login/logout
# --------------------------------------------------------------------------- #

def test_dashboard_routes_redirect_to_login(client):
    for path in ("/", "/events"):
        response = client.get(path)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_login_page_renders_with_csrf(client):
    response = client.get("/login")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'name="password"' in body
    assert 'name="csrf_token"' in body
    assert DASHBOARD_TOKEN not in body
    assert INGESTION_TOKEN not in body


def test_login_with_correct_password_starts_session(client):
    response = _login(client)
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert client.get("/").status_code == 200
    with client.session_transaction() as session:
        assert session.get("dashboard_authenticated") is True


def test_login_with_wrong_password_is_rejected(client):
    response = _login(client, password="wrong-password")
    assert response.status_code == 401
    body = response.get_data(as_text=True)
    assert "Invalid credentials." in body
    assert DASHBOARD_TOKEN not in body
    assert INGESTION_TOKEN not in body
    assert client.get("/").status_code == 302  # still not signed in


def test_login_without_csrf_is_rejected(client):
    client.get("/login")
    response = client.post("/login", data={"password": DASHBOARD_TOKEN})
    assert response.status_code == 400


def test_login_honours_safe_next_only(client):
    response = _login(client, next_target="/events?page=2")
    assert response.status_code == 302
    assert response.headers["Location"] == "/events?page=2"

    # A protocol-relative target must not be honoured.
    response = _login(client, next_target="//evil.example")
    assert response.status_code == 302
    assert "evil.example" not in response.headers["Location"]


def test_logout_requires_csrf_and_clears_session(client):
    _login(client)
    assert client.get("/").status_code == 200

    # Missing CSRF is rejected and the session is preserved.
    assert client.post("/logout").status_code == 400
    assert client.get("/").status_code == 200

    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    response = client.post("/logout", data={"csrf_token": csrf})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert client.get("/").status_code == 302


# --------------------------------------------------------------------------- #
# Ingestion endpoints keep using the ingestion token
# --------------------------------------------------------------------------- #

def _event_payload(event_id="evt-sec-1"):
    return {
        "event_id": event_id,
        "computer_id": "LAB-PC-01",
        "event_type": "connected",
        "occurred_at": "2026-10-11T14:07:00Z",
    }


def test_events_still_require_ingestion_token(client):
    assert client.post("/api/events", json=_event_payload()).status_code == 401
    assert client.post(
        "/api/events", json=_event_payload(), headers=_bearer(INGESTION_TOKEN)
    ).status_code == 201


def test_dashboard_token_cannot_submit_events(client):
    response = client.post(
        "/api/events", json=_event_payload("evt-sec-2"),
        headers=_bearer(DASHBOARD_TOKEN),
    )
    assert response.status_code == 401


def test_heartbeat_requires_ingestion_token(client):
    assert client.post("/api/heartbeat", json={"computer_id": "LAB-PC-01"}).status_code == 401
    assert client.post(
        "/api/heartbeat", json={"computer_id": "LAB-PC-01"},
        headers=_bearer(DASHBOARD_TOKEN),
    ).status_code == 401
    assert client.post(
        "/api/heartbeat", json={"computer_id": "LAB-PC-01"},
        headers=_bearer(INGESTION_TOKEN),
    ).status_code == 200


def test_health_endpoint_is_public(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


# --------------------------------------------------------------------------- #
# Secrets never leak into responses
# --------------------------------------------------------------------------- #

def test_secrets_never_appear_in_any_response(client):
    responses = [
        client.get("/login"),
        client.get("/api/computers"),
        client.get("/api/computers", headers=_bearer("wrong")),
        client.get("/api/no-such-endpoint"),
        client.post("/api/events", json={}),
        _login(client, password="wrong-password"),
        client.get("/no-such-page", headers={"Accept": "text/html"}),
    ]
    for response in responses:
        body = response.get_data(as_text=True)
        assert INGESTION_TOKEN not in body
        assert DASHBOARD_TOKEN not in body
        assert SECRET_KEY not in body


def test_unauthorized_error_is_generic(client):
    response = client.get("/api/computers", headers=_bearer("wrong"))
    assert response.get_json() == {"error": "authentication required"}


# --------------------------------------------------------------------------- #
# Startup configuration validation
# --------------------------------------------------------------------------- #

def _settings(**overrides):
    base = {
        "IS_PRODUCTION": True,
        "AUTH_TOKEN": "a" * 32,
        "DASHBOARD_TOKEN": "b" * 32,
        "SECRET_KEY": "c" * 32,
        "SESSION_COOKIE_SECURE": True,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("missing", ["AUTH_TOKEN", "DASHBOARD_TOKEN", "SECRET_KEY"])
def test_production_requires_each_secret(missing):
    with pytest.raises(ConfigError):
        validate_config(_settings(**{missing: None}))


def test_production_rejects_identical_tokens():
    with pytest.raises(ConfigError):
        validate_config(_settings(DASHBOARD_TOKEN="a" * 32))


def test_production_rejects_short_secret_key():
    with pytest.raises(ConfigError):
        validate_config(_settings(SECRET_KEY="short"))


def test_valid_production_settings_pass():
    assert validate_config(_settings()) == []


def test_development_does_not_raise_for_missing_secrets():
    warnings = validate_config({
        "IS_PRODUCTION": False,
        "AUTH_TOKEN": None,
        "DASHBOARD_TOKEN": None,
        "SECRET_KEY": "ephemeral",
        "SESSION_COOKIE_SECURE": False,
    })
    assert warnings  # problems are reported as warnings, not exceptions


def test_create_app_fails_fast_on_invalid_production_config(tmp_path):
    with pytest.raises(ConfigError):
        create_app({
            "ENVIRONMENT": "production",
            "DATABASE_PATH": str(tmp_path / "prod.db"),
            "AUTH_TOKEN": None,
            "DASHBOARD_TOKEN": None,
            "SECRET_KEY": None,
        })


def test_invalid_environment_name_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ConfigError):
        load_config()


# --------------------------------------------------------------------------- #
# Database hardening
# --------------------------------------------------------------------------- #

def test_database_uses_wal_mode(tmp_path):
    path = str(tmp_path / "wal.db")
    init_db(path)
    conn = sqlite3.connect(path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_busy_timeout_and_foreign_keys_are_set(app):
    with app.app_context():
        conn = get_db()
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
