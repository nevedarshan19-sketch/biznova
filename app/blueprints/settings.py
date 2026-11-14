from flask import Blueprint, flash, g, redirect, render_template, request, session
from app.blueprints.common import attempt
from app.extensions import db
from app.models import BusinessSettings, utcnow
from app.security import login_required, start_session
from app.services.audit import log_event
from app.services.clock import COMMON_TIMEZONES
from app.services.errors import FormValidator, ValidationError

bp = Blueprint("settings", __name__, url_prefix="/settings")


def save_settings():
    action = request.form.get("action")
    validator = FormValidator(request.form)
    if action == "business":
        name = validator.text("business_name", "Business name", required=True, max_len=120)
        currency = validator.choice("currency", "Currency", {"INR", "USD", "EUR", "GBP"})
        timezone = validator.choice("timezone", "Timezone", COMMON_TIMEZONES)
        theme = request.form.get("theme", "system")
        if theme not in {"system", "light", "dark"}:
            validator.errors["theme"] = "Choose a valid theme."
        validator.raise_if_errors()
        settings = db.session.get(BusinessSettings, 1)
        if settings is None:
            settings = BusinessSettings(id=1)
            db.session.add(settings)
        settings.business_name, settings.currency, settings.timezone, settings.theme = name, currency, timezone, theme
        log_event("settings.updated", "settings", 1, "Business display settings updated", user_id=g.user.id)
    elif action == "password":
        password = request.form.get("password", "")
        if not g.user.check_password(request.form.get("old_password", "")[:1024]):
            validator.errors["old_password"] = "Current password is incorrect."
        if not 12 <= len(password) <= 128:
            validator.errors["password"] = "Use between 12 and 128 characters."
        if password != request.form.get("confirm_password"):
            validator.errors["confirm_password"] = "Passwords do not match."
        validator.raise_if_errors()
        g.user.set_password(password)
        log_event("password.changed", "user", g.user.id, "Owner password changed", user_id=g.user.id)
    else:
        raise ValidationError("Unknown settings action.")
    db.session.commit()
    if action == "password":
        start_session(g.user)
    return action


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    errors, status = {}, 200
    if request.method == "POST":
        _, errors, status = attempt(save_settings)
        if not errors:
            flash("Settings saved.", "success")
            return redirect("/settings", code=303)
        form = {key: value for key, value in request.form.items() if "password" not in key}
    else:
        form = {"business_name": g.business.business_name, "currency": g.business.currency, "timezone": g.business.timezone, "theme": g.business.theme}
    return render_template("settings.html", form=form, errors=errors, timezones=COMMON_TIMEZONES), status
