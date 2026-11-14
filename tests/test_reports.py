import csv
from datetime import date, timedelta
import io

import pytest

from app.services import expenses, products, purchases, reports, sales
from app.services.errors import ValidationError


def test_period_totals_void_exclusion_top_products_and_current_inventory(make_product, transaction_form):
    day = date(2026, 6, 15)
    product = make_product(opening_stock="20")
    included = transaction_form(product.id, "2", business_date=day.isoformat(), discount="0.01")
    sales.create_sale(included, None, "report-included")
    old_sale, _ = sales.create_sale(transaction_form(
        product.id, "1", business_date=(day - timedelta(days=1)).isoformat(),
    ), None, "report-old")
    voided_sale, _ = sales.create_sale(transaction_form(
        product.id, "1", business_date=day.isoformat(),
    ), None, "report-void-sale")
    sales.void_sale(voided_sale.id, "Exclude from active totals", None)
    purchases.create_purchase(transaction_form(
        product.id, "2", business_date=day.isoformat(), **{"items[0][cost]": "2.01"},
    ), None, "report-purchase")
    voided_purchase, _ = purchases.create_purchase(transaction_form(
        product.id, "1", business_date=day.isoformat(),
    ), None, "report-void-purchase")
    purchases.void_purchase(voided_purchase.id, "Exclude from totals", None)
    expenses.create_expense({"category": "Travel", "amount": "1.11", "business_date": day.isoformat()}, None)
    voided_expense = expenses.create_expense({"category": "Travel", "amount": "9.99", "business_date": day.isoformat()}, None)
    expenses.void_expense(voided_expense.id, "Exclude from totals", None)
    expenses.create_expense({"category": "Old", "amount": "99", "business_date": old_sale.business_date.isoformat()}, None)
    period = reports.Period(day, day, "custom", "Test day")
    data = reports.report_pack(period)
    assert data["sales"]["revenue_paise"] == 2019
    assert data["sales"]["count"] == 1 and data["sales"]["items_sold"] == 2
    assert data["purchases"]["total_paise"] == 402
    assert data["expenses"]["total_paise"] == 111
    assert data["cash_flow_paise"] == 1506  # Revenue minus purchases minus expenses, NOT profit.
    assert data["top_products"][0]["sales_before_discount_paise"] == 2020  # Pre-discount line totals.
    assert data["top_products"][0]["quantity"] == 2
    assert reports.sales_trend(day, day + timedelta(days=1)) == [
        {"date": day.isoformat(), "value_paise": 2019},
        {"date": (day + timedelta(days=1)).isoformat(), "value_paise": 0},
    ]
    # Inventory is NOW, not historical stock at the chosen report date.
    assert data["inventory"][0]["current_stock"] == 19
    products.adjust_stock(product, {"expected_stock": "19", "new_stock": "2", "reason": "Count current shelf"}, None)
    assert reports.report_pack(period)["inventory"][0]["current_stock"] == 2
    # Report CSV matches active KPI totals; voided records remain in detail views.
    rows = reports.sales_csv_rows(day, day)
    assert len(rows) == 1
    assert {row[3] for row in rows} == {"completed"}


def test_period_is_inclusive_and_validated(ctx, monkeypatch):
    today = date(2026, 6, 30)
    monkeypatch.setattr(reports, "business_today", lambda tz=None: today)
    period = reports.resolve_period({"period": "30d"}, "Asia/Kolkata")
    assert period.start == date(2026, 6, 1) and period.end == today and period.days == 30
    assert reports.resolve_period({"period": "today"}, "UTC").days == 1
    for args in (
        {"period": "nonsense"}, {"period": "custom"},
        {"period": "custom", "from": "2026-06-03", "to": "2026-06-01"},
        {"period": "custom", "from": "bad", "to": "2026-06-01"},
        {"period": "custom", "from": "2000-01-01", "to": "2026-01-01"},
    ):
        with pytest.raises(ValidationError):
            reports.resolve_period(args, "UTC")


@pytest.mark.parametrize("payload", [
    "=SUM(A1:A2)", "+cmd", "-1+2", "@SUM(A1)", "\t=1+1", "\r=1+1",
    "  =1+1", "\n=1+1",
])
def test_csv_injection_is_neutralized_in_headers_and_cells(payload):
    output = reports.csv_bytes([payload, "Name"], [[payload, 'Comma, quote " and newline\n']])
    parsed = list(csv.reader(io.StringIO(output.decode("utf-8"))))
    assert parsed[0][0].startswith("'")
    assert parsed[1][0].startswith("'")
    assert parsed[1][1] == 'Comma, quote " and newline\n'


def test_inventory_excludes_archived_and_labels_low_and_out(make_product):
    low = make_product(sku="LOW", opening_stock="2", low_stock_threshold="2")
    make_product(sku="OUT", opening_stock="0")
    archived = make_product(sku="HIDDEN", opening_stock="0")
    products.set_archived(archived, True, None)
    assert reports.active_product_count() == 2
    assert reports.low_stock_count() == 2
    assert reports.out_of_stock_count() == 1
    rows = {row["sku"]: row for row in reports.inventory_snapshot()}
    assert rows["LOW"]["status"] == "Low stock" and rows["OUT"]["status"] == "Out of stock"
    assert "HIDDEN" not in rows
    assert low.id in [product.id for product in reports.low_stock_products()]
