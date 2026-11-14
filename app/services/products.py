"""Product and category operations."""
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Category, Product
from app.services import audit, inventory
from app.services.errors import (
    ConflictError, FormValidator, NotFoundError, StockConflictError, ValidationError,
)
from app.services.money import MAX_STOCK, parse_product_id
from app.services.transactions import atomic_write, fresh_entity

_SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,39}$")


def normalize_sku(raw: str) -> str:
    return " ".join((raw or "").split()).upper()


def get_or_create_category(name: str) -> Category:
    """Find/create within the caller's atomic write (NOCASE is database-backed)."""
    name = name.strip()
    if not name or len(name) > 80:
        raise ValidationError("Category must contain 1 to 80 characters.")
    category = db.session.execute(
        select(Category).where(Category.name == name)
    ).scalar_one_or_none()
    if category is None:
        category = Category(name=name)
        db.session.add(category)
        db.session.flush()
    return category


def _validate_base(validator: FormValidator, *, include_opening: bool) -> dict:
    sku_raw = validator.text("sku", "SKU", required=True, max_len=40)
    sku = normalize_sku(sku_raw)
    if sku and not _SKU_RE.match(sku):
        validator.errors.setdefault(
            "sku", "Use letters, numbers, dots, dashes, slashes or underscores (max 40)."
        )
    name = validator.text("name", "Product name", required=True, max_len=160)
    category = validator.text("category", "Category", required=False, max_len=80, default="General") or "General"
    unit = validator.text("unit", "Unit", required=False, max_len=16, default="pc") or "pc"
    purchase_price = validator.money("purchase_price", "Default purchase price", required=False, allow_blank=True, default=0)
    selling_price = validator.money("selling_price", "Selling price", required=False, allow_blank=True, default=0)
    threshold = validator.integer(
        "low_stock_threshold", "Low-stock threshold", required=False, minimum=0,
        maximum=MAX_STOCK, default=0,
    )
    description = validator.text("description", "Description", required=False, max_len=1000)
    opening = 0
    if include_opening:
        opening = validator.integer(
            "opening_stock", "Opening stock", required=False, minimum=0, maximum=MAX_STOCK, default=0,
        )
    validator.raise_if_errors()
    return {
        "sku": sku,
        "name": name,
        "category": category,
        "unit": unit,
        "purchase_price_paise": purchase_price or 0,
        "selling_price_paise": selling_price or 0,
        "low_stock_threshold": threshold or 0,
        "description": description or None,
        "opening_stock": opening or 0,
    }


@atomic_write
def create_product(form, user_id: int | None) -> Product:
    validator = FormValidator(form)
    data = _validate_base(validator, include_opening=True)
    if db.session.execute(select(Product.id).where(Product.sku == data["sku"])).scalar_one_or_none():
        raise ValidationError("That SKU already exists.", {"sku": "That SKU already exists."})

    category = get_or_create_category(data["category"])
    product = Product(
        sku=data["sku"],
        name=data["name"],
        category_id=category.id,
        unit=data["unit"],
        purchase_price_paise=data["purchase_price_paise"],
        selling_price_paise=data["selling_price_paise"],
        current_stock=data["opening_stock"],
        low_stock_threshold=data["low_stock_threshold"],
        description=data["description"],
    )
    db.session.add(product)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise ValidationError("That SKU already exists.", {"sku": "That SKU already exists."})

    inventory.opening_movement(product, data["opening_stock"])
    audit.log_event(
        "product.created",
        "product",
        product.id,
        f"Created product {product.sku} with opening stock {data['opening_stock']}",
        details={"sku": product.sku, "opening_stock": data["opening_stock"]},
        user_id=user_id,
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return product


@atomic_write
def update_product(product: Product, form, user_id: int | None) -> Product:
    product = fresh_entity(product, Product)
    if product.is_archived:
        raise ConflictError("Restore this product before editing it.")
    validator = FormValidator(form)
    data = _validate_base(validator, include_opening=False)
    duplicate = db.session.execute(
        select(Product.id).where(Product.sku == data["sku"], Product.id != product.id)
    ).scalar_one_or_none()
    if duplicate:
        raise ValidationError("That SKU already exists.", {"sku": "That SKU already exists."})

    category = get_or_create_category(data["category"])
    before = {
        "sku": product.sku,
        "name": product.name,
        "selling_price_paise": product.selling_price_paise,
    }
    product.sku = data["sku"]
    product.name = data["name"]
    product.category_id = category.id
    product.unit = data["unit"]
    product.purchase_price_paise = data["purchase_price_paise"]
    product.selling_price_paise = data["selling_price_paise"]
    product.low_stock_threshold = data["low_stock_threshold"]
    product.description = data["description"]
    audit.log_event(
        "product.updated",
        "product",
        product.id,
        f"Updated product {product.sku}",
        details={"before": before, "after": data},
        user_id=user_id,
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ValidationError("That SKU already exists.", {"sku": "That SKU already exists."})
    return product


@atomic_write
def set_archived(product: Product, archived: bool, user_id: int | None) -> Product:
    product = fresh_entity(product, Product)
    if product.is_archived == archived:
        raise ConflictError("Product is already in that state.")
    product.is_archived = archived
    audit.log_event(
        "product.archived" if archived else "product.restored",
        "product",
        product.id,
        f"{'Archived' if archived else 'Restored'} product {product.sku}",
        user_id=user_id,
    )
    db.session.commit()
    return product


@atomic_write
def adjust_stock(product: Product, form, user_id: int | None) -> Product:
    product = fresh_entity(product, Product)
    if product.is_archived:
        raise ConflictError("Restore this product before adjusting stock.")
    validator = FormValidator(form)
    new_stock = validator.integer(
        "new_stock", "New stock quantity", required=True, minimum=0, maximum=MAX_STOCK,
    )
    expected_stock = validator.integer(
        "expected_stock", "Original stock quantity", required=True, minimum=0, maximum=MAX_STOCK,
    )
    reason = validator.text("reason", "Reason", required=True, max_len=300)
    if reason and len(reason) < 3:
        validator.errors.setdefault("reason", "Please give a short reason (3+ characters).")
    validator.raise_if_errors()

    before = product.current_stock
    if expected_stock != before:
        raise StockConflictError(
            "Stock changed since this form was loaded. Reload the product and try again.",
            {"expected_stock": f"Stock is now {before}. Reload before adjusting it."},
        )
    inventory.set_absolute_stock(product, new_stock, reason)
    audit.log_event(
        "stock.adjusted",
        "product",
        product.id,
        f"Adjusted stock for {product.sku}: {before} -> {new_stock}",
        details={"before": before, "after": new_stock, "reason": reason},
        user_id=user_id,
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return product


def get_product_or_404(product_id: int) -> Product:
    try:
        product_id = parse_product_id(product_id)
    except ValueError:
        raise NotFoundError("Product not found.") from None
    product = db.session.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product not found.")
    return product
