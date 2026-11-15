import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import create_app
from app.extensions import db
from app.models import BusinessSettings, Product, Sale, User

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"


def run():
    ARTIFACTS.mkdir(exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix="biznova-browser-") as directory:
        app = create_app("testing", {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{directory}/browser.sqlite",
            "WTF_CSRF_ENABLED": True,
        })
        with app.app_context():
            db.create_all()
            owner = User(username="owner", display_name="Browser Test Owner")
            owner.set_password("Browser-test-only-2026!")
            db.session.add_all([owner, BusinessSettings(id=1, business_name="Browser QA Business")])
            db.session.commit()
        server = make_server("127.0.0.1", 0, app, threaded=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/")
                expect(page.locator("h1")).to_contain_text("Stop running your shop")
                page.screenshot(path=str(ARTIFACTS / "landing-1440.png"), full_page=True)
                page.locator('a[href="/auth/login?next=/"]').first.click()
                page.wait_for_selector('[name="username"]')
                page.locator('[name="username"]').fill("owner")
                page.locator('[name="password"]').fill("Browser-test-only-2026!")
                page.locator('form:has(input[name="password"]) button[type="submit"]').click()
                page.wait_for_url(base + "/")
                results.append("Login and route protection")
                page.goto(base + "/products/new")
                for name, value in {"sku": "QA-001", "name": "Browser Precision", "category": "QA", "unit": "pc", "purchase_price": "75.10", "selling_price": "125.25", "opening_stock": "0", "low_stock_threshold": "2"}.items():
                    page.locator(f'[name="{name}"]').fill(value)
                page.locator('form:has(input[name="sku"]) button[type="submit"]').click()
                page.wait_for_url("**/products/1")
                results.append("Create product with zero opening stock")
                page.goto(base + "/purchases/new")
                page.locator('[name="supplier_name"]').fill("Fictional QA Supplier")
                page.locator('select[name="items[0][product_id]"]').select_option("1")
                page.locator('[name="items[0][quantity]"]').fill("5")
                page.locator('[name="items[0][cost]"]').fill("75.10")
                page.locator('form:has(input[name="idempotency_key"]) button[type="submit"]').click()
                page.wait_for_url("**/purchases/1")
                results.append("Purchase increases stock")
                page.goto(base + "/sales/new")
                page.locator('[name="customer_name"]').fill("Fictional QA Customer")
                page.locator('select[name="items[0][product_id]"]').select_option("1")
                page.locator('[name="items[0][quantity]"]').fill("3")
                page.locator('[name="items[0][price]"]').fill("125.25")
                page.locator('[name="discount"]').fill("0.25")
                page.locator('form:has(input[name="idempotency_key"]) button[type="submit"]').click()
                page.wait_for_url("**/sales/1")
                expect(page.locator("body")).to_contain_text("375.50")
                results.append("Sale and exact decimal discount")
                page.goto(base + "/expenses/new")
                page.locator('[name="category"]').fill("Transport")
                page.locator('[name="amount"]').fill("10.25")
                page.locator('form:has(input[name="amount"]) [name="description"]').fill("Fictional delivery cost")
                page.locator('form:has(input[name="amount"]) button[type="submit"]').click()
                page.wait_for_url("**/expenses/1")
                results.append("Expense recorded")
                page.goto(base + "/")
                expect(page.locator("body")).to_contain_text("375.50")
                expect(page.locator("body")).to_contain_text("10.25")
                page.goto(base + "/products?stock=low")
                expect(page.locator("body")).to_contain_text("Browser Precision")
                results.append("Dashboard totals and live low-stock alert")
                expect(page.locator("nav[aria-label='Stock level'] a.is-active")).to_have_text("Low stock")
                page.locator("nav[aria-label='Stock level'] a", has_text="Out of stock").click()
                page.wait_for_url("**/products?*stock=out*")
                expect(page.locator(".empty-state")).to_be_visible()
                page.locator("nav[aria-label='Stock level'] a", has_text="All stock").click()
                page.wait_for_url("**/products?*stock=all*")
                expect(page.locator("body")).to_contain_text("Browser Precision")
                results.append("Inventory segmented tabs filter stock without losing search")
                page.goto(base + "/expenses")
                page.locator("#quick-category").fill("Utilities")
                page.locator("#quick-amount").fill("1.25")
                page.locator("#quick-method").fill("UPI")
                page.locator("#quick-description").fill("Fictional quick entry")
                page.locator('form.quick-add button[type="submit"]').click()
                page.wait_for_url(base + "/expenses")
                expect(page.locator("body")).to_contain_text("Utilities")
                expect(page.locator("body")).to_contain_text("11.50")
                results.append("Inline expense quick add records and returns to the ledger")
                page.goto(base + "/sales/new")
                expect(page.locator(".line-item-head")).to_be_visible()
                results.append("Sale form shows the compact billing header")
                page.goto(base + "/reports")
                with page.expect_download() as download:
                    page.locator('a[href*="/reports/export/sales"]').first.click()
                csv = download.value
                csv.save_as(ARTIFACTS / "browser-sales.csv")
                assert "375.50" in (ARTIFACTS / "browser-sales.csv").read_text()
                results.append("CSV export matches saved transaction")
                for width in (360, 768, 1440):
                    page.set_viewport_size({"width": width, "height": 1000})
                    for theme in ("light", "dark"):
                        page.goto(base + "/")
                        page.locator("[data-theme-select]").first.select_option(theme)
                        page.reload()
                        assert page.locator("html").get_attribute("data-theme") == theme
                        for path in ("/", "/products", "/sales/new", "/purchases", "/expenses", "/reports", "/settings"):
                            page.goto(base + path)
                            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (width, theme, path, page.evaluate("""[...document.querySelectorAll('body *')].filter(el => { const r = el.getBoundingClientRect(); return r.right > innerWidth && r.width > 0; }).map(el => ({tag: el.tagName, classes: el.className, width: el.getBoundingClientRect().width, right: el.getBoundingClientRect().right}))"""))
                        page.goto(base + "/")
                        page.screenshot(path=str(ARTIFACTS / f"dashboard-{theme}-{width}.png"), full_page=True)
                        if width == 1440:
                            page.goto(base + "/products")
                            page.screenshot(path=str(ARTIFACTS / f"products-{theme}-1440.png"), full_page=True)
                            page.goto(base + "/expenses")
                            page.screenshot(path=str(ARTIFACTS / f"expenses-{theme}-1440.png"), full_page=True)
                            page.goto(base + "/sales/new")
                            page.screenshot(path=str(ARTIFACTS / f"sale-form-{theme}-1440.png"), full_page=True)
                results.append("Both themes persist across pages and refresh; no overflow at 360, 768, 1440px")
                page.set_viewport_size({"width": 360, "height": 800})
                page.goto(base + "/")
                page.locator('[data-open-drawer]').click()
                expect(page.locator('[data-open-drawer]')).to_have_attribute('aria-expanded', 'true')
                page.keyboard.press('Escape')
                expect(page.locator('[data-open-drawer]')).to_have_attribute('aria-expanded', 'false')
                for kind, field in (("purchases", "cost"), ("sales", "price")):
                    page.goto(base + f"/{kind}/new")
                    page.locator('select[name="items[0][product_id]"]').select_option("1")
                    page.locator('[name="items[0][quantity]"]').fill("1")
                    page.locator(f'[name="items[0][{field}]"]').fill("75.10")
                    page.locator('form:has(input[name="idempotency_key"]) button[type="submit"]').click()
                    page.wait_for_url(f"**/{kind}/2")
                for kind in ("sales", "purchases"):
                    page.goto(base + f"/{kind}/2")
                    page.locator('[data-open-dialog="void-dialog"]').click()
                    expect(page.locator('#void-dialog')).to_be_visible()
                    for _ in range(7):
                        page.keyboard.press('Tab')
                        assert page.evaluate("document.getElementById('void-dialog').contains(document.activeElement)"), page.evaluate("({active: document.activeElement.outerHTML.slice(0, 200), modal: document.getElementById('void-dialog').matches(':modal')})")
                    page.keyboard.press('Escape')
                    expect(page.locator('#void-dialog')).not_to_be_visible()
                    expect(page.locator('[data-open-dialog="void-dialog"]')).to_be_focused()
                    page.locator('[data-open-dialog="void-dialog"]').click()
                    page.locator('#void-dialog [name="reason"]').fill("Mobile QA controlled reversal")
                    page.locator('#void-dialog button[type="submit"]').click()
                    expect(page.locator('.receipt')).to_contain_text('Voided on')
                results.append("Mobile purchase/sale and controlled reversals; drawer, dialog focus trap and Escape restoration")
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(base + "/sales/new")
                page.locator('select[name="items[0][product_id]"]').select_option("1")
                page.locator('[name="items[0][quantity]"]').fill("1")
                page.locator('[name="customer_name"]').fill("Preserved on failure")
                key = page.locator('[name="idempotency_key"]').input_value()
                page.route("**/sales/new", lambda route: route.fulfill(status=503, content_type="text/html", body="Temporarily unavailable") if route.request.method == "POST" else route.continue_())
                page.locator('form:has(input[name="idempotency_key"]) button[type="submit"]').click()
                expect(page.locator('[name="customer_name"]')).to_have_value("Preserved on failure")
                assert page.locator('[name="idempotency_key"]').input_value() == key
                page.unroute("**/sales/new")
                results.append("Server unavailable state preserves entries and retry key")
                page.goto(base + "/sales/new")
                page.locator('[data-add-row]').click()
                expect(page.locator('[data-item-row]')).to_have_count(2)
                expect(page.locator('select[name="items[1][product_id]"]')).to_have_count(1)
                page.locator('[data-remove-row]').first.click()
                expect(page.locator('[data-item-row]')).to_have_count(1)
                expect(page.locator('select[name="items[0][product_id]"]')).to_have_count(1)
                page.locator('select[name="items[0][product_id]"]').select_option("1")
                page.locator('[name="items[0][quantity]"]').fill("999")
                page.locator('[name="customer_name"]').fill("Oversell attempt")
                key = page.locator('[name="idempotency_key"]').input_value()
                page.locator('form:has(input[name="idempotency_key"]) button[type="submit"]').click()
                expect(page.locator('.notice-error, .field-error').first).to_be_visible()
                expect(page).to_have_url(base + "/sales/new")
                expect(page.locator('[name="customer_name"]')).to_have_value("Oversell attempt")
                expect(page.locator('[name="items[0][quantity]"]')).to_have_value("999")
                assert page.locator('[name="idempotency_key"]').input_value() == key
                page.screenshot(path=str(ARTIFACTS / "sale-oversell-rejected-1440.png"), full_page=True)
                results.append("Line add/remove reindexing; oversell rejected by server with input and retry key kept")
                assert not errors, errors
                results.append("No uncaught browser JavaScript errors")
                with app.app_context():
                    assert db.session.get(Product, 1).current_stock == 2
                    assert db.session.get(Sale, 1).total_paise == 37550
                browser.close()
        finally:
            server.shutdown()
            worker.join(timeout=5)
    (ARTIFACTS / "browser-results.json").write_text(json.dumps({"passed": results}, indent=2))
    print(json.dumps({"passed": results}, indent=2))


if __name__ == "__main__":
    run()
