"""Real SQLite file + independent sessions; barriers precede service calls."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import IdempotencyRecord, Product, Purchase, Sale, StockMovement
from app.services import products, purchases, sales
from app.services.errors import ConflictError


def race(app, calls):
    barrier = Barrier(len(calls), timeout=10)

    def execute(call):
        with app.app_context():
            # Context-local scoped sessions and separate thread connections.
            # Do not acquire BEGIN IMMEDIATE until both workers reach this point.
            barrier.wait()
            try:
                return ("ok", call())
            except ConflictError as exc:
                return ("conflict", type(exc).__name__)
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(execute, call) for call in calls]
        return [future.result(timeout=30) for future in futures]


def setup_product(app, product_form, stock):
    with app.app_context():
        assert db.engine.url.database.endswith(".sqlite")
        product = products.create_product(product_form(opening_stock=str(stock)), None)
        return product.id


def assert_balances(product_id, expected_stock):
    assert db.session.get(Product, product_id).current_stock == expected_stock
    balance = 0
    for row in db.session.scalars(select(StockMovement).where(
        StockMovement.product_id == product_id,
    ).order_by(StockMovement.id)):
        balance += row.quantity_change
        assert row.balance_after == balance
        assert balance >= 0
    assert balance == expected_stock


def test_two_simultaneous_sales_cannot_oversell(app, product_form, transaction_form):
    product_id = setup_product(app, product_form, 5)
    form = transaction_form(product_id, "4")

    def sell(key):
        sale, created = sales.create_sale(dict(form), None, key)
        return sale.id, created

    results = race(app, [lambda: sell("race-a"), lambda: sell("race-b")])
    assert sorted(result[0] for result in results) == ["conflict", "ok"]
    with app.app_context():
        assert db.session.scalar(select(func.count(Sale.id))) == 1
        assert db.session.scalar(select(func.count(IdempotencyRecord.id))) == 1
        assert_balances(product_id, 1)


def test_simultaneous_same_idempotency_key_creates_once(app, product_form, transaction_form):
    product_id = setup_product(app, product_form, 5)
    form = transaction_form(product_id, "3")

    def sell():
        sale, created = sales.create_sale(dict(form), None, "same-racing-key")
        return sale.id, created

    results = race(app, [sell, sell])
    assert [result[0] for result in results] == ["ok", "ok"]
    assert results[0][1][0] == results[1][1][0]
    assert sorted(result[1][1] for result in results) == [False, True]
    with app.app_context():
        assert db.session.scalar(select(func.count(Sale.id))) == 1
        assert db.session.scalar(select(func.count(IdempotencyRecord.id))) == 1
        assert_balances(product_id, 2)


@pytest.mark.parametrize("kind", ["sale", "purchase"])
def test_simultaneous_voids_reverse_stock_exactly_once(app, product_form, transaction_form, kind):
    product_id = setup_product(app, product_form, 5)
    with app.app_context():
        create = sales.create_sale if kind == "sale" else purchases.create_purchase
        transaction, _ = create(transaction_form(product_id, "3"), None, "void-race-source")
        transaction_id = transaction.id

    def void():
        operation = sales.void_sale if kind == "sale" else purchases.void_purchase
        return operation(transaction_id, "Concurrent correction", None).id

    results = race(app, [void, void])
    assert sorted(result[0] for result in results) == ["conflict", "ok"]
    with app.app_context():
        model = Sale if kind == "sale" else Purchase
        assert db.session.get(model, transaction_id).status == "voided"
        assert db.session.scalar(select(func.count(StockMovement.id)).where(
            StockMovement.movement_type == f"{kind}_void",
        )) == 1
        assert_balances(product_id, 5)
