"""USB monitoring client package.

The client runs on Ubuntu lab computers, detects USB connect/disconnect events,
queues them locally, and delivers them to the Flask server.

Importing this package must work on macOS: pyudev is imported lazily inside the
Linux monitor, so nothing here requires Linux-only dependencies at import time.
"""

from .config import ClientConfig, ConfigError, load_config

__all__ = ["ClientConfig", "ConfigError", "load_config"]
