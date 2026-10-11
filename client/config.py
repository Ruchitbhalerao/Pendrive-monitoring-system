"""Client configuration loaded from environment variables.

Required:
    COMPUTER_ID    unique identifier for this computer, e.g. LAB-PC-01
    SERVER_URL     base URL of the Flask server, e.g. http://192.168.1.10:5000
    AUTH_TOKEN     bearer token matching the server configuration

Optional:
    CLIENT_DB_PATH    local SQLite outbox path (default: client_outbox.db)
    RETRY_INTERVAL    seconds between delivery attempts (default: 5)
    REQUEST_TIMEOUT   HTTP request timeout in seconds (default: 10)
    HEARTBEAT_INTERVAL_SECONDS
                      seconds between heartbeats (default: 30)

No real server address or token is hardcoded anywhere.
"""

import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

DEFAULT_DB_PATH = os.path.join(BASE_DIR, "client_outbox.db")
DEFAULT_RETRY_INTERVAL = 5.0
DEFAULT_REQUEST_TIMEOUT = 10.0
DEFAULT_HEARTBEAT_INTERVAL = 30.0

EVENTS_PATH = "/api/events"
HEARTBEAT_PATH = "/api/heartbeat"


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


class ClientConfig:
    """Validated client settings."""

    def __init__(self, computer_id, server_url, auth_token,
                 client_db_path, retry_interval, request_timeout,
                 heartbeat_interval):
        self.computer_id = computer_id
        self.server_url = server_url
        self.auth_token = auth_token
        self.client_db_path = client_db_path
        self.retry_interval = retry_interval
        self.request_timeout = request_timeout
        self.heartbeat_interval = heartbeat_interval

    @property
    def events_url(self):
        """Full endpoint for event submission."""
        return self.server_url + EVENTS_PATH

    @property
    def heartbeat_url(self):
        """Full endpoint for heartbeat submission."""
        return self.server_url + HEARTBEAT_PATH


def _required(environ, name):
    value = environ.get(name)
    if value is None or not str(value).strip():
        raise ConfigError(f"{name} is required")
    return str(value).strip()


def _positive_float(environ, name, default):
    raw = environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a number")
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero")
    return value


def _normalize_server_url(raw_url):
    """Return a base URL without a trailing slash or duplicate endpoint.

    Accepts either a base URL (http://host:5000) or one that already includes
    /api/events or /api/heartbeat, so the client never builds a malformed URL.
    """
    url = raw_url.strip().rstrip("/")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ConfigError("SERVER_URL must start with http:// or https://")
    for path in (EVENTS_PATH, HEARTBEAT_PATH):
        if url.endswith(path):
            url = url[: -len(path)].rstrip("/")
            break
    if not url:
        raise ConfigError("SERVER_URL must include a host")
    return url


def load_config(environ=None):
    """Build a ClientConfig from a mapping (defaults to os.environ)."""
    environ = dict(os.environ) if environ is None else dict(environ)

    computer_id = _required(environ, "COMPUTER_ID")
    if len(computer_id) > 64:
        raise ConfigError("COMPUTER_ID must be at most 64 characters")

    server_url = _normalize_server_url(_required(environ, "SERVER_URL"))
    auth_token = _required(environ, "AUTH_TOKEN")

    client_db_path = environ.get("CLIENT_DB_PATH") or DEFAULT_DB_PATH
    retry_interval = _positive_float(environ, "RETRY_INTERVAL", DEFAULT_RETRY_INTERVAL)
    request_timeout = _positive_float(environ, "REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT)
    heartbeat_interval = _positive_float(
        environ, "HEARTBEAT_INTERVAL_SECONDS", DEFAULT_HEARTBEAT_INTERVAL
    )

    return ClientConfig(
        computer_id=computer_id,
        server_url=server_url,
        auth_token=auth_token,
        client_db_path=client_db_path,
        retry_interval=retry_interval,
        request_timeout=request_timeout,
        heartbeat_interval=heartbeat_interval,
    )
