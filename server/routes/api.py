"""API endpoints for health checks and USB event ingestion."""

import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from ..auth import require_read_access, require_token
from ..db import get_db

api_bp = Blueprint("api", __name__)

ALLOWED_EVENT_TYPES = {"connected", "disconnected"}

# Maximum accepted string lengths, used for validation.
MAX_LENGTHS = {
    "event_id": 128,
    "computer_id": 64,
    "device_category": 64,
    "vendor_id": 32,
    "product_id": 32,
    "device_name": 256,
}

REQUIRED_FIELDS = ("event_id", "computer_id", "event_type", "occurred_at")

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100

# Columns returned by read endpoints. These are device metadata only; the
# system never stores or returns file contents from USB devices.
EVENT_FIELDS = (
    "event_id", "computer_id", "event_type", "device_category",
    "vendor_id", "product_id", "device_name", "occurred_at", "received_at",
)


def _utc_now_iso():
    """Current UTC time as an ISO-8601 string with a Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_timestamp(value):
    """Validate an ISO-8601 timestamp and return it normalized to UTC.

    Returns None when the value is missing, not a string, or not parseable.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:  # Treat naive timestamps as UTC.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_event(payload):
    """Return (clean_data, errors). ``errors`` is a list of messages."""
    errors = []

    if not isinstance(payload, dict):
        return None, ["request body must be a JSON object"]

    for field in REQUIRED_FIELDS:
        value = payload.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"'{field}' is required")

    cleaned = {}
    for field, max_len in MAX_LENGTHS.items():
        value = payload.get(field)
        if value is None:
            cleaned[field] = None
            continue
        if not isinstance(value, str):
            errors.append(f"'{field}' must be a string")
            continue
        if len(value) > max_len:
            errors.append(f"'{field}' must be at most {max_len} characters")
            continue
        cleaned[field] = value or None

    event_type = payload.get("event_type")
    if event_type is not None and event_type not in ALLOWED_EVENT_TYPES:
        errors.append("'event_type' must be 'connected' or 'disconnected'")
    else:
        cleaned["event_type"] = event_type

    if not errors:
        normalized = _normalize_timestamp(payload.get("occurred_at"))
        if normalized is None:
            errors.append("'occurred_at' must be an ISO-8601 timestamp")
        else:
            cleaned["occurred_at"] = normalized

    if errors:
        return None, errors
    return cleaned, []


@api_bp.get("/health")
def health():
    """Liveness check. Does not require authentication."""
    db_status = "ok"
    try:
        conn = get_db()
        conn.execute("SELECT 1")
    except sqlite3.Error:
        db_status = "error"
        current_app.logger.exception("Health check could not query the database")

    status_code = 200 if db_status == "ok" else 503
    return jsonify({"status": "ok" if db_status == "ok" else "degraded",
                    "database": db_status}), status_code


def _validate_computer_id(payload):
    """Return (computer_id, errors) for a heartbeat payload."""
    if not isinstance(payload, dict):
        return None, ["request body must be a JSON object"]

    value = payload.get("computer_id")
    if value is None:
        return None, ["'computer_id' is required"]
    if not isinstance(value, str):
        return None, ["'computer_id' must be a string"]
    value = value.strip()
    if not value:
        return None, ["'computer_id' is required"]
    if len(value) > MAX_LENGTHS["computer_id"]:
        return None, [
            "'computer_id' must be at most "
            f"{MAX_LENGTHS['computer_id']} characters"
        ]
    return value, []


@api_bp.post("/heartbeat")
@require_token
def heartbeat():
    """Record a client heartbeat for an authenticated computer.

    Heartbeats update ``computers.last_heartbeat`` only. They do not create
    events and are safe to repeat; a computer is registered on first contact.
    """
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "request body must be valid JSON"}), 400

    computer_id, errors = _validate_computer_id(payload)
    if errors:
        return jsonify({"error": "invalid heartbeat data", "details": errors}), 400

    now = _utc_now_iso()
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO computers (computer_id, created_at) VALUES (?, ?)",
            (computer_id, now),
        )
        conn.execute(
            "UPDATE computers SET last_heartbeat = ? WHERE computer_id = ?",
            (now, computer_id),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        current_app.logger.exception("Failed to record heartbeat")
        return jsonify({"error": "internal server error"}), 500

    return jsonify({
        "status": "ok",
        "message": "heartbeat recorded",
        "computer_id": computer_id,
        "last_heartbeat": now,
    }), 200


@api_bp.post("/events")
@require_token
def create_event():
    """Accept a single USB event from an authenticated client."""
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "request body must be valid JSON"}), 400

    data, errors = _validate_event(payload)
    if errors:
        return jsonify({"error": "invalid event data", "details": errors}), 400

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM events WHERE event_id = ?", (data["event_id"],)
        ).fetchone()
        if existing is not None:
            # Duplicate submission: acknowledge without creating a new row.
            return jsonify({
                "status": "duplicate",
                "message": "event already recorded",
                "event_id": data["event_id"],
            }), 200

        now = _utc_now_iso()

        # Register the computer if this is the first time we see it.
        conn.execute(
            "INSERT OR IGNORE INTO computers (computer_id, created_at) VALUES (?, ?)",
            (data["computer_id"], now),
        )
        conn.execute(
            "UPDATE computers SET last_seen = ? WHERE computer_id = ?",
            (now, data["computer_id"]),
        )
        conn.execute(
            """
            INSERT INTO events (
                event_id, computer_id, event_type, device_category,
                vendor_id, product_id, device_name, occurred_at, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["computer_id"],
                data["event_type"],
                data["device_category"],
                data["vendor_id"],
                data["product_id"],
                data["device_name"],
                data["occurred_at"],
                now,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        current_app.logger.exception("Failed to store event")
        return jsonify({"error": "internal server error"}), 500

    return jsonify({
        "status": "created",
        "message": "event recorded",
        "event_id": data["event_id"],
        "computer_id": data["computer_id"],
    }), 201


# --------------------------------------------------------------------------- #
# Read endpoints (used by the professor's dashboard).
#
# These are read-only and require dashboard access: either a signed-in
# browser session or ``Authorization: Bearer <DASHBOARD_TOKEN>``. They never
# accept the client ingestion ``AUTH_TOKEN``. See ``require_read_access`` in
# ``server/auth.py`` and the README security section.
# --------------------------------------------------------------------------- #

def _parse_iso_utc(value):
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_positive_int_param(raw, default, name, maximum=None):
    """Validate an integer query parameter, raising ValueError when invalid."""
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be an integer")
    if value < 1:
        raise ValueError(f"'{name}' must be a positive integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"'{name}' must be at most {maximum}")
    return value


def _escape_like(value):
    """Escape LIKE wildcards so user text is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _computer_status(last_heartbeat, threshold_seconds, now):
    """Classify a computer's monitoring status from its last heartbeat.

    * "never"  - the client has never sent a heartbeat.
    * "recent" - a heartbeat arrived within the stale threshold.
    * "stale"  - the most recent heartbeat is older than the threshold.

    This reflects *client process health*. USB event activity is reported
    separately via ``last_seen`` / ``last_event_at``.
    """
    if not last_heartbeat:
        return "never"
    beat_at = _parse_iso_utc(last_heartbeat)
    if beat_at is None:
        return "never"
    age_seconds = (now - beat_at).total_seconds()
    return "recent" if age_seconds <= threshold_seconds else "stale"


def _event_to_dict(row):
    return {field: row[field] for field in EVENT_FIELDS}


@api_bp.get("/computers")
@require_read_access
def list_computers():
    """Return registered computers with monitoring status and event counts."""
    threshold = current_app.config.get("STALE_AFTER_SECONDS", 90)
    now = datetime.now(timezone.utc)

    try:
        conn = get_db()
        # A single grouped query avoids N+1 lookups for per-computer counts.
        rows = conn.execute(
            """
            SELECT c.computer_id AS computer_id,
                   c.created_at  AS created_at,
                   c.last_seen   AS last_seen,
                   c.last_heartbeat AS last_heartbeat,
                   COUNT(e.id)   AS event_count,
                   MAX(e.occurred_at) AS last_event_at
            FROM computers c
            LEFT JOIN events e ON e.computer_id = c.computer_id
            GROUP BY c.computer_id, c.created_at, c.last_seen, c.last_heartbeat
            ORDER BY c.computer_id
            """
        ).fetchall()
    except sqlite3.Error:
        current_app.logger.exception("Failed to list computers")
        return jsonify({"error": "internal server error"}), 500

    computers = []
    summary = {
        "total_computers": 0,
        "online": 0,
        "stale": 0,
        "never_reported": 0,
        "total_events": 0,
    }
    for row in rows:
        status = _computer_status(row["last_heartbeat"], threshold, now)
        summary["total_computers"] += 1
        summary["total_events"] += row["event_count"]
        if status == "recent":
            summary["online"] += 1
        elif status == "stale":
            summary["stale"] += 1
        else:
            summary["never_reported"] += 1

        computers.append({
            "computer_id": row["computer_id"],
            "created_at": row["created_at"],
            "last_seen": row["last_seen"],
            "last_heartbeat": row["last_heartbeat"],
            "last_event_at": row["last_event_at"],
            "event_count": row["event_count"],
            "status": status,
        })

    return jsonify({
        "computers": computers,
        "summary": summary,
        "stale_after_seconds": threshold,
        "server_time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


@api_bp.get("/events")
@require_read_access
def list_events():
    """Return a paginated, filtered list of events, newest first."""
    try:
        page = _parse_positive_int_param(request.args.get("page"), 1, "page")
        per_page = _parse_positive_int_param(
            request.args.get("per_page"), DEFAULT_PER_PAGE, "per_page", maximum=MAX_PER_PAGE
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    computer_id = (request.args.get("computer_id") or "").strip() or None
    event_type = (request.args.get("event_type") or "").strip() or None
    device_category = (request.args.get("device_category") or "").strip() or None
    search = (request.args.get("q") or "").strip() or None

    if event_type is not None and event_type not in ALLOWED_EVENT_TYPES:
        return jsonify({"error": "'event_type' must be 'connected' or 'disconnected'"}), 400

    # WHERE clauses are fixed strings; every value is bound as a parameter.
    where_clauses = []
    params = []
    if computer_id is not None:
        where_clauses.append("computer_id = ?")
        params.append(computer_id)
    if event_type is not None:
        where_clauses.append("event_type = ?")
        params.append(event_type)
    if device_category is not None:
        where_clauses.append("device_category = ?")
        params.append(device_category)
    if search is not None:
        like_pattern = f"%{_escape_like(search)}%"
        where_clauses.append(
            "(computer_id LIKE ? ESCAPE '\\' OR device_name LIKE ? ESCAPE '\\')"
        )
        params.extend([like_pattern, like_pattern])

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    try:
        conn = get_db()
        total = conn.execute(
            f"SELECT COUNT(*) FROM events{where_sql}", params
        ).fetchone()[0]
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"SELECT * FROM events{where_sql} "
            "ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()
    except sqlite3.Error:
        current_app.logger.exception("Failed to list events")
        return jsonify({"error": "internal server error"}), 500

    total_pages = (total + per_page - 1) // per_page
    return jsonify({
        "events": [_event_to_dict(row) for row in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "filters": {
            "computer_id": computer_id,
            "event_type": event_type,
            "device_category": device_category,
            "q": search,
        },
    })
