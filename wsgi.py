"""WSGI entry point for production deployments (gunicorn, uWSGI, etc.).

Example:

    gunicorn --workers 2 --bind 0.0.0.0:5000 wsgi:application

The application reads all configuration from environment variables; see
``.env.example`` and ``deploy/server.env.example``. The database schema is
created (idempotently) here so a fresh deployment works without a separate
init step.
"""

from server import create_app
from server.config import DEFAULT_SQLITE_BUSY_TIMEOUT_MS, load_config
from server.db import init_db

_settings = load_config()
init_db(
    _settings["DATABASE_PATH"],
    busy_timeout_ms=_settings.get(
        "SQLITE_BUSY_TIMEOUT_MS", DEFAULT_SQLITE_BUSY_TIMEOUT_MS
    ),
)

application = create_app()

if __name__ == "__main__":  # pragma: no cover - manual smoke run only
    raise SystemExit(
        "Use a WSGI server, e.g. "
        "'gunicorn --workers 2 --bind 0.0.0.0:5000 wsgi:application'"
    )
