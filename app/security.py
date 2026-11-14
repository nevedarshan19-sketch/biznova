import hashlib
import hmac
import time
from collections import OrderedDict
from datetime import datetime
from functools import wraps
from threading import Lock
from urllib.parse import unquote, urlsplit

from flask import current_app, g, redirect, request, session, url_for
from app.extensions import db
from app.models import User, utcnow

_attempts = OrderedDict()
_attempt_lock = Lock()


def safe_next_url(target, fallback="/"):
    if not isinstance(target, str) or not target:
        return fallback
    decoded = unquote(target)
    if any(ord(char) < 32 for char in decoded) or "\\" in decoded:
        return fallback
    try:
        parts = urlsplit(decoded)
    except ValueError:
        return fallback
    if parts.scheme or parts.netloc or not decoded.startswith("/") or decoded.startswith("//"):
        return fallback
    return target


def _rate_key():
    # Only a local tunnel process can reach the loopback socket, so its client-IP header is trusted there alone.
    header = current_app.config.get("CLIENT_IP_HEADER")
    if header and request.remote_addr in ("127.0.0.1", "::1"):
        forwarded = request.headers.get(header, "").strip()
        if forwarded:
            return forwarded[:64]
    return request.remote_addr or "unknown"


def reserve_login_attempt(key=None):
    key = key or _rate_key()
    now = time.monotonic()
    with _attempt_lock:
        recent = [stamp for stamp in _attempts.get(key, [])
                  if now - stamp < current_app.config["LOGIN_WINDOW_SECONDS"]]
        _attempts[key] = recent
        _attempts.move_to_end(key)
        while len(_attempts) > 10000:
            _attempts.popitem(last=False)
        if len(recent) >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
            return False
        recent.append(now)
        return True


def clear_login_failures(key=None):
    with _attempt_lock:
        _attempts.pop(key or _rate_key(), None)


def reset_rate_limits():
    with _attempt_lock:
        _attempts.clear()


def session_expired():
    if not session.get("user_id"):
        return False
    try:
        seen = datetime.fromisoformat(session["last_seen"])
        return utcnow() - seen > current_app.config["SESSION_IDLE_TIMEOUT"]
    except (KeyError, ValueError, TypeError):
        return True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "user", None):
            return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else "/"))
        return view(*args, **kwargs)
    return wrapped


def credential_stamp(user):
    return hmac.new(current_app.secret_key.encode(), user.password_hash.encode(), hashlib.sha256).hexdigest()


def start_session(user):
    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session["credential_stamp"] = credential_stamp(user)
    session["last_seen"] = utcnow().isoformat()


def load_user():
    g.user = None
    if session_expired():
        session.clear()
    if session.get("user_id"):
        g.user = db.session.get(User, session["user_id"])
        if not g.user or not hmac.compare_digest(session.get("credential_stamp", ""), credential_stamp(g.user)):
            session.clear()
            g.user = None
        else:
            session["last_seen"] = utcnow().isoformat()
