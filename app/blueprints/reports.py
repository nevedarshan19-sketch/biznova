from flask import Blueprint, Response, abort, g, render_template
from app.blueprints.common import period_from_request
from app.security import login_required
from app.services import reports
from app.services.clock import business_today

bp = Blueprint("reports", __name__, url_prefix="/reports")


@bp.get("")
@login_required
def index():
    period = period_from_request()
    return render_template("reports.html", data=reports.report_pack(period), period=period)


@bp.get("/export/<kind>")
@login_required
def export(kind):
    period = period_from_request()
    currency = g.business.currency
    choices = {
        "sales": (["Reference", "Date", "Customer", "Status", f"Subtotal {currency}", f"Discount {currency}", f"Total {currency}", "Void reason"], lambda: reports.sales_csv_rows(period.start, period.end)),
        "purchases": (["Reference", "Date", "Supplier", "Status", f"Total {currency}", "Void reason"], lambda: reports.purchases_csv_rows(period.start, period.end)),
        "expenses": (["Date", "Category", f"Amount {currency}", "Payment method", "Description", "Status", "Void reason"], lambda: reports.expenses_csv_rows(period.start, period.end)),
        "top-products": (["SKU", "Product", "Unit", "Quantity sold", f"Before-discount sales {currency}"], lambda: reports.top_products_csv_rows(period.start, period.end)),
        "inventory": (["SKU", "Product", "Category", "Unit", "Current stock", "Threshold", "Status"], reports.inventory_csv_rows),
        "low-stock": (["SKU", "Product", "Unit", "Current stock", "Threshold", "Units to threshold"], reports.low_stock_csv_rows),
        "summary": (["Metric", f"Value ({currency} for money)"], lambda: reports.summary_csv_rows(period, reports.report_pack(period))),
    }
    if kind not in choices:
        abort(404)
    headers, get_rows = choices[kind]
    stamp = f"current-{business_today(g.business.timezone)}" if kind in {"inventory", "low-stock"} else f"{period.start}-to-{period.end}"
    return Response(reports.csv_bytes(headers, get_rows()), content_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="biznova-{kind}-{stamp}.csv"'})
