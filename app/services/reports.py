"""Dashboard and report queries.

All totals are read from stored transactions. Voided sales, purchases and
expenses are excluded from active totals. 'Recorded cash flow' is explicitly
revenue minus purchases minus expenses and assumes recorded transactions are
settled; it is never labelled profit.
"""
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    Expense,
    Product,
    Purchase,
    Sale,
    SaleItem,
)
from app.models.transactions import STATUS_ACTIVE, STATUS_COMPLETED
from app.services.clock import business_today
from app.services.errors import ValidationError
from app.services.money import format_money, money_input

PERIOD_OPTIONS = {
    "today": "Today",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "90d": "Last 90 days",
    "month": "This month",
    "custom": "Custom range",
}
DEFAULT_PERIOD = "30d"
MAX_REPORT_DAYS = 731
TOP_PRODUCTS_AMOUNT_LABEL = "Sales before discount"


def _validate_range(start: date, end: date) -> None:
    if type(start) is not date or type(end) is not date:
        raise ValidationError("Choose a valid date range.", {"period": "Use valid dates."})
    if not date(2000, 1, 1) <= start <= end <= date(2100, 12, 31):
        raise ValidationError(
            "Choose ordered dates between 2000 and 2100.", {"period": "Invalid date range."},
        )
    if (end - start).days + 1 > MAX_REPORT_DAYS:
        raise ValidationError(
            "Please choose a range of two years or less.", {"period": "Range is too long."},
        )


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    key: str
    label: str

    def __post_init__(self):
        _validate_range(self.start, self.end)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "key": self.key,
            "label": self.label,
            "days": self.days,
        }


def _parse_iso(value, field: str) -> date:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(
            "Please choose a valid date range.",
            {field: "Use YYYY-MM-DD format."},
        )


def resolve_period(args, tz_name: str) -> Period:
    period = (args.get("period") or DEFAULT_PERIOD).strip()
    if period not in PERIOD_OPTIONS:
        raise ValidationError("Unknown date range.", {"period": "Choose a valid range."})
    today = business_today(tz_name)
    if period == "custom":
        start_raw, end_raw = args.get("from"), args.get("to")
        if not start_raw or not end_raw:
            raise ValidationError(
                "Choose both a start and end date.", {"period": "Choose both dates."}
            )
        start = _parse_iso(start_raw, "from")
        end = _parse_iso(end_raw, "to")
        if start > end:
            raise ValidationError(
                "The start date must be on or before the end date.",
                {"period": "Start date must be before the end date."},
            )
        if (end - start).days > 730:
            raise ValidationError(
                "Please choose a range of two years or less.",
                {"period": "Range is too long."},
            )
        label = f"{start:%d %b %Y} to {end:%d %b %Y}"
    else:
        end = today
        if period == "today":
            start = today
        elif period == "7d":
            start = today - timedelta(days=6)
        elif period == "30d":
            start = today - timedelta(days=29)
        elif period == "90d":
            start = today - timedelta(days=89)
        else:  # month
            start = today.replace(day=1)
        label = PERIOD_OPTIONS[period]
    return Period(start=start, end=end, key=period, label=label)


def _sum(column, model, date_column, status_column, status_value, start, end) -> int:
    _validate_range(start, end)
    value = db.session.scalar(
        select(func.coalesce(func.sum(column), 0)).where(
            model.business_date >= start,
            model.business_date <= end,
            status_column == status_value,
        )
    )
    return int(value or 0)


def _count(column, model, date_column, status_column, status_value, start, end) -> int:
    _validate_range(start, end)
    return int(
        db.session.scalar(
            select(func.count(column)).where(
                model.business_date >= start,
                model.business_date <= end,
                status_column == status_value,
            )
        )
        or 0
    )


def sales_revenue(start, end) -> int:
    return _sum(Sale.total_paise, Sale, "business_date", Sale.status, STATUS_COMPLETED, start, end)


def purchases_total(start, end) -> int:
    return _sum(
        Purchase.total_paise, Purchase, "business_date", Purchase.status, STATUS_COMPLETED, start, end
    )


def expenses_total(start, end) -> int:
    return _sum(
        Expense.amount_paise, Expense, "business_date", Expense.status, STATUS_ACTIVE, start, end
    )


def cash_flow(start, end) -> int:
    return sales_revenue(start, end) - purchases_total(start, end) - expenses_total(start, end)


def active_product_count() -> int:
    return int(
        db.session.scalar(
            select(func.count(Product.id)).where(Product.is_archived.is_(False))
        )
        or 0
    )


def low_stock_count() -> int:
    return int(
        db.session.scalar(
            select(func.count(Product.id)).where(
                Product.is_archived.is_(False),
                Product.current_stock <= Product.low_stock_threshold,
            )
        )
        or 0
    )


def out_of_stock_count() -> int:
    return int(
        db.session.scalar(
            select(func.count(Product.id)).where(
                Product.is_archived.is_(False), Product.current_stock <= 0
            )
        )
        or 0
    )


def low_stock_products(limit: int | None = None):
    stmt = (
        select(Product)
        .where(Product.is_archived.is_(False), Product.current_stock <= Product.low_stock_threshold)
        .order_by(Product.current_stock.asc(), Product.name.asc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return db.session.execute(stmt).scalars().all()


def sales_trend(start: date, end: date) -> list[dict]:
    _validate_range(start, end)
    rows = db.session.execute(
        select(Sale.business_date, func.coalesce(func.sum(Sale.total_paise), 0))
        .where(
            Sale.status == STATUS_COMPLETED,
            Sale.business_date >= start,
            Sale.business_date <= end,
        )
        .group_by(Sale.business_date)
    ).all()
    totals = {row[0]: int(row[1] or 0) for row in rows}
    series = []
    cursor = start
    while cursor <= end:
        series.append({"date": cursor.isoformat(), "value_paise": totals.get(cursor, 0)})
        cursor += timedelta(days=1)
    return series


def top_products(start, end, limit: int = 5) -> list[dict]:
    """Rank historical name/SKU/unit snapshots by quantity sold.

    Amounts are line sales BEFORE order-level discounts, not net revenue.
    """
    _validate_range(start, end)
    rows = db.session.execute(
        select(
            SaleItem.product_id,
            SaleItem.product_name,
            SaleItem.sku,
            SaleItem.unit,
            func.sum(SaleItem.quantity).label("quantity"),
            func.sum(SaleItem.line_total_paise).label("sales_before_discount_paise"),
        )
        .join(Sale, SaleItem.sale_id == Sale.id)
        .where(
            Sale.status == STATUS_COMPLETED,
            Sale.business_date >= start,
            Sale.business_date <= end,
        )
        .group_by(SaleItem.product_id, SaleItem.product_name, SaleItem.sku, SaleItem.unit)
        .order_by(
            func.sum(SaleItem.quantity).desc(), SaleItem.product_id.asc(),
            SaleItem.product_name.asc(), SaleItem.sku.asc(), SaleItem.unit.asc(),
        )
        .limit(limit)
    ).all()
    return [
        {
            "product_id": row.product_id,
            "name": row.product_name,
            "sku": row.sku,
            "unit": row.unit,
            "quantity": int(row.quantity or 0),
            "sales_before_discount_paise": int(row.sales_before_discount_paise or 0),
            "amount_label": TOP_PRODUCTS_AMOUNT_LABEL,
        }
        for row in rows
    ]


def sales_summary(start, end) -> dict:
    count = _count(Sale.id, Sale, "business_date", Sale.status, STATUS_COMPLETED, start, end)
    revenue = sales_revenue(start, end)
    items_sold = int(
        db.session.scalar(
            select(func.coalesce(func.sum(SaleItem.quantity), 0))
            .join(Sale, SaleItem.sale_id == Sale.id)
            .where(
                Sale.status == STATUS_COMPLETED,
                Sale.business_date >= start,
                Sale.business_date <= end,
            )
        )
        or 0
    )
    return {
        "count": count,
        "revenue_paise": revenue,
        "items_sold": items_sold,
        "average_sale_paise": (revenue // count) if count else 0,
    }


def purchases_summary(start, end) -> dict:
    return {
        "count": _count(
            Purchase.id, Purchase, "business_date", Purchase.status, STATUS_COMPLETED, start, end
        ),
        "total_paise": purchases_total(start, end),
    }


def expense_summary(start, end) -> dict:
    _validate_range(start, end)
    rows = db.session.execute(
        select(
            Expense.category,
            func.coalesce(func.sum(Expense.amount_paise), 0).label("total"),
            func.count(Expense.id).label("count"),
        )
        .where(
            Expense.status == STATUS_ACTIVE,
            Expense.business_date >= start,
            Expense.business_date <= end,
        )
        .group_by(Expense.category)
        .order_by(func.sum(Expense.amount_paise).desc())
    ).all()
    return {
        "count": _count(
            Expense.id, Expense, "business_date", Expense.status, STATUS_ACTIVE, start, end
        ),
        "total_paise": expenses_total(start, end),
        "by_category": [
            {"category": row.category, "total_paise": int(row.total or 0), "count": int(row.count)}
            for row in rows
        ],
    }


def recent_transactions(start: date, end: date, limit: int = 8) -> list[dict]:
    """Latest active entries within the inclusive business-date range."""
    _validate_range(start, end)
    entries = []
    for sale in db.session.execute(
        select(Sale)
        .where(Sale.status == STATUS_COMPLETED, Sale.business_date >= start, Sale.business_date <= end)
        .order_by(Sale.created_at.desc(), Sale.id.desc()).limit(limit)
    ).scalars():
        entries.append(
            {
                "kind": "sale",
                "id": sale.id,
                "reference": sale.reference,
                "party": sale.customer_name or "Walk-in customer",
                "amount_paise": sale.total_paise,
                "business_date": sale.business_date,
                "created_at": sale.created_at,
                "status": sale.status,
                "url": f"/sales/{sale.id}",
            }
        )
    for purchase in db.session.execute(
        select(Purchase)
        .where(Purchase.status == STATUS_COMPLETED, Purchase.business_date >= start, Purchase.business_date <= end)
        .order_by(Purchase.created_at.desc(), Purchase.id.desc()).limit(limit)
    ).scalars():
        entries.append(
            {
                "kind": "purchase",
                "id": purchase.id,
                "reference": purchase.reference,
                "party": purchase.supplier_name or "Unnamed supplier",
                "amount_paise": purchase.total_paise,
                "business_date": purchase.business_date,
                "created_at": purchase.created_at,
                "status": purchase.status,
                "url": f"/purchases/{purchase.id}",
            }
        )
    for expense in db.session.execute(
        select(Expense)
        .where(Expense.status == STATUS_ACTIVE, Expense.business_date >= start, Expense.business_date <= end)
        .order_by(Expense.created_at.desc(), Expense.id.desc()).limit(limit)
    ).scalars():
        entries.append(
            {
                "kind": "expense",
                "id": expense.id,
                "reference": expense.category,
                "party": expense.description or expense.category,
                "amount_paise": expense.amount_paise,
                "business_date": expense.business_date,
                "created_at": expense.created_at,
                "status": expense.status,
                "url": "/expenses",
            }
        )
    entries.sort(
        key=lambda item: (item["created_at"] or datetime.min, item["kind"], item["id"]),
        reverse=True,
    )
    return entries[:limit]


def inventory_snapshot() -> list[dict]:
    products = db.session.execute(
        select(Product).where(Product.is_archived.is_(False)).order_by(Product.name.asc())
    ).scalars()
    rows = []
    for product in products:
        if product.current_stock <= 0:
            status = "Out of stock"
        elif product.current_stock <= product.low_stock_threshold:
            status = "Low stock"
        else:
            status = "In stock"
        rows.append(
            {
                "sku": product.sku,
                "name": product.name,
                "category": product.category.name if product.category else "General",
                "unit": product.unit,
                "current_stock": product.current_stock,
                "threshold": product.low_stock_threshold,
                "status": status,
            }
        )
    return rows


def dashboard_data(period: Period) -> dict:
    return {
        "period": period,
        "revenue_paise": sales_revenue(period.start, period.end),
        "purchases_paise": purchases_total(period.start, period.end),
        "expenses_paise": expenses_total(period.start, period.end),
        "cash_flow_paise": cash_flow(period.start, period.end),
        "sales_count": _count(
            Sale.id, Sale, "business_date", Sale.status, STATUS_COMPLETED, period.start, period.end
        ),
        "active_products": active_product_count(),
        "low_stock_count": low_stock_count(),
        "out_of_stock_count": out_of_stock_count(),
        "trend": sales_trend(period.start, period.end),
        "top_products": top_products(period.start, period.end),
        "recent": recent_transactions(period.start, period.end),
        "low_stock_products": low_stock_products(limit=6),
    }


def report_pack(period: Period) -> dict:
    return {
        "period": period,
        "sales": sales_summary(period.start, period.end),
        "purchases": purchases_summary(period.start, period.end),
        "expenses": expense_summary(period.start, period.end),
        "top_products": top_products(period.start, period.end, limit=10),
        "cash_flow_paise": cash_flow(period.start, period.end),
        "inventory": inventory_snapshot(),
        "low_stock": low_stock_products(),
    }


def csv_safe(value) -> str:
    text = "" if value is None else str(value)
    # Spreadsheet importers may ignore leading whitespace/control characters.
    # Inspect past them, but retain the original cell and prefix an apostrophe.
    start = 0
    for character in text:
        if not (character.isspace() or ord(character) < 32 or character == "\ufeff"):
            break
        start += 1
    if text[start:].startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        text = "'" + text
    return text


def csv_bytes(headers: list[str], rows: list[list]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([csv_safe(item) for item in headers])
    for row in rows:
        writer.writerow([csv_safe(item) for item in row])
    return buffer.getvalue().encode("utf-8")


def _amount(paise) -> str:
    return money_input(paise)


def sales_csv_rows(start, end):
    _validate_range(start, end)
    sales = db.session.execute(
        select(Sale)
        .where(Sale.status == STATUS_COMPLETED, Sale.business_date >= start, Sale.business_date <= end)
        .order_by(Sale.business_date.asc(), Sale.id.asc())
    ).scalars()
    rows = []
    for sale in sales:
        rows.append(
            [
                sale.reference,
                sale.business_date.isoformat(),
                sale.customer_name or "Walk-in customer",
                sale.status,
                _amount(sale.subtotal_paise),
                _amount(sale.discount_paise),
                _amount(sale.total_paise),
                sale.void_reason or "",
            ]
        )
    return rows


def purchases_csv_rows(start, end):
    _validate_range(start, end)
    purchases = db.session.execute(
        select(Purchase)
        .where(Purchase.status == STATUS_COMPLETED, Purchase.business_date >= start, Purchase.business_date <= end)
        .order_by(Purchase.business_date.asc(), Purchase.id.asc())
    ).scalars()
    return [
        [
            purchase.reference,
            purchase.business_date.isoformat(),
            purchase.supplier_name or "",
            purchase.status,
            _amount(purchase.total_paise),
            purchase.void_reason or "",
        ]
        for purchase in purchases
    ]


def expenses_csv_rows(start, end):
    _validate_range(start, end)
    expenses = db.session.execute(
        select(Expense)
        .where(Expense.status == STATUS_ACTIVE, Expense.business_date >= start, Expense.business_date <= end)
        .order_by(Expense.business_date.asc(), Expense.id.asc())
    ).scalars()
    return [
        [
            expense.business_date.isoformat(),
            expense.category,
            _amount(expense.amount_paise),
            expense.payment_method or "",
            expense.description or "",
            expense.status,
            expense.void_reason or "",
        ]
        for expense in expenses
    ]


def top_products_csv_rows(start, end):
    """The caller must label the final column TOP_PRODUCTS_AMOUNT_LABEL."""
    return [
        [row["sku"], row["name"], row["unit"], row["quantity"], _amount(row["sales_before_discount_paise"])]
        for row in top_products(start, end, limit=500)
    ]


def inventory_csv_rows():
    return [
        [row["sku"], row["name"], row["category"], row["unit"], row["current_stock"], row["threshold"], row["status"]]
        for row in inventory_snapshot()
    ]


def low_stock_csv_rows():
    return [
        [
            product.sku,
            product.name,
            product.unit,
            product.current_stock,
            product.low_stock_threshold,
            max(0, product.low_stock_threshold - product.current_stock),
        ]
        for product in low_stock_products()
    ]


def summary_csv_rows(period: Period, data: dict):
    return [
        ["Reporting period", f"{period.start.isoformat()} to {period.end.isoformat()}"],
        ["Sales revenue", _amount(data["sales"]["revenue_paise"])],
        ["Completed sales", data["sales"]["count"]],
        ["Items sold", data["sales"]["items_sold"]],
        ["Purchase spending", _amount(data["purchases"]["total_paise"])],
        ["Completed purchases", data["purchases"]["count"]],
        ["Expenses", _amount(data["expenses"]["total_paise"])],
        ["Recorded cash flow", _amount(data["cash_flow_paise"])],
    ]


def format_money_for(paise, currency):
    return format_money(paise, currency)
