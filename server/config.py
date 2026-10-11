"""Configuration loaded from environment variables.

The project deliberately avoids python-dotenv. All settings are read from the
process environment, so an operator can start the server with shell exports or a
systemd ``EnvironmentFile``. See ``.env.example`` for the full list.

Two independent secrets are used:

* ``AUTH_TOKEN``      - ingestion token for ``POST /api/events`` and
                        ``POST /api/heartbeat`` (client agents).
* ``DASHBOARD_TOKEN`` - dashboard login password and the bearer token for the
                        read-only APIs (``GET /api/computers``, ``GET /api/events``).

They must never be the same value. ``SECRET_KEY`` signs the browser session
cookie and is independent of both.
"""

import hmac
import logging
import os
import secrets

logger = logging.getLogger(__name__)

# Project root is the parent directory of this ``server`` package.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

# Sensible default database location: a file in the project root.
DEFAULT_DATABASE_PATH = os.path.join(BASE_DIR, "exam_monitor.db")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000

# A computer is considered "online" (recent heartbeat) when its last_heartbeat
# timestamp is within this many seconds of the server's current time.
DEFAULT_STALE_AFTER_SECONDS = 90

# SQLite wait time (milliseconds) before returning "database is locked".
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 5000

# Browser session lifetime and cookie flags.
DEFAULT_SESSION_LIFETIME_HOURS = 12

# ``APP_ENV`` (or ``ENVIRONMENT``) selects validation strictness.
DEFAULT_ENVIRONMENT = "development"
VALID_ENVIRONMENTS = ("development", "production")

# Minimum acceptable length for SECRET_KEY in production.
MIN_SECRET_LENGTH = 16


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


def _read_port(value, default=DEFAULT_PORT):
    """Convert the PORT environment variable to an int, falling back safely."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_positive_int(value, default):
    """Convert a positive integer environment variable, falling back safely."""
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _read_bool(value, default=False):
    """Interpret common truthy strings for boolean environment variables."""
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _read_environment():
    """Return the validated deployment environment name."""
    raw = os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or DEFAULT_ENVIRONMENT
    environment = raw.strip().lower()
    if environment not in VALID_ENVIRONMENTS:
        raise ConfigError(
            "APP_ENV must be one of: " + ", ".join(VALID_ENVIRONMENTS)
        )
    return environment


def load_config():
    """Return the application settings as a plain dictionary.

    Only environment variables are read here; no secret is hardcoded. Missing
    tokens are left as ``None`` so protected endpoints fail safely (401) instead
    of accepting unauthenticated traffic. ``validate_config`` then enforces the
    stricter requirements for production deployments.
    """
    environment = _read_environment()

    secret_key = os.environ.get("SECRET_KEY")
    secret_key_generated = False
    if not secret_key and environment != "production":
        # Development convenience only: an ephemeral key keeps the app usable
        # locally. Sessions reset on restart, which is acceptable for demos.
        # In production SECRET_KEY is required and validated below.
        secret_key = secrets.token_hex(32)
        secret_key_generated = True

    return {
        "ENVIRONMENT": environment,
        "IS_PRODUCTION": environment == "production",
        "DATABASE_PATH": os.environ.get("DATABASE_PATH", DEFAULT_DATABASE_PATH),
        "AUTH_TOKEN": os.environ.get("AUTH_TOKEN"),
        "DASHBOARD_TOKEN": os.environ.get("DASHBOARD_TOKEN"),
        "SECRET_KEY": secret_key,
        "SECRET_KEY_GENERATED": secret_key_generated,
        "HOST": (os.environ.get("HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST,
        "PORT": _read_port(os.environ.get("PORT")),
        "STALE_AFTER_SECONDS": _read_positive_int(
            os.environ.get("STALE_AFTER_SECONDS"), DEFAULT_STALE_AFTER_SECONDS
        ),
        "SQLITE_BUSY_TIMEOUT_MS": _read_positive_int(
            os.environ.get("SQLITE_BUSY_TIMEOUT_MS"), DEFAULT_SQLITE_BUSY_TIMEOUT_MS
        ),
        "SESSION_LIFETIME_HOURS": _read_positive_int(
            os.environ.get("SESSION_LIFETIME_HOURS"), DEFAULT_SESSION_LIFETIME_HOURS
        ),
        "SESSION_COOKIE_SECURE": _read_bool(
            os.environ.get("SESSION_COOKIE_SECURE"), False
        ),
    }


def validate_config(settings):
    """Validate settings for the active environment.

    In production, missing or unsafe secrets raise :class:`ConfigError` so the
    server refuses to start with a broken security posture. In development the
    same problems are only logged as warnings, keeping local use convenient.

    Returns the list of warning strings emitted (useful for tests).
    """
    warnings = []
    is_production = bool(settings.get("IS_PRODUCTION"))

    if is_production:
        errors = []
        for name in ("AUTH_TOKEN", "DASHBOARD_TOKEN", "SECRET_KEY"):
            if not settings.get(name):
                errors.append(f"{name} is required in production")
        auth_token = settings.get("AUTH_TOKEN")
        dashboard_token = settings.get("DASHBOARD_TOKEN")
        if auth_token and dashboard_token and hmac.compare_digest(auth_token, dashboard_token):
            errors.append(
                "AUTH_TOKEN and DASHBOARD_TOKEN must be different values"
            )
        secret_key = settings.get("SECRET_KEY")
        if secret_key and len(secret_key) < MIN_SECRET_LENGTH:
            errors.append(
                f"SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters in production"
            )
        if errors:
            raise ConfigError("; ".join(errors))

    if not settings.get("AUTH_TOKEN"):
        warnings.append(
            "AUTH_TOKEN is not set; client ingestion endpoints will reject all "
            "requests (401) until it is configured."
        )
    if not settings.get("DASHBOARD_TOKEN"):
        warnings.append(
            "DASHBOARD_TOKEN is not set; the dashboard and read APIs are "
            "unusable until it is configured."
        )
    if settings.get("SECRET_KEY_GENERATED"):
        warnings.append(
            "SECRET_KEY is not set; using an ephemeral development key. Browser "
            "sessions will not survive a restart."
        )
    if settings.get("AUTH_TOKEN") and settings.get("DASHBOARD_TOKEN") and \
            hmac.compare_digest(settings["AUTH_TOKEN"], settings["DASHBOARD_TOKEN"]):
        warnings.append(
            "AUTH_TOKEN and DASHBOARD_TOKEN are identical; use separate secrets "
            "so read access and event ingestion are independent."
        )
    if not settings.get("SESSION_COOKIE_SECURE"):
        warnings.append(
            "SESSION_COOKIE_SECURE is disabled; enable it when serving over HTTPS."
        )

    for message in warnings:
        logger.warning("%s", message)
    return warnings
