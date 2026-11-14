"""Stock ledger operations.

Every stock change flows through this module so the append-only ledger and the
product balance always move together inside one transaction. Decreases use a
conditional SQL UPDATE (current_stock >= quantity) which is atomic in SQLite,
so concurrent sales cannot oversell. These helpers do not commit: call them
inside a service's @atomic_write boundary with its stock/audit writes.
"""
from sqlalchemy import select, update

from app.extensions import db
from app.models import Product, StockMovement
from app.services.errors import ConflictError, NotFoundError, StockConflictError, ValidationError
from app.services.money import MAX_STOCK


def _stock_of(product_id: int) -> int | None:
    return db.session.execute(
        select(Product.current_stock).where(Product.id == product_id)
    ).scalar_one_or_none()


def change_stock(
    product: Product,
    delta: int,
    movement_type: str,
    *,
    reference_type: str | None = None,
    reference_id: int | None = None,
    reason: str | None = None,
) -> int:
    """Apply a signed delta atomically and append a ledger row.

    Returns the balance after the change. Raises StockConflictError when a
    decrease would take stock below zero.
    """
    if type(delta) is not int or abs(delta) > MAX_STOCK:
        raise ValidationError("Stock changes must be bounded whole numbers.")
    conditions = [
        Product.id == product.id,
        Product.current_stock + delta >= 0,
        Product.current_stock + delta <= MAX_STOCK,
    ]
    # Voids must still reverse historical lines on archived products.
    if movement_type in ("sale", "purchase", "adjustment"):
        conditions.append(Product.is_archived.is_(False))
    result = db.session.execute(
        update(Product).where(*conditions).values(current_stock=Product.current_stock + delta)
    )
    if result.rowcount != 1:
        available = _stock_of(product.id)
        if available is None:
            raise NotFoundError("Product no longer exists.")
        if available + delta < 0:
            raise StockConflictError(
                f"Not enough stock for {product.name}. "
                f"Available: {available} {product.unit}, requested: {-delta}."
            )
        if available + delta > MAX_STOCK:
            raise StockConflictError(f"Stock for {product.name} cannot exceed {MAX_STOCK}.")
        raise ConflictError("Archived products cannot receive new stock transactions.")

    balance = _stock_of(product.id)
    if balance is None:
        raise NotFoundError("Product no longer exists.")

    movement = StockMovement(
        product_id=product.id,
        quantity_change=delta,
        movement_type=movement_type,
        reference_type=reference_type,
        reference_id=reference_id,
        reason=(reason or None),
        balance_after=balance,
    )
    db.session.add(movement)
    db.session.expire(product, ["current_stock"])
    return balance


def set_absolute_stock(product: Product, new_stock: int, reason: str) -> int:
    """Explicit stock adjustment with optimistic concurrency on current stock."""
    if type(new_stock) is not int or not 0 <= new_stock <= MAX_STOCK:
        raise ValidationError(
            f"Stock must be a whole number between 0 and {MAX_STOCK}.",
            {"new_stock": f"Enter a whole number between 0 and {MAX_STOCK}."},
        )
    # adjust_stock has checked the submitted expected_stock under BEGIN IMMEDIATE.
    expected = product.current_stock
    delta = new_stock - expected
    if delta == 0:
        raise ValidationError(
            "The new quantity matches the current stock, so there is nothing to adjust.",
            {"new_stock": "Enter a different quantity."},
        )
    result = db.session.execute(
        update(Product)
        .where(
            Product.id == product.id,
            Product.current_stock == expected,
            Product.is_archived.is_(False),
        )
        .values(current_stock=new_stock)
    )
    if result.rowcount != 1:
        raise StockConflictError(
            "Stock changed while you were adjusting it. Reload the product and try again."
        )
    db.session.add(
        StockMovement(
            product_id=product.id,
            quantity_change=delta,
            movement_type="adjustment",
            reason=reason,
            balance_after=new_stock,
        )
    )
    db.session.expire(product, ["current_stock"])
    return new_stock


def opening_movement(product: Product, quantity: int) -> None:
    db.session.add(
        StockMovement(
            product_id=product.id,
            quantity_change=quantity,
            movement_type="opening",
            reason="Opening stock at product creation",
            balance_after=quantity,
        )
    )


def low_stock_query():
    return select(Product).where(
        Product.is_archived.is_(False),
        Product.current_stock <= Product.low_stock_threshold,
    )
