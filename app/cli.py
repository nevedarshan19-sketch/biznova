"""Explicit local maintenance commands; never invoked during application startup."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import os
import re
import sqlite3

import click
from flask import current_app
from sqlalchemy import delete, select, text

from app.extensions import db
from app.models import (
    AuditEvent, BusinessSettings, Category, Expense, IdempotencyRecord, Product,
    Purchase, PurchaseItem, Sale, SaleItem, StockMovement, User,
)
from app.services import expenses, products, purchases, sales
from app.services.clock import business_today

DEMO_FILENAME = "biznova_demo.sqlite"
DEMO_BUSINESS = "Nova Corner — Fictional Demo"


def _database_path(*, demo=False):
    url = db.engine.url
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise click.ClickException("This command requires an existing file-backed SQLite database.")
    path = Path(url.database).expanduser()
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise click.ClickException("Database does not exist; run db upgrade first.") from exc
    if not canonical.is_file():
        raise click.ClickException("The SQLite database must be a regular file.")
    if demo and (
        current_app.config.get("DEMO_MODE") is not True
        or path.name != DEMO_FILENAME
        or canonical.name != DEMO_FILENAME
    ):
        raise click.ClickException(
            "Refusing: DEMO_MODE must be true and the canonical database filename "
            "must be exactly biznova_demo.sqlite."
        )
    return canonical


def _strong_password(password):
    return (
        12 <= len(password) <= 128
        and any(char.islower() for char in password)
        and any(char.isupper() for char in password)
        and any(char.isdigit() for char in password)
        and any(not char.isalnum() and not char.isspace() for char in password)
    )


def _seed_demo():
    _database_path(demo=True)
    for model in (Product, Sale, Purchase, Expense, StockMovement, IdempotencyRecord):
        if db.session.scalar(select(model.id).limit(1)) is not None:
            raise click.ClickException("Refusing to seed a populated database. Use a fresh demo database.")
    settings = db.session.get(BusinessSettings, 1)
    if settings is None:
        settings = BusinessSettings(id=1)
        db.session.add(settings)
    settings.business_name = DEMO_BUSINESS
    settings.currency = "INR"
    settings.timezone = "Asia/Kolkata"
    db.session.commit()
    today = business_today("Asia/Kolkata")
    # All catalog and financial writes use the same services as the web forms.
    # Names, parties and transactions below are fictional; no account is seeded.
    catalog = [
        ("NOVA-001", "Cedar Notebook A5", "Stationery", "pc", "32.50", "55.00", 60),
        ("NOVA-002", "Orbit Blue Pen", "Stationery", "pc", "6.25", "12.00", 100),
        ("NOVA-003", "Meadow Tea 100g", "Pantry", "pack", "65.00", "95.00", 45),
        ("NOVA-004", "Sunrise Coffee 100g", "Pantry", "pack", "105.50", "149.00", 40),
        ("NOVA-005", "Willow Oat Biscuits", "Snacks", "pack", "24.00", "39.50", 55),
        ("NOVA-006", "Pebble Trail Mix", "Snacks", "pack", "48.75", "75.00", 45),
        ("NOVA-007", "Cloud Cotton Cloth", "Household", "pc", "22.00", "38.00", 50),
        ("NOVA-008", "Fern Dish Soap 250ml", "Household", "bottle", "38.25", "59.00", 45),
        ("NOVA-009", "Harbor Hand Wash", "Personal Care", "bottle", "52.00", "89.00", 40),
        ("NOVA-010", "Comet Paper Clips", "Stationery", "box", "14.00", "25.00", 40),
        ("NOVA-011", "Dawn Reusable Tote", "Accessories", "pc", "45.00", "79.00", 2),
        ("NOVA-012", "Maple Bookmark Set", "Accessories", "set", "18.50", "35.00", 0),
    ]
    product_ids = []
    for sku, name, category, unit, cost, price, opening in catalog:
        product = products.create_product({
            "sku": sku, "name": name, "category": category, "unit": unit,
            "purchase_price": cost, "selling_price": price,
            "opening_stock": str(opening), "low_stock_threshold": "5",
            "description": "Fictional demonstration product; not a real brand.",
        }, None)
        product_ids.append(product.id)
    for index, days_ago in enumerate((29, 19, 9)):
        purchases.create_purchase({
            "supplier_name": "Imaginary Meadow Supply",
            "business_date": (today - timedelta(days=days_ago)).isoformat(),
            "notes": "Fictional demo restock",
            "items[0][product_id]": str(product_ids[index]),
            "items[0][quantity]": str(10 + index * 5),
            "items[0][cost]": catalog[index][4],
        }, None, f"demo-purchase-{index}")
    for index in range(30):
        first, second = index % 10, (index + 3) % 10
        sales.create_sale({
            "customer_name": ("Fictional walk-in", "Fictional campus club", "Fictional neighbor")[index % 3],
            "business_date": (today - timedelta(days=29 - index)).isoformat(),
            "notes": "Fictional demo sale",
            "discount": "5.00" if index % 4 == 0 else "0.00",
            "items[0][product_id]": str(product_ids[first]),
            "items[0][quantity]": str(1 + index % 3),
            "items[0][price]": catalog[first][5],
            "items[1][product_id]": str(product_ids[second]),
            "items[1][quantity]": "1",
            "items[1][price]": catalog[second][5],
        }, None, f"demo-sale-{index}")
    for index, (category, amount, days_ago) in enumerate((
        ("Rent", "2500.00", 28), ("Utilities", "425.75", 22),
        ("Transport", "180.00", 15), ("Packaging", "135.50", 7),
        ("Cleaning", "90.25", 2),
    )):
        expenses.create_expense({
            "category": category, "amount": amount,
            "business_date": (today - timedelta(days=days_ago)).isoformat(),
            "payment_method": "Cash", "description": f"Fictional demo expense {index + 1}",
        }, None)


def register_cli(app):
    """Register explicit operator commands on a Flask application."""

    @app.cli.command("create-owner")
    @click.option("--username", prompt=True)
    @click.option("--display-name", prompt=True)
    @click.password_option(confirmation_prompt=True)
    def create_owner(username, display_name, password):
        """Create the sole owner account (no default credentials)."""
        username = username.strip().lower()
        display_name = display_name.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}", username):
            raise click.ClickException("Username must be 3–40 letters, digits, dots, underscores or dashes.")
        if not display_name or len(display_name) > 120:
            raise click.ClickException("Display name must be 1–120 characters.")
        if not _strong_password(password):
            raise click.ClickException(
                "Password must be 12–128 characters with uppercase, lowercase, a digit and a symbol."
            )
        try:
            # Serialize the check and insert, including concurrent CLI invocations.
            db.session.execute(text("BEGIN IMMEDIATE"))
            if db.session.scalar(select(User.id).limit(1)) is not None:
                raise click.ClickException("An owner already exists. Only one owner account is supported.")
            owner = User(username=username, display_name=display_name)
            owner.set_password(password)
            db.session.add(owner)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        click.echo(f"Owner '{username}' created. No additional accounts can be created.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Seed fictional data only into an empty, explicitly configured demo DB."""
        try:
            _seed_demo()
        except click.ClickException:
            db.session.rollback()
            raise
        except Exception as exc:
            db.session.rollback()
            raise click.ClickException(
                "Demo seed stopped. Earlier service transactions may have committed; "
                "inspect the demo DB, then explicitly reset-demo before retrying."
            ) from exc
        click.echo(f"Seeded {DEMO_BUSINESS}: 12 products, 30 sales, 3 purchases, 5 expenses.")
        click.echo("No credentials were created. Run create-owner separately if needed.")

    @app.cli.command("reset-demo")
    @click.option("--confirm", required=True, help="Must be exactly RESET-DEMO.")
    def reset_demo(confirm):
        """Delete demo business data transactionally; preserve schema and owner."""
        if confirm != "RESET-DEMO":
            raise click.ClickException("Refusing: --confirm must equal RESET-DEMO exactly.")
        _database_path(demo=True)
        try:
            db.session.execute(text("BEGIN IMMEDIATE"))
            for model in (
                AuditEvent, IdempotencyRecord, StockMovement, SaleItem, PurchaseItem,
                Sale, Purchase, Expense, Product, Category, BusinessSettings,
            ):
                db.session.execute(delete(model))
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        click.echo("Demo business data reset. Owner, schema and database file preserved.")

    @app.cli.command("backup")
    @click.argument("path", type=click.Path(path_type=Path, dir_okay=False))
    def backup(path):
        """Create a consistent SQLite backup at a NEW destination PATH."""
        source = _database_path()
        destination = path.expanduser().absolute()
        if not destination.parent.is_dir():
            raise click.ClickException("Backup destination parent directory must already exist.")
        try:
            descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise click.ClickException("Backup destination already exists; refusing to overwrite.") from exc
        except OSError as exc:
            raise click.ClickException(f"Cannot create backup: {exc}") from exc
        identity = os.fstat(descriptor)
        os.close(descriptor)
        try:
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
                with closing(sqlite3.connect(str(destination))) as writer:
                    reader.backup(writer)
            click.echo(f"Backup created: {destination}")
        except Exception as exc:
            if destination.exists() and destination.stat().st_ino == identity.st_ino:
                destination.unlink()
            raise click.ClickException(f"Backup failed: {exc}") from exc

    @app.cli.command("reconcile-stock")
    def reconcile_stock():
        """Read-only check of ledger sums, every running balance and stock totals."""
        problems = []
        checked = 0
        # Explicit BEGIN gives both SELECTs a consistent SQLite read snapshot.
        with db.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            balances = dict(connection.execute(select(Product.id, Product.current_stock)).all())
            running = {product_id: 0 for product_id in balances}
            for row in connection.execute(select(
                StockMovement.id, StockMovement.product_id,
                StockMovement.quantity_change, StockMovement.balance_after,
            ).order_by(StockMovement.product_id, StockMovement.id)):
                checked += 1
                if row.product_id not in running:
                    problems.append(f"Movement {row.id}: orphan product {row.product_id}.")
                    continue
                running[row.product_id] += row.quantity_change
                expected = running[row.product_id]
                if expected < 0 or expected != row.balance_after:
                    problems.append(
                        f"Movement {row.id}, product {row.product_id}: "
                        f"ledger balance {row.balance_after}, expected {expected}."
                    )
            for product_id, stock in balances.items():
                if running[product_id] != stock:
                    problems.append(
                        f"Product {product_id}: current stock {stock}, ledger sum {running[product_id]}."
                    )
            connection.rollback()
        for problem in problems:
            click.echo(problem, err=True)
        if problems:
            raise click.ClickException(f"Stock reconciliation failed: {len(problems)} inconsistency(s).")
        click.echo(f"Stock reconciled: {len(balances)} products, {checked} movements; no inconsistencies.")
