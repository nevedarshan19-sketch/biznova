from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import AuditEvent, BusinessSettings, Product, Sale, User, utcnow
from app.security import safe_next_url

TEST_PASSWORD = "Test-only-Owner42!"
NEW_PASSWORD = "Changed-test-Owner43!"


class Inputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and "name" in values:
            self.values[values["name"]] = values.get("value", "")


def csrf_token(response):
    parser = Inputs()
    parser.feed(response.get_data(as_text=True))
    assert parser.values.get("csrf_token"), "Rendered login/form must contain an actual CSRF token"
    return parser.values["csrf_token"]


@pytest.mark.parametrize("path", [
    "/products", "/products/new", "/products/1", "/products/1/edit",
    "/sales", "/sales/new", "/sales/1", "/purchases", "/purchases/new",
    "/purchases/1", "/expenses", "/expenses/new", "/reports", "/reports/export/sales", "/settings",
])
def test_private_read_routes_require_login(client, path):
    response = client.get(path)
    assert response.status_code in (302, 303)
    assert urlsplit(response.headers["Location"]).path == "/auth/login"


def test_public_landing_page_is_available_without_login(client):
    response = client.get("/")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Stop running your shop from a" in body
    assert 'href="/auth/login?next=/"' in body


@pytest.mark.parametrize("path", [
    "/products/new", "/products/1/edit", "/products/1/archive", "/products/1/adjust",
    "/sales/new", "/sales/1/void", "/purchases/new", "/purchases/1/void",
    "/expenses/new", "/expenses/1/edit", "/expenses/1/void", "/settings", "/auth/logout",
])
def test_private_write_routes_require_login(client, path):
    response = client.post(path, data={})
    assert response.status_code in (302, 303)
    assert urlsplit(response.headers["Location"]).path == "/auth/login"


def test_login_session_safe_redirect_logout_and_cookie_flags(app, client, owner):
    with client.session_transaction() as session:
        session["untrusted_before_login"] = "remove me"
    response = client.post("/auth/login", data={
        "username": " OWNER ", "password": TEST_PASSWORD, "next": "https://example.com/escape",
    })
    assert response.status_code == 303 and response.headers["Location"] == "/"
    assert "HttpOnly" in response.headers["Set-Cookie"]
    assert "SameSite=Lax" in response.headers["Set-Cookie"]
    with client.session_transaction() as session:
        assert session["user_id"] == owner
        assert "untrusted_before_login" not in session
    assert client.get("/auth/logout").status_code == 405
    assert client.post("/auth/logout").status_code == 303
    assert client.get("/settings").status_code == 302


@pytest.mark.parametrize("target", [
    "https://example.com", "//example.com", "\\example.com", "/\\example.com",
    "/%2fexample.com", "/%5cexample.com", "/%0aevil", "javascript:alert(1)",
])
def test_next_redirect_rejects_external_and_encoded_destinations(target):
    assert safe_next_url(target) == "/"


def test_safe_internal_next_and_expired_session(client, owner):
    response = client.post("/auth/login", data={
        "username": "owner", "password": TEST_PASSWORD, "next": "/products?stock=low",
    })
    assert response.headers["Location"] == "/products?stock=low"
    with client.session_transaction() as session:
        session["last_seen"] = (utcnow() - timedelta(days=1)).isoformat()
    assert client.get("/products").status_code == 302
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_invalid_login_is_generic_and_rate_limited(app, client, owner):
    bodies = []
    for index in range(app.config["LOGIN_MAX_ATTEMPTS"]):
        response = client.post("/auth/login", data={
            "username": "owner" if index % 2 else "unknown", "password": "Wrong-test-password!",
        })
        assert response.status_code == 401
        bodies.append(response.get_data(as_text=True))
        assert "Invalid username or password" in bodies[-1]
    denied = client.post("/auth/login", data={"username": "owner", "password": TEST_PASSWORD})
    assert denied.status_code == 429
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_csrf_enabled_login_and_mutation_require_rendered_token(app_factory):
    app = app_factory(WTF_CSRF_ENABLED=True)
    with app.app_context():
        user = User(username="owner", display_name="CSRF Test Owner")
        user.set_password(TEST_PASSWORD)
        db.session.add(user)
        db.session.commit()
    client = app.test_client()
    data = {"username": "owner", "password": TEST_PASSWORD, "next": "/"}
    assert client.post("/auth/login", data=data).status_code == 400
    assert client.post("/auth/login", data={**data, "csrf_token": "invalid"}).status_code == 400
    token = csrf_token(client.get("/auth/login"))
    assert client.post("/auth/login", data={**data, "csrf_token": token}).status_code == 303
    assert client.post("/auth/logout").status_code == 400
    assert client.post("/products/new", data={"name": "Missing token"}).status_code == 400
    fresh_token = csrf_token(client.get("/settings"))
    assert client.post("/auth/logout", data={"csrf_token": fresh_token}).status_code == 303


def test_settings_password_requires_current_and_matching_confirmation(app, logged_in, owner):
    form = {
        "action": "password", "old_password": "incorrect",
        "password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD,
    }
    assert logged_in.post("/settings", data=form).status_code == 422
    assert logged_in.post("/settings", data={**form, "old_password": TEST_PASSWORD, "confirm_password": "different"}).status_code == 422
    assert logged_in.post("/settings", data={**form, "old_password": TEST_PASSWORD}).status_code == 303
    with app.app_context():
        user = db.session.get(User, owner)
        assert user.check_password(NEW_PASSWORD) and not user.check_password(TEST_PASSWORD)
        event = db.session.scalar(select(AuditEvent).where(AuditEvent.event_type == "password.changed"))
        assert event is not None
        assert NEW_PASSWORD not in (event.details_json or "") + event.summary
    logged_in.post("/auth/logout")
    assert logged_in.post("/auth/login", data={"username": "owner", "password": TEST_PASSWORD}).status_code == 401
    assert logged_in.post("/auth/login", data={"username": "owner", "password": NEW_PASSWORD}).status_code == 303


def test_settings_validate_and_persist_business_preferences(app, logged_in):
    assert logged_in.post("/settings", data={
        "action": "business", "business_name": "New Test Shop", "currency": "INR",
        "timezone": "Asia/Kolkata", "theme": "dark",
    }).status_code == 303
    with app.app_context():
        settings = db.session.get(BusinessSettings, 1)
        assert settings.business_name == "New Test Shop" and settings.theme == "dark"
    assert logged_in.post("/settings", data={
        "action": "business", "business_name": "Wrong", "currency": "XXX", "timezone": "Invalid/Timezone",
    }).status_code == 422
    with app.app_context():
        assert db.session.get(BusinessSettings, 1).business_name == "New Test Shop"


def test_product_sale_form_lifecycle_and_csv(app, logged_in, product_form, transaction_form):
    response = logged_in.post("/products/new", data=product_form(sku="ROUTE-1", opening_stock="2"))
    assert response.status_code == 303
    product_id = int(response.headers["Location"].rsplit("/", 1)[1])
    invalid = logged_in.post("/sales/new", data=transaction_form(
        "malformed", idempotency_key="bad-route-sale",
    ))
    assert invalid.status_code == 422
    form = transaction_form(product_id, "2", discount="0.01", idempotency_key="route-sale")
    sale = logged_in.post("/sales/new", data=form)
    assert sale.status_code == 303
    assert logged_in.post("/sales/new", data=form).headers["Location"] == sale.headers["Location"]
    assert logged_in.get(sale.headers["Location"]).status_code == 200
    with app.app_context():
        assert db.session.scalar(select(func.count(Sale.id))) == 1
        assert db.session.get(Product, product_id).current_stock == 0
    export = logged_in.get("/reports/export/sales?period=30d")
    assert export.status_code == 200 and export.mimetype == "text/csv"
    assert "attachment" in export.headers["Content-Disposition"]
    assert "20.19" in export.get_data(as_text=True)
    assert logged_in.get("/reports/export/not-a-report").status_code == 404
    assert logged_in.get("/products/not-an-id").status_code == 404
    assert logged_in.post(sale.headers["Location"] + "/void", data={"reason": "Route correction"}).status_code == 303
    assert logged_in.post(sale.headers["Location"] + "/void", data={"reason": "Repeated correction"}).status_code == 409
    with app.app_context():
        assert db.session.get(Product, product_id).current_stock == 2


@pytest.mark.parametrize("path", ["/", "/products", "/sales", "/purchases", "/expenses", "/reports", "/settings"])
def test_authenticated_pages_render_with_security_headers(logged_in, path):
    response = logged_in.get(path)
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"


def test_password_rotation_revokes_other_sessions(app, logged_in, owner):
    other = app.test_client()
    assert other.post('/auth/login', data={'username': 'owner', 'password': TEST_PASSWORD}).status_code == 303
    assert other.get('/settings').status_code == 200
    response = logged_in.post('/settings', data={
        'action': 'password', 'old_password': TEST_PASSWORD,
        'password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD,
    })
    assert response.status_code == 303
    assert logged_in.get('/settings').status_code == 200
    assert other.get('/settings').status_code == 302


def test_login_limit_reserves_concurrent_attempts_atomically(app):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.security import reserve_login_attempt, reset_rate_limits
    reset_rate_limits()
    barrier = Barrier(12)
    def attempt():
        with app.test_request_context('/auth/login'):
            barrier.wait(timeout=10)
            return reserve_login_attempt('concurrent-client')
    with ThreadPoolExecutor(max_workers=12) as pool:
        admitted = list(pool.map(lambda _: attempt(), range(12)))
    assert sum(admitted) == app.config['LOGIN_MAX_ATTEMPTS']


def test_oversized_post_returns_413_without_error_handler_recursion(logged_in):
    response = logged_in.post('/products/new', data={'description': 'x' * (1024 * 1024 + 1)})
    assert response.status_code == 413
    assert '413' in response.text


def test_too_many_transaction_lines_preserve_form_and_retry_key(logged_in):
    data = {'idempotency_key': 'preserved-over-limit', 'customer_name': 'Keep this name', 'business_date': '2026-11-14'}
    for index in range(101):
        data[f'items[{index}][product_id]'] = str(index + 1)
        data[f'items[{index}][quantity]'] = '1'
        data[f'items[{index}][price]'] = '10.00'
    response = logged_in.post('/sales/new', data=data)
    assert response.status_code == 422
    assert 'value="preserved-over-limit"' in response.text
    assert 'value="Keep this name"' in response.text
    assert 'items[100][quantity]' in response.text


def test_tunnel_client_ip_header_trusted_only_when_configured_and_loopback(app):
    from app.security import _rate_key
    headers = {"CF-Connecting-IP": "203.0.113.7"}
    with app.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert _rate_key() == "127.0.0.1"
    app.config["CLIENT_IP_HEADER"] = "CF-Connecting-IP"
    with app.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert _rate_key() == "203.0.113.7"
    with app.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": "198.51.100.9"}):
        assert _rate_key() == "198.51.100.9"


def test_proxy_fix_uses_forwarded_client_ip_and_scheme_only_when_enabled(tmp_path):
    from flask import request
    from app import create_app
    seen = {}
    for hops in (0, 1):
        app = create_app("testing", {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/proxy{hops}.sqlite", "PROXY_FIX_HOPS": hops})
        with app.app_context():
            db.create_all()

        @app.get("/probe-client")
        def probe():
            seen[hops] = (request.remote_addr, request.scheme)
            return ""

        app.test_client().get("/probe-client", headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"}, environ_base={"REMOTE_ADDR": "10.0.0.5"})
    assert seen[0] == ("10.0.0.5", "http")
    assert seen[1] == ("203.0.113.7", "https")
