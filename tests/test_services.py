import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    AuditEvent, Expense, IdempotencyRecord, Product, Purchase, PurchaseItem,
    Sale, SaleItem, StockMovement,
)
from app.services import audit, expenses, products, purchases, sales
from app.services.errors import ConflictError, StockConflictError, ValidationError


def count(model):
    return db.session.scalar(select(func.count()).select_from(model))


def ledger(product_id):
    rows = db.session.scalars(select(StockMovement).where(
        StockMovement.product_id == product_id,
    ).order_by(StockMovement.id)).all()
    running = 0
    for row in rows:
        running += row.quantity_change
        assert running == row.balance_after
        assert running >= 0
    assert running == db.session.get(Product, product_id).current_stock
    return rows


def test_product_normalization_uniqueness_and_whole_opening(make_product, product_form):
    product = make_product(sku="  note-001  ", opening_stock="7")
    assert product.sku == "NOTE-001"
    assert product.purchase_price_paise == 425
    assert product.selling_price_paise == 1010
    assert ledger(product.id)[0].quantity_change == 7
    with pytest.raises(ValidationError):
        products.create_product(product_form(sku="note-001"), None)
    assert count(Product) == 1
    assert count(StockMovement) == 1


@pytest.mark.parametrize("field,value", [
    ("opening_stock", "1.2"), ("opening_stock", "-1"),
    ("opening_stock", "1000000001"), ("low_stock_threshold", "2.5"),
    ("selling_price", "1.001"), ("purchase_price", "-0.01"),
])
def test_product_rejects_invalid_quantities_and_money(ctx, product_form, field, value):
    with pytest.raises(ValidationError):
        products.create_product(product_form(**{field: value}), None)
    assert count(Product) == count(StockMovement) == count(AuditEvent) == 0


def test_sale_exact_stock_exact_discount_and_idempotent_retry(make_product, transaction_form):
    product = make_product(opening_stock="3")
    form = transaction_form(product.id, "3", discount="0.01")
    sale, created = sales.create_sale(form, None, "exact-sale")
    assert created
    assert (sale.subtotal_paise, sale.discount_paise, sale.total_paise) == (3030, 1, 3029)
    assert product.current_stock == 0
    assert sale.items[0].line_total_paise == 3030
    replay, created = sales.create_sale(form, None, "exact-sale")
    assert not created and replay.id == sale.id
    assert count(Sale) == count(SaleItem) == count(IdempotencyRecord) == 1
    assert len(ledger(product.id)) == 2
    with pytest.raises(ConflictError):
        sales.create_sale(transaction_form(product.id, "2"), None, "exact-sale")
    assert count(Sale) == 1


def test_oversell_rolls_back_all_lines_and_key(make_product, transaction_form):
    first = make_product(opening_stock="5")
    second = make_product(opening_stock="1")
    form = transaction_form(first.id, "2", **{
        "items[1][product_id]": str(second.id), "items[1][quantity]": "2",
    })
    with pytest.raises(StockConflictError):
        sales.create_sale(form, None, "oversell")
    assert first.current_stock == 5 and second.current_stock == 1
    assert count(Sale) == count(SaleItem) == count(IdempotencyRecord) == 0
    assert len(ledger(first.id)) == len(ledger(second.id)) == 1


@pytest.mark.parametrize("service", [sales.create_sale, purchases.create_purchase])
def test_duplicate_transaction_lines_rejected(make_product, transaction_form, service):
    product = make_product()
    form = transaction_form(product.id, **{
        "items[1][product_id]": str(product.id), "items[1][quantity]": "1",
    })
    with pytest.raises(ValidationError):
        service(form, None, str(uuid4()))
    assert product.current_stock == 10
    assert count(Sale) == count(Purchase) == count(IdempotencyRecord) == 0


@pytest.mark.parametrize("bad_id", ["abc", "-1", "0", "1.0", "1e2", "999999999999999999999999", "²"])
@pytest.mark.parametrize("service", [sales.create_sale, purchases.create_purchase])
def test_malformed_and_missing_product_ids_are_validation_errors(make_product, transaction_form, bad_id, service):
    make_product()
    with pytest.raises(ValidationError):
        service(transaction_form(bad_id), None, str(uuid4()))
    assert count(Sale) == count(Purchase) == count(IdempotencyRecord) == 0


@pytest.mark.parametrize("discount", ["-1", "1.001", "10.11", "NaN"])
def test_invalid_discount_never_writes(make_product, transaction_form, discount):
    product = make_product()
    with pytest.raises(ValidationError):
        sales.create_sale(transaction_form(product.id, discount=discount), None, str(uuid4()))
    assert product.current_stock == 10
    assert count(Sale) == 0


def test_purchase_exact_stock_and_snapshot(make_product, product_form, transaction_form):
    product = make_product(opening_stock="0")
    form = transaction_form(product.id, "3", **{"items[0][cost]": "4.29"})
    purchase, created = purchases.create_purchase(form, None, "purchase-once")
    assert created and purchase.total_paise == 1287
    assert product.current_stock == 3
    replay, created = purchases.create_purchase(form, None, "purchase-once")
    assert not created and replay.id == purchase.id
    products.update_product(product, product_form(sku=product.sku, name="Renamed", purchase_price="99.99"), None)
    assert purchase.items[0].product_name == "Test Notebook"
    assert purchase.items[0].unit_cost_paise == 429
    assert len(ledger(product.id)) == 2


def test_sale_snapshot_survives_edit_archive_and_void_once(make_product, product_form, transaction_form):
    product = make_product()
    original_sku = product.sku
    sale, _ = sales.create_sale(transaction_form(product.id, "2"), None, "snapshot-sale")
    products.update_product(product, product_form(sku="NEW-SKU", name="Changed", unit="box", selling_price="99.99"), None)
    products.set_archived(product, True, None)
    assert product.is_archived
    assert (sale.items[0].sku, sale.items[0].product_name, sale.items[0].unit) == (original_sku, "Test Notebook", "pc")
    assert sale.items[0].unit_price_paise == 1010
    with pytest.raises(ValidationError):
        sales.create_sale(transaction_form(product.id), None, "archived-sale")
    with pytest.raises(ValidationError):
        purchases.create_purchase(transaction_form(product.id), None, "archived-purchase")
    sales.void_sale(sale.id, "Entry correction", None)
    assert product.current_stock == 10
    assert sale.status == "voided"
    assert sale.voided_at is not None
    with pytest.raises(ConflictError):
        sales.void_sale(sale.id, "Second attempt", None)
    assert [row.movement_type for row in ledger(product.id)] == ["opening", "sale", "sale_void"]
    products.set_archived(product, False, None)
    assert not product.is_archived


def test_purchase_reversal_cannot_make_stock_negative(make_product, transaction_form):
    product = make_product(opening_stock="0")
    purchase, _ = purchases.create_purchase(transaction_form(product.id, "5"), None, "stock-in")
    sale, _ = sales.create_sale(transaction_form(product.id, "1"), None, "stock-out")
    before = count(AuditEvent)
    with pytest.raises(ConflictError):
        purchases.void_purchase(purchase.id, "Cannot remove five", None)
    assert product.current_stock == 4
    assert purchase.status == "completed" and purchase.voided_at is None
    assert count(AuditEvent) == before
    sales.void_sale(sale.id, "Restore first", None)
    purchases.void_purchase(purchase.id, "Now reverse purchase", None)
    assert product.current_stock == 0
    with pytest.raises(ConflictError):
        purchases.void_purchase(purchase.id, "Repeat reversal", None)
    assert len(ledger(product.id)) == 5


def test_stock_adjustment_requires_reason_and_preserves_ledger(make_product):
    product = make_product()
    with pytest.raises(ValidationError):
        products.adjust_stock(product, {"expected_stock": "10", "new_stock": "2", "reason": ""}, None)
    products.adjust_stock(product, {"expected_stock": "10", "new_stock": "2", "reason": "Counted shelf stock"}, None)
    assert ledger(product.id)[-1].quantity_change == -8
    with pytest.raises(ValidationError):
        products.adjust_stock(product, {"new_stock": "-1", "reason": "Invalid count"}, None)
    assert product.current_stock == 2


def test_expense_edit_void_audit_and_no_second_void(ctx):
    expense = expenses.create_expense({"business_date": "2026-06-15", "category": "Utilities", "amount": "12.34"}, None)
    assert expense.amount_paise == 1234
    expenses.update_expense(expense, {"business_date": "2026-06-15", "category": "Utilities", "amount": "13.01"}, None)
    event = db.session.scalar(select(AuditEvent).where(AuditEvent.event_type == "expense.updated"))
    details = json.loads(event.details_json)
    assert details["before"]["amount_paise"] == 1234
    assert details["after"]["amount_paise"] == 1301
    expenses.void_expense(expense.id, "Duplicate receipt", None)
    with pytest.raises(ConflictError):
        expenses.void_expense(expense.id, "Duplicate again", None)
    with pytest.raises(ConflictError):
        expenses.update_expense(expense, {"business_date": "2026-06-15", "category": "Utilities", "amount": "99"}, None)
    assert count(AuditEvent) == 3
    assert expense.status == "voided"


@pytest.mark.parametrize("operation", ["product", "sale", "purchase", "expense", "adjust", "archive", "update"])
def test_forced_audit_failure_rolls_back_entire_write(make_product, product_form, transaction_form, monkeypatch, operation):
    product = make_product()
    product_id = product.id
    before = {model: count(model) for model in (
        Product, Sale, SaleItem, Purchase, PurchaseItem, Expense, StockMovement, AuditEvent, IdempotencyRecord,
    )}

    def fail(*args, **kwargs):
        raise RuntimeError("forced audit failure")

    monkeypatch.setattr(audit, "log_event", fail)
    actions = {
        "product": lambda: products.create_product(product_form(), None),
        "sale": lambda: sales.create_sale(transaction_form(product_id), None, "fail-sale"),
        "purchase": lambda: purchases.create_purchase(transaction_form(product_id), None, "fail-purchase"),
        "expense": lambda: expenses.create_expense({"business_date": "2026-06-15", "category": "Rent", "amount": "10.01"}, None),
        "adjust": lambda: products.adjust_stock(product, {"expected_stock": "10", "new_stock": "5", "reason": "Cycle count"}, None),
        "archive": lambda: products.set_archived(product, True, None),
        "update": lambda: products.update_product(product, product_form(sku=product.sku, name="Uncommitted name"), None),
    }
    with pytest.raises(RuntimeError, match="forced audit failure"):
        actions[operation]()
    # No test-side rollback: the service must clean up its own failed transaction.
    assert {model: count(model) for model in before} == before
    db.session.expire_all()
    unchanged = db.session.get(Product, product_id)
    assert unchanged.current_stock == 10
    assert unchanged.name == "Test Notebook"
    assert not unchanged.is_archived
    ledger(product_id)


@pytest.mark.parametrize("kind", ["sale", "purchase", "expense"])
def test_forced_audit_failure_rolls_back_void(make_product, transaction_form, monkeypatch, kind):
    product = make_product()
    if kind == "sale":
        transaction, _ = sales.create_sale(transaction_form(product.id), None, "before-void-sale")
        void = sales.void_sale
    elif kind == "purchase":
        transaction, _ = purchases.create_purchase(transaction_form(product.id), None, "before-void-purchase")
        void = purchases.void_purchase
    else:
        transaction = expenses.create_expense({"business_date": "2026-06-15", "category": "Rent", "amount": "10"}, None)
        void = expenses.void_expense
    stock, movements, events = product.current_stock, count(StockMovement), count(AuditEvent)

    def fail(*args, **kwargs):
        raise RuntimeError("forced void audit failure")

    monkeypatch.setattr(audit, "log_event", fail)
    with pytest.raises(RuntimeError, match="forced void audit failure"):
        void(transaction.id, "Test reversal", None)
    assert product.current_stock == stock
    assert count(StockMovement) == movements and count(AuditEvent) == events
    db.session.expire_all()
    assert transaction.status == ("active" if kind == "expense" else "completed")
    assert transaction.voided_at is None
    ledger(product.id)
