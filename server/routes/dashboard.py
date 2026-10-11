"""HTML routes for the professor's monitoring dashboard.

These pages render a lightweight shell with Jinja; the live data is fetched
from the read-only JSON API by plain JavaScript (no CDN, no build step). Both
pages require a signed-in dashboard session and otherwise redirect to /login.
"""

from flask import Blueprint, render_template

from ..auth import login_required

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/")
@login_required
def index():
    """Main dashboard: summary, computers, and recent alerts."""
    return render_template("index.html", active="dashboard")


@dashboard_bp.get("/events")
@login_required
def events_page():
    """Event history with filters and pagination."""
    return render_template("events.html", active="events")
