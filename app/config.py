import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or (
        f"sqlite:///{BASE_DIR / 'instance' / ('biznova_demo.sqlite' if DEMO_MODE else 'biznova.sqlite')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15, "check_same_thread": False}}
    MAX_CONTENT_LENGTH = 1024 * 1024
    MAX_FORM_PARTS = 600
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_IDLE_TIMEOUT = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = False
    WTF_CSRF_TIME_LIMIT = 8 * 60 * 60
    SQLITE_BUSY_TIMEOUT_MS = 15000
    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_WINDOW_SECONDS = 600
    CLIENT_IP_HEADER = os.environ.get("CLIENT_IP_HEADER", "")
    # Set only when every request arrives through that many trusted reverse proxies (e.g. 1 on Render).
    PROXY_FIX_HOPS = int(os.environ.get("PROXY_FIX_HOPS", "0"))
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key-not-for-production"
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    DEMO_MODE = False
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True


CONFIGS = {"development": Config, "testing": TestingConfig, "production": ProductionConfig}


def get_config(name=None):
    key = name or os.environ.get("BIZNOVA_ENV", "development")
    if key not in CONFIGS:
        raise RuntimeError("BIZNOVA_ENV must be development, testing or production.")
    return CONFIGS[key]
