import re
import secrets
from flask import Blueprint, flash, g, redirect, render_template, request
from sqlalchemy import or_, select
from app.blueprints.common import attempt, page_number, period_from_request, search_term
from app.extensions import db
from app.models import Product, Purchase, Sale
from app.security import login_required
from app.services import purchases, sales
from app.services.clock import business_today

sales_bp = Blueprint("sales", __name__, url_prefix="/sales")
purchases_bp = Blueprint("purchases", __name__, url_prefix="/purchases")


def transaction_list(kind):
    model = Sale if kind == "sale" else Purchase
    party = model.customer_name if kind == "sale" else model.supplier_name
    period = period_from_request()
    stmt = select(model).where(model.business_date >= period.start, model.business_date <= period.end)
    status = request.args.get("status", "all")
    if status in {"completed", "voided"}:
        stmt = stmt.where(model.status == status)
    if search_term():
        stmt = stmt.where(or_(model.reference.contains(search_term(), autoescape=True), party.contains(search_term(), autoescape=True)))
    pagination = db.paginate(stmt.order_by(model.business_date.desc(), model.id.desc()), page=page_number(), per_page=12, error_out=False)
    return render_template("transactions/list.html", kind=kind, pagination=pagination, period=period)


def transaction_form(kind):
    errors, status = {}, 200
    if request.method == "POST":
        result, errors, status = attempt(lambda: (sales.create_sale if kind == "sale" else purchases.create_purchase)(request.form, g.user.id, request.form.get("idempotency_key", "")))
        if not errors:
            transaction, created = result
            flash("Transaction recorded." if created else "This request was already recorded. Showing the original transaction.", "success")
            return redirect(f"/{'sales' if kind == 'sale' else 'purchases'}/{transaction.id}", code=303)
        form = request.form
        rows = {}
        for key, value in form.items():
            match = re.fullmatch(r"items\[([0-9]{1,6})\]\[(product_id|quantity|price|cost)\]", key)
            if match:
                rows.setdefault(int(match[1]), {})[match[2]] = value
        item_rows = [{"index": index, **row} for index, row in sorted(rows.items())]
    else:
        form = {"idempotency_key": secrets.token_urlsafe(24), "business_date": business_today(g.business.timezone).isoformat(), "discount": "0.00"}
        item_rows = [{"index": 0, "product_id": "", "quantity": "1", "price": "", "cost": ""}]
    available = db.session.scalars(select(Product).where(Product.is_archived.is_(False)).order_by(Product.name)).all()
    return render_template("transactions/form.html", kind=kind, products=available, item_rows=item_rows, form=form, errors=errors), status


def transaction_detail(kind, transaction_id, void=False):
    transaction = sales.get_sale_or_404(transaction_id) if kind == "sale" else purchases.get_purchase_or_404(transaction_id)
    errors, status = {}, 200
    if void:
        _, errors, status = attempt(lambda: (sales.void_sale if kind == "sale" else purchases.void_purchase)(transaction_id, request.form.get("reason", ""), g.user.id))
        if not errors:
            flash("Transaction voided. Inventory reversal recorded.", "success")
            return redirect(f"/{'sales' if kind == 'sale' else 'purchases'}/{transaction_id}", code=303)
    return render_template("transactions/detail.html", transaction=transaction, kind=kind, errors=errors, form=request.form if request.method == "POST" else {}), status


@sales_bp.get("")
@login_required
def index():
    return transaction_list("sale")


@sales_bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    return transaction_form("sale")


@sales_bp.get("/<int(min=1,max=2147483647):transaction_id>")
@login_required
def detail(transaction_id):
    return transaction_detail("sale", transaction_id)


@sales_bp.post("/<int(min=1,max=2147483647):transaction_id>/void")
@login_required
def void(transaction_id):
    return transaction_detail("sale", transaction_id, void=True)


@purchases_bp.get("")
@login_required
def purchase_index():
    return transaction_list("purchase")


@purchases_bp.route("/new", methods=["GET", "POST"])
@login_required
def purchase_create():
    return transaction_form("purchase")


@purchases_bp.get("/<int(min=1,max=2147483647):transaction_id>")
@login_required
def purchase_detail(transaction_id):
    return transaction_detail("purchase", transaction_id)


@purchases_bp.post("/<int(min=1,max=2147483647):transaction_id>/void")
@login_required
def purchase_void(transaction_id):
    return transaction_detail("purchase", transaction_id, void=True)
