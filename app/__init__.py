import json
import logging
import sqlite3
import time
from pathlib import Path

from flask import Flask, g, render_template, request
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import get_config
from app.extensions import csrf, db, migrate


def create_app(config_name=None, test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"] or app.config["SECRET_KEY"].startswith("replace-with"):
        raise RuntimeError("Generate and set SECRET_KEY in .env before starting BizNova. See README.md.")
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:"):
        raise RuntimeError("This version supports SQLite databases only.")
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if app.config["PROXY_FIX_HOPS"]:
        hops = app.config["PROXY_FIX_HOPS"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops)
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)

    with app.app_context():
        @event.listens_for(db.engine, "connect")
        def configure_sqlite(connection, _):
            if isinstance(connection, sqlite3.Connection):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={int(app.config['SQLITE_BUSY_TIMEOUT_MS'])}")
                cursor.close()

    from app.models import BusinessSettings
    from app.security import load_user
    from app.services.clock import business_today, utc_to_local
    from app.services.errors import ServiceError
    from app.services.money import format_money, money_input
    from app.services.reports import PERIOD_OPTIONS
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.dashboard import bp as dashboard_bp
    from app.blueprints.products import bp as products_bp
    from app.blueprints.transactions import sales_bp, purchases_bp
    from app.blueprints.expenses import bp as expenses_bp
    from app.blueprints.reports import bp as reports_bp
    from app.blueprints.settings import bp as settings_bp
    for bp in (auth_bp, dashboard_bp, products_bp, sales_bp, purchases_bp, expenses_bp, reports_bp, settings_bp):
        app.register_blueprint(bp)

    @app.before_request
    def prepare_request():
        g.started = time.monotonic()
        if request.endpoint == "static":
            return
        load_user()
        g.business = BusinessSettings.get()

    @app.context_processor
    def common_context():
        business = getattr(g, "business", None) or BusinessSettings(
            business_name="BizNova", currency="INR", timezone="Asia/Kolkata", theme="system"
        )
        return {
            "business": business, "current_user": getattr(g, "user", None),
            "today": business_today(business.timezone), "demo_mode": app.config["DEMO_MODE"],
            "errors": {}, "form": {},
            "period_options": PERIOD_OPTIONS,
        }

    @app.template_filter("money")
    def money(value):
        return format_money(value, getattr(getattr(g, "business", None), "currency", "INR"))

    app.add_template_filter(money_input, "money_input")

    @app.template_filter("localtime")
    def localtime(value):
        local = utc_to_local(value, getattr(getattr(g, "business", None), "timezone", "Asia/Kolkata"))
        return local.strftime("%d %b %Y, %H:%M") if local else "—"

    def error_page(code, title, message, retryable=False):
        return render_template("error.html", code=code, title=title, message=message, retryable=retryable), code

    @app.errorhandler(ServiceError)
    def service_error(error):
        db.session.rollback()
        return error_page(error.status_code, "Unable to complete request", error.message)

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return error_page(400, "Security token expired", "Reload the form before submitting. Your request was not saved.")

    @app.errorhandler(OperationalError)
    def database_error(error):
        db.session.rollback()
        app.logger.error(json.dumps({"event": "database_unavailable", "path": request.path}))
        response, status = error_page(503, "Database temporarily unavailable", "Please retry shortly. If you submitted a sale or purchase, retry the same form to prevent duplicates.", True)
        return response, status, {"Retry-After": "3"}

    @app.errorhandler(HTTPException)
    def http_error(error):
        return error_page(error.code, error.name, error.description)

    @app.errorhandler(Exception)
    def unexpected_error(error):
        db.session.rollback()
        app.logger.error(json.dumps({"event": "request_failed", "path": request.path, "type": type(error).__name__}))
        return error_page(500, "Something went wrong", "We could not confirm completion. Keep the form and retry with the same request key.", True)

    @app.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        app.logger.info(json.dumps({"event": "request", "method": request.method, "path": request.path, "status": response.status_code}))
        return response

    from app.cli import register_cli
    register_cli(app)
    app.logger.setLevel(logging.INFO)
    return app
