from flask import Blueprint, flash, g, redirect, render_template, request
from sqlalchemy import or_, select
from app.blueprints.common import attempt, page_number, search_term
from app.extensions import db
from app.models import AuditEvent, Category, Product, StockMovement
from app.security import login_required
from app.services import products
from app.services.money import money_input
from app.services.errors import ValidationError

bp = Blueprint("products", __name__, url_prefix="/products")


@bp.get("")
@login_required
def index():
    stmt = select(Product)
    status = request.args.get("status", "active")
    if status != "all":
        stmt = stmt.where(Product.is_archived.is_(status == "archived"))
    if search_term():
        stmt = stmt.where(or_(Product.name.contains(search_term(), autoescape=True), Product.sku.contains(search_term(), autoescape=True)))
    category = request.args.get("category", type=int)
    if category:
        if not 1 <= category <= 2147483647:
            raise ValidationError("Choose a valid category.")
        stmt = stmt.where(Product.category_id == category)
    stock = request.args.get("stock")
    if stock == "low":
        stmt = stmt.where(Product.current_stock <= Product.low_stock_threshold)
    elif stock == "out":
        stmt = stmt.where(Product.current_stock == 0)
    sort = {"name": Product.name, "sku": Product.sku, "stock": Product.current_stock, "price": Product.selling_price_paise}.get(request.args.get("sort"), Product.name)
    pagination = db.paginate(stmt.order_by(sort, Product.id), page=page_number(), per_page=12, error_out=False)
    return render_template("products/list.html", pagination=pagination, categories=db.session.scalars(select(Category).order_by(Category.name)).all(), filters=request.args)


def product_form(product=None):
    errors, status = {}, 200
    if request.method == "POST":
        result, errors, status = attempt(lambda: products.update_product(product, request.form, g.user.id) if product else products.create_product(request.form, g.user.id))
        if not errors:
            flash("Product saved.", "success")
            return redirect(f"/products/{result.id}", code=303)
        form = request.form
    elif product:
        form = {"sku": product.sku, "name": product.name, "category": product.category.name if product.category else "General", "unit": product.unit, "purchase_price": money_input(product.purchase_price_paise), "selling_price": money_input(product.selling_price_paise), "low_stock_threshold": product.low_stock_threshold, "description": product.description or ""}
    else:
        form = {"unit": "pc", "opening_stock": 0, "low_stock_threshold": 5, "purchase_price": "0.00", "selling_price": "0.00"}
    return render_template("products/form.html", product=product, form=form, errors=errors, categories=db.session.scalars(select(Category).order_by(Category.name)).all()), status


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    return product_form()


@bp.route("/<int(min=1,max=2147483647):product_id>/edit", methods=["GET", "POST"])
@login_required
def edit(product_id):
    return product_form(products.get_product_or_404(product_id))


def show_detail(product, errors=None, status=200):
    movements = db.session.scalars(select(StockMovement).where(StockMovement.product_id == product.id).order_by(StockMovement.id.desc())).all()
    events = db.session.scalars(select(AuditEvent).where(AuditEvent.entity_type == "product", AuditEvent.entity_id == product.id).order_by(AuditEvent.id.desc())).all()
    return render_template("products/detail.html", product=product, movements=movements, audit_events=events, errors=errors or {}, form=request.form if request.method == "POST" else {}), status


@bp.get("/<int(min=1,max=2147483647):product_id>")
@login_required
def detail(product_id):
    return show_detail(products.get_product_or_404(product_id))


@bp.post("/<int(min=1,max=2147483647):product_id>/archive")
@login_required
def archive(product_id):
    product = products.get_product_or_404(product_id)
    if request.form.get("archived") not in {"0", "1"}:
        raise ValidationError("Choose a valid archive action.")
    _, errors, status = attempt(lambda: products.set_archived(product, request.form.get("archived") == "1", g.user.id))
    if errors:
        return show_detail(product, errors, status)
    flash("Product archive status updated. History is preserved.", "success")
    return redirect(f"/products/{product.id}", code=303)


@bp.post("/<int(min=1,max=2147483647):product_id>/adjust")
@login_required
def adjust(product_id):
    product = products.get_product_or_404(product_id)
    _, errors, status = attempt(lambda: products.adjust_stock(product, request.form, g.user.id))
    if errors:
        return show_detail(product, errors, status)
    flash("Stock adjusted and recorded in the ledger.", "success")
    return redirect(f"/products/{product.id}", code=303)
