from pathlib import Path
import sqlite3

from sqlalchemy import func, select, text, update

from app.extensions import db
from app.models import (
    BusinessSettings, Expense, Product, Purchase, Sale, StockMovement, User,
)
from app.services import products

TEST_PASSWORD = "Test-only-Owner42!"


def test_create_owner_prompts_confirms_hashes_and_refuses_second(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-owner"], input=f"owner\nTest Owner\n{TEST_PASSWORD}\n{TEST_PASSWORD}\n")
    assert result.exit_code == 0, result.output
    with app.app_context():
        owner = db.session.scalar(select(User))
        assert owner.username == "owner"
        assert owner.password_hash != TEST_PASSWORD
        assert owner.check_password(TEST_PASSWORD)
    again = runner.invoke(args=["create-owner", "--username", "second", "--display-name", "Second", "--password", TEST_PASSWORD])
    assert again.exit_code != 0 and "already exists" in again.output
    with app.app_context():
        assert db.session.scalar(select(func.count(User.id))) == 1


def test_create_owner_rejects_weak_password_and_mismatched_confirmation(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-owner", "--username", "owner", "--display-name", "Owner", "--password", "weak"])
    assert result.exit_code != 0 and "12" in result.output
    result = runner.invoke(args=["create-owner", "--username", "owner", "--display-name", "Owner"], input=f"{TEST_PASSWORD}\ndifferent-password\n")
    assert result.exit_code != 0
    with app.app_context():
        assert db.session.scalar(select(func.count(User.id))) == 0


def test_seed_requires_mode_and_exact_database_name(app_factory):
    for config in (
        {"DEMO_MODE": False, "filename": "biznova_demo.sqlite"},
        {"DEMO_MODE": True, "filename": "real.sqlite"},
        {"DEMO_MODE": True, "filename": "biznova_demo.sqlite.bak"},
    ):
        app = app_factory(**config)
        for args in (["seed-demo"], ["reset-demo", "--confirm", "RESET-DEMO"]):
            result = app.test_cli_runner().invoke(args=args)
            assert result.exit_code != 0 and "Refusing" in result.output
        with app.app_context():
            assert db.session.scalar(select(func.count(Product.id))) == 0
            assert db.session.get(BusinessSettings, 1).business_name == "Test-only Shop"


def test_demo_alias_cannot_reset_real_database(app_factory, tmp_path):
    real = tmp_path / "actual-business.sqlite"
    with sqlite3.connect(real):
        pass
    (tmp_path / "biznova_demo.sqlite").symlink_to(real)
    app = app_factory(DEMO_MODE=True, filename="biznova_demo.sqlite")
    for args in (["seed-demo"], ["reset-demo", "--confirm", "RESET-DEMO"]):
        result = app.test_cli_runner().invoke(args=args)
        assert result.exit_code != 0 and "Refusing" in result.output
    assert real.exists()


def test_demo_seed_fictional_data_no_credentials_and_reset_preserves_owner(app_factory):
    app = app_factory(DEMO_MODE=True, filename="biznova_demo.sqlite")
    runner = app.test_cli_runner()
    seeded = runner.invoke(args=["seed-demo"])
    assert seeded.exit_code == 0, (seeded.output, seeded.exception)
    with app.app_context():
        assert db.session.scalar(select(func.count(User.id))) == 0
        assert db.session.scalar(select(func.count(Product.id))) >= 10
        assert db.session.scalar(select(func.count(Sale.id))) == 30
        assert db.session.scalar(select(func.count(Purchase.id))) >= 1
        assert db.session.scalar(select(func.count(Expense.id))) >= 1
        assert db.session.scalar(select(func.count(func.distinct(Sale.business_date)))) == 30
        assert db.session.scalar(select(func.count(Product.id)).where(Product.current_stock == 0)) >= 1
        assert db.session.scalar(select(func.count(Product.id)).where(
            Product.current_stock > 0, Product.current_stock <= Product.low_stock_threshold,
        )) >= 1
        assert db.session.get(BusinessSettings, 1).business_name == "Nova Corner — Fictional Demo"
        filename = Path(db.engine.url.database)
    reconcile = runner.invoke(args=["reconcile-stock"])
    assert reconcile.exit_code == 0, reconcile.output
    assert runner.invoke(args=["seed-demo"]).exit_code != 0
    assert runner.invoke(args=["reset-demo", "--confirm", "reset-demo"]).exit_code != 0
    with app.app_context():
        assert db.session.scalar(select(func.count(Sale.id))) == 30
    created = runner.invoke(args=["create-owner", "--username", "owner", "--display-name", "Owner", "--password", TEST_PASSWORD])
    assert created.exit_code == 0, created.output
    reset = runner.invoke(args=["reset-demo", "--confirm", "RESET-DEMO"])
    assert reset.exit_code == 0, reset.output
    assert filename.is_file()
    with app.app_context():
        for model in (Product, Sale, Purchase, Expense, StockMovement):
            assert db.session.scalar(select(func.count(model.id))) == 0
        assert db.session.scalar(select(func.count(User.id))) == 1
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []
    assert runner.invoke(args=["seed-demo"]).exit_code == 0


def test_demo_reset_failure_is_transactional(app_factory, product_form):
    app = app_factory(DEMO_MODE=True, filename="biznova_demo.sqlite")
    with app.app_context():
        products.create_product(product_form(), None)
        db.session.execute(text("CREATE TRIGGER reject_reset BEFORE DELETE ON products BEGIN SELECT RAISE(ABORT, 'forced failure'); END"))
        db.session.commit()
    result = app.test_cli_runner().invoke(args=["reset-demo", "--confirm", "RESET-DEMO"])
    assert result.exit_code != 0
    with app.app_context():
        assert db.session.scalar(select(func.count(Product.id))) == 1
        assert db.session.scalar(select(func.count(StockMovement.id))) == 1


def test_backup_uses_new_file_and_contains_committed_data(app, product_form, tmp_path):
    with app.app_context():
        product = products.create_product(product_form(), None)
        sku = product.sku
    destination = tmp_path / "safe-backup.sqlite"
    runner = app.test_cli_runner()
    result = runner.invoke(args=["backup", str(destination)])
    assert result.exit_code == 0, result.output
    with sqlite3.connect(destination) as saved:
        assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert saved.execute("SELECT sku FROM products").fetchone()[0] == sku
    before = destination.read_bytes()
    refusal = runner.invoke(args=["backup", str(destination)])
    assert refusal.exit_code != 0 and "already exists" in refusal.output
    assert destination.read_bytes() == before


def test_reconcile_detects_bad_running_balance_even_when_sum_matches(app, product_form):
    with app.app_context():
        products.create_product(product_form(), None)
    runner = app.test_cli_runner()
    assert runner.invoke(args=["reconcile-stock"]).exit_code == 0
    with app.app_context():
        db.session.execute(update(StockMovement).values(balance_after=9))
        db.session.commit()
    result = runner.invoke(args=["reconcile-stock"])
    assert result.exit_code != 0 and "expected 10" in result.output


def test_reconcile_detects_stock_sum_mismatch(app, product_form):
    with app.app_context():
        products.create_product(product_form(), None)
        db.session.execute(update(Product).values(current_stock=11))
        db.session.commit()
    result = app.test_cli_runner().invoke(args=["reconcile-stock"])
    assert result.exit_code != 0 and "ledger sum 10" in result.output
