from flask import Blueprint, render_template
from app.blueprints.common import period_from_request
from app.security import login_required
from app.services.reports import dashboard_data

bp = Blueprint("dashboard", __name__)


@bp.get("/")
@login_required
def index():
    period = period_from_request()
    return render_template("dashboard.html", data=dashboard_data(period), period=period)
