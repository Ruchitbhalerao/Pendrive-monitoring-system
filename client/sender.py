"""HTTP delivery of queued events and heartbeats to the Flask API.

Uses only the Python standard library so no extra networking dependency is
needed. Authentication tokens are never logged.
"""

import json
import logging
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from .outbox import ALLOWED_EVENT_TYPES, PAYLOAD_FIELDS

logger = logging.getLogger(__name__)


class DeliveryOutcome:
    """Possible results of a single delivery attempt."""

    DELIVERED = "delivered"      # HTTP 201 created or HTTP 200 duplicate
    TRANSIENT = "transient"      # network problem or 5xx: retry later
    AUTH_ERROR = "auth_error"    # 401/403: configuration problem
    PERMANENT = "permanent"      # other 4xx: payload is invalid


def post_json(url, auth_token, payload, request_timeout):
    """POST a JSON payload and classify the response.

    Returns ``(outcome, status_code, detail)``. ``status_code`` is the HTTP
    status when a response was received, else None. ``detail`` is a short
    diagnostic that never contains the authentication token.

    This is the single place networking happens; callers only interpret the
    generic outcome for their own endpoint.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib_request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_token}",
        },
    )

    try:
        response = urllib_request.urlopen(request, timeout=request_timeout)
    except HTTPError as error:
        code = error.code
        try:
            error.read()
        except Exception:
            pass
        error.close()
        if code in (401, 403):
            return DeliveryOutcome.AUTH_ERROR, code, f"HTTP {code} authentication failed"
        if 400 <= code < 500:
            return DeliveryOutcome.PERMANENT, code, f"HTTP {code} rejected the request"
        return DeliveryOutcome.TRANSIENT, code, f"HTTP {code} server error"
    except (URLError, TimeoutError, OSError) as error:
        return DeliveryOutcome.TRANSIENT, None, f"connection failed: {error}"

    try:
        status = response.status
        response.read()
    finally:
        response.close()

    if status in (200, 201):
        return DeliveryOutcome.DELIVERED, status, f"HTTP {status}"
    if 500 <= status:
        return DeliveryOutcome.TRANSIENT, status, f"HTTP {status} server error"
    if 400 <= status < 500:
        return DeliveryOutcome.PERMANENT, status, f"HTTP {status} rejected the request"
    return DeliveryOutcome.TRANSIENT, status, f"unexpected HTTP {status}"


class EventSender:
    """Sends a single event to POST /api/events and classifies the outcome."""

    def __init__(self, events_url, auth_token, request_timeout):
        self.events_url = events_url
        self.auth_token = auth_token
        self.request_timeout = request_timeout

    def send(self, event):
        """Attempt to deliver one event.

        Returns (outcome, detail). ``detail`` is a short diagnostic that never
        contains the authentication token.
        """
        event_type = event.get("event_type")
        if event_type not in ALLOWED_EVENT_TYPES:
            return DeliveryOutcome.PERMANENT, f"invalid event_type {event_type!r}"

        payload = {field: event.get(field) for field in PAYLOAD_FIELDS}
        outcome, status, detail = post_json(
            self.events_url, self.auth_token, payload, self.request_timeout
        )
        if outcome == DeliveryOutcome.DELIVERED:
            return DeliveryOutcome.DELIVERED, ("created" if status == 201 else "duplicate")
        return outcome, detail


class HeartbeatSender:
    """Sends a single heartbeat to POST /api/heartbeat and classifies the outcome."""

    def __init__(self, heartbeat_url, auth_token, request_timeout):
        self.heartbeat_url = heartbeat_url
        self.auth_token = auth_token
        self.request_timeout = request_timeout

    def send(self, computer_id):
        """Attempt to deliver one heartbeat for ``computer_id``.

        Returns (outcome, detail); the same transient/auth/permanent rules as
        event delivery apply, so the agent can retry transient failures.
        """
        outcome, _status, detail = post_json(
            self.heartbeat_url, self.auth_token,
            {"computer_id": computer_id}, self.request_timeout,
        )
        if outcome == DeliveryOutcome.DELIVERED:
            return DeliveryOutcome.DELIVERED, "heartbeat accepted"
        return outcome, detail
