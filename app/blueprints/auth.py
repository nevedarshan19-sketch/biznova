from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash
from app.extensions import db
from app.models import User, utcnow
from app.security import clear_login_failures, reserve_login_attempt, login_required, safe_next_url, start_session

bp = Blueprint("auth", __name__, url_prefix="/auth")
_dummy_hash = generate_password_hash("not-a-real-account-password")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if getattr(g, "user", None):
        return redirect("/")
    errors, status = {}, 200
    target = safe_next_url(request.values.get("next"))
    if request.method == "POST":
        if not reserve_login_attempt():
            errors = {"_form": "Too many sign-in attempts. Please try again in 10 minutes."}
            status = 429
        else:
            username = request.form.get("username", "").strip().lower()[:40]
            password = request.form.get("password", "")
            user = db.session.scalar(select(User).where(User.username == username))
            valid = check_password_hash(user.password_hash if user else _dummy_hash, password[:1024])
            if user and valid and len(password) <= 1024:
                user.last_login_at = utcnow()
                db.session.commit()
                clear_login_failures()
                start_session(user)
                return redirect(target, code=303)
            errors = {"_form": "Invalid username or password."}
            status = 401
    return render_template("auth/login.html", errors=errors, next=target, form={"username": request.form.get("username", "")} if request.method == "POST" else {}), status


@bp.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"), code=303)
