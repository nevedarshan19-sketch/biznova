from flask import Blueprint, g, render_template
from app.blueprints.common import period_from_request
from app.services.reports import dashboard_data

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def index():
    if getattr(g, "user", None) is None:
        return render_template("home.html")
    period = period_from_request()
    return render_template("dashboard.html", data=dashboard_data(period), period=period)
