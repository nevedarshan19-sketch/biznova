"""Disposable file-backed databases; never open the operator's configured DB."""
from uuid import uuid4

import pytest

from app import create_app
from app.extensions import db
from app.models import BusinessSettings, User
from app.security import reset_rate_limits
from app.services import products
from app.services.clock import business_today

TEST_PASSWORD = "Test-only-Owner42!"


@pytest.fixture
def app_factory(tmp_path):
    apps = []

    def factory(**overrides):
        filename = overrides.pop("filename", f"test-{len(apps)}.sqlite")
        app = create_app("testing", {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / filename}",
            "WTF_CSRF_ENABLED": False,
            "DEMO_MODE": False,
            "LOGIN_MAX_ATTEMPTS": 3,
            "LOGIN_WINDOW_SECONDS": 600,
            **overrides,
        })
        with app.app_context():
            db.create_all()
            db.session.add(BusinessSettings(id=1, business_name="Test-only Shop"))
            db.session.commit()
        apps.append(app)
        return app

    reset_rate_limits()
    yield factory
    for app in apps:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
    reset_rate_limits()


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def owner(app):
    with app.app_context():
        user = User(username="owner", display_name="Test Owner")
        user.set_password(TEST_PASSWORD)
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def logged_in(client, owner):
    response = client.post("/auth/login", data={
        "username": "owner", "password": TEST_PASSWORD, "next": "/",
    })
    assert response.status_code in (302, 303)
    return client


@pytest.fixture
def product_form():
    def build(**overrides):
        return {
            "sku": "TEST-" + uuid4().hex[:12], "name": "Test Notebook",
            "category": "Stationery", "unit": "pc", "purchase_price": "4.25",
            "selling_price": "10.10", "opening_stock": "10",
            "low_stock_threshold": "2", "description": "Test-only data", **overrides,
        }
    return build


@pytest.fixture
def make_product(ctx, product_form):
    def create(**overrides):
        return products.create_product(product_form(**overrides), None)
    return create


@pytest.fixture
def transaction_form():
    def build(product_id, quantity="1", **overrides):
        return {
            "items[0][product_id]": str(product_id),
            "items[0][quantity]": str(quantity),
            "business_date": business_today("Asia/Kolkata").isoformat(), **overrides,
        }
    return build
