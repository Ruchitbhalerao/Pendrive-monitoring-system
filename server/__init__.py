"""USB Device Monitoring and Alert System - server package.

This package contains the Flask backend that receives USB connection and
disconnection events from lab computers and stores them in SQLite.
"""

from .app import create_app

__all__ = ["create_app"]
