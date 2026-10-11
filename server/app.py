"""Flask application factory and error handlers."""

from datetime import timedelta

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from .auth import ensure_csrf_token
from .config import load_config, validate_config
from .db import close_db
from .routes.api import api_bp
from .routes.auth import auth_bp
from .routes.dashboard import dashboard_bp


def _wants_json():
    """True for API requests or clients that explicitly prefer JSON."""
    if request.path.startswith("/api/"):
        return True
    best = request.accept_mimetypes.best_match(["text/html", "application/json"])
    return best == "application/json"


def _configure_session(app):
    """Apply secure session cookie defaults, overridable by config."""
    app.config["SECRET_KEY"] = app.config.get("SECRET_KEY")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = bool(
        app.config.get("SESSION_COOKIE_SECURE", False)
    )
    lifetime_hours = app.config.get("SESSION_LIFETIME_HOURS", 12)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=lifetime_hours)


def create_app(config_overrides=None):
    """Create and configure the Flask application.

    The application is returned without starting a server, so it can be
    imported by tests and by the WSGI entry point. Pass ``config_overrides`` to
    replace any setting.
    """
    app = Flask(__name__)

    settings = load_config()
    app.config.update(settings)
    if config_overrides:
        app.config.update(config_overrides)

    # Keep derived flags consistent when tests override raw settings.
    app.config["IS_PRODUCTION"] = app.config.get("ENVIRONMENT") == "production"
    validate_config(app.config)
    _configure_session(app)

    # Open/close a SQLite connection per request.
    app.teardown_appcontext(close_db)

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp)

    @app.context_processor
    def _inject_csrf_token():
        # Templates (e.g. the sign-out form) can render a fresh CSRF token.
        return {"csrf_token": ensure_csrf_token()}

    _register_error_handlers(app)
    return app


def _register_error_handlers(app):
    """Return JSON for API clients and HTML pages for browsers.

    Internal exception details and secrets are logged server-side but never
    returned to the client.
    """

    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        if _wants_json():
            return jsonify({"error": error.name, "status": error.code}), error.code
        return render_template(
            "error.html", code=error.code, name=error.name,
            message=error.description,
        ), error.code

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.exception("Unhandled server error")
        if _wants_json():
            return jsonify({"error": "internal server error"}), 500
        return render_template(
            "error.html", code=500, name="Internal Server Error",
            message="An unexpected error occurred.",
        ), 500
