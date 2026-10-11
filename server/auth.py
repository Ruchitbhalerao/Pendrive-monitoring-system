"""Authentication helpers for the ingestion API and the dashboard.

Two separate credentials are supported:

* Ingestion token (``AUTH_TOKEN``) - sent by client agents as
  ``Authorization: Bearer <token>`` to ``POST /api/events`` and
  ``POST /api/heartbeat``.
* Dashboard token (``DASHBOARD_TOKEN``) - the professor's read credential. For
  the read-only APIs it is accepted as ``Authorization: Bearer <token>``. For
  the browser dashboard it is exchanged for a signed session cookie at
  ``POST /login`` so the secret is never embedded in static JavaScript.

Missing tokens cause protected endpoints to fail safely (401) rather than
accepting unauthenticated traffic.
"""

import hmac
import secrets
from functools import wraps

from flask import current_app, jsonify, redirect, request, session, url_for


def _extract_bearer_token(header_value):
    """Return the token from a ``Bearer`` header, or None if malformed."""
    if not header_value:
        return None
    parts = header_value.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _constant_time_match(candidate, configured):
    """Compare two secrets without leaking length/contents via timing."""
    if not candidate or not configured:
        return False
    return hmac.compare_digest(candidate, configured)


def require_token(view):
    """Decorator that enforces the client ingestion bearer token."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        configured_token = current_app.config.get("AUTH_TOKEN")

        # Fail safely: never accept requests when no token is configured.
        if not configured_token:
            return jsonify({"error": "authentication is not configured"}), 401

        supplied_token = _extract_bearer_token(request.headers.get("Authorization"))
        if supplied_token is None:
            return jsonify({"error": "missing or malformed Authorization header"}), 401

        # Constant-time comparison avoids leaking the token via timing.
        if not hmac.compare_digest(supplied_token, configured_token):
            return jsonify({"error": "invalid authentication token"}), 401

        return view(*args, **kwargs)

    return wrapper


def is_dashboard_authenticated():
    """True when the browser session belongs to a signed-in professor."""
    return bool(session.get("dashboard_authenticated"))


def _has_dashboard_bearer_token():
    """True when the request carries the dashboard token as a bearer token."""
    configured_token = current_app.config.get("DASHBOARD_TOKEN")
    supplied_token = _extract_bearer_token(request.headers.get("Authorization"))
    return _constant_time_match(supplied_token, configured_token)


def require_read_access(view):
    """Decorator for read APIs: session cookie or dashboard bearer token."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if is_dashboard_authenticated() or _has_dashboard_bearer_token():
            return view(*args, **kwargs)
        return jsonify({"error": "authentication required"}), 401

    return wrapper


def safe_redirect_target(target):
    """Return a safe local path for post-login redirect, or None.

    Only same-site, absolute paths are allowed so an attacker cannot use the
    login page to bounce a user to an external site.
    """
    if not target or not isinstance(target, str):
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


def login_required(view):
    """Decorator for HTML routes: redirect browsers to the login page."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if is_dashboard_authenticated():
            return view(*args, **kwargs)
        target = request.path
        if request.query_string:
            target += "?" + request.query_string.decode("utf-8", "ignore")
        return redirect(url_for("auth.login", next=target))

    return wrapper


def ensure_csrf_token():
    """Return the session CSRF token, creating it on first use."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf():
    """Validate the CSRF token submitted with a state-changing form POST."""
    expected = session.get("csrf_token")
    submitted = request.form.get("csrf_token")
    return bool(expected) and bool(submitted) and hmac.compare_digest(expected, submitted)
