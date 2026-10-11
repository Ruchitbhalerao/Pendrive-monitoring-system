"""Browser session routes: login and logout for the dashboard.

The professor signs in with ``DASHBOARD_TOKEN`` at ``/login``. A successful
login stores a signed session cookie (see ``server/app.py`` for cookie flags);
the static JavaScript never contains the token. Login and logout are
state-changing form posts protected by a session CSRF token.
"""

import hmac

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..auth import ensure_csrf_token, login_required, safe_redirect_target, validate_csrf

auth_bp = Blueprint("auth", __name__)


def _post_login_target(raw_target):
    """Return a safe redirect path after login, else the dashboard."""
    return safe_redirect_target(raw_target) or url_for("dashboard.index")


@auth_bp.get("/login")
def login():
    """Render the login form (or skip ahead if already signed in)."""
    if session.get("dashboard_authenticated"):
        return redirect(_post_login_target(request.args.get("next")))

    return render_template(
        "login.html",
        csrf_token=ensure_csrf_token(),
        next_target=request.args.get("next", ""),
        error=None,
    )


@auth_bp.post("/login")
def login_submit():
    """Verify the dashboard password and start a signed session."""
    next_target = request.form.get("next", "")

    if not validate_csrf():
        return _render_login(
            next_target,
            "Your session expired or the form was invalid. Please try again.",
            400,
        )

    configured_token = current_app.config.get("DASHBOARD_TOKEN")
    supplied_token = request.form.get("password", "")

    # Constant-time comparison; never reveal which part was wrong.
    if not configured_token or not hmac.compare_digest(supplied_token, configured_token):
        current_app.logger.warning("Failed dashboard login attempt")
        return _render_login(next_target, "Invalid credentials.", 401)

    # Start a fresh session to avoid session fixation.
    session.clear()
    session["dashboard_authenticated"] = True
    session.permanent = True
    ensure_csrf_token()
    return redirect(_post_login_target(next_target))


@auth_bp.post("/logout")
@login_required
def logout():
    """End the dashboard session."""
    if not validate_csrf():
        return render_template(
            "error.html",
            code=400,
            name="Bad Request",
            message="The sign-out form expired. Reload the page and try again.",
        ), 400
    session.clear()
    return redirect(url_for("auth.login"))


def _render_login(next_target, error, status_code):
    return (
        render_template(
            "login.html",
            csrf_token=ensure_csrf_token(),
            next_target=next_target,
            error=error,
        ),
        status_code,
    )
