from sqlalchemy import Index

from app.extensions import db
from app.models.base import TimestampMixin, utcnow

MOVEMENT_OPENING = "opening"
MOVEMENT_SALE = "sale"
MOVEMENT_PURCHASE = "purchase"
MOVEMENT_ADJUSTMENT = "adjustment"
MOVEMENT_SALE_VOID = "sale_void"
MOVEMENT_PURCHASE_VOID = "purchase_void"

MOVEMENT_TYPES = (
    MOVEMENT_OPENING,
    MOVEMENT_SALE,
    MOVEMENT_PURCHASE,
    MOVEMENT_ADJUSTMENT,
    MOVEMENT_SALE_VOID,
    MOVEMENT_PURCHASE_VOID,
)


class Category(TimestampMixin, db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80, collation="NOCASE"), nullable=False, unique=True)


class Product(TimestampMixin, db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), nullable=False, unique=True, index=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True, index=True)
    unit = db.Column(db.String(16), nullable=False, default="pc")
    purchase_price_paise = db.Column(db.Integer, nullable=False, default=0)
    selling_price_paise = db.Column(db.Integer, nullable=False, default=0)
    current_stock = db.Column(db.Integer, nullable=False, default=0)
    low_stock_threshold = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.Text, nullable=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    category = db.relationship("Category", lazy="joined")
    movements = db.relationship("StockMovement", back_populates="product", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("purchase_price_paise >= 0", name="ck_products_purchase_price"),
        db.CheckConstraint("selling_price_paise >= 0", name="ck_products_selling_price"),
        db.CheckConstraint(
            "typeof(purchase_price_paise) = 'integer' AND typeof(selling_price_paise) = 'integer' "
            "AND purchase_price_paise <= 99999999999 AND selling_price_paise <= 99999999999",
            name="ck_products_prices_bounded_integer",
        ),
        db.CheckConstraint("current_stock >= 0", name="ck_products_stock_non_negative"),
        db.CheckConstraint("current_stock <= 1000000000", name="ck_products_stock_max"),
        db.CheckConstraint("typeof(current_stock) = 'integer'", name="ck_products_stock_integer"),
        db.CheckConstraint("low_stock_threshold >= 0", name="ck_products_threshold_non_negative"),
        db.CheckConstraint(
            "low_stock_threshold <= 1000000000 AND typeof(low_stock_threshold) = 'integer'",
            name="ck_products_threshold_bounded_integer",
        ),
        Index("ix_products_name_stock", "name", "current_stock"),
    )


class StockMovement(db.Model):
    """Append-only ledger of every stock change.

    quantity_change is signed. balance_after lets the ledger be reconciled
    against Product.current_stock at any point in time.
    """

    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    quantity_change = db.Column(db.Integer, nullable=False)
    movement_type = db.Column(db.String(24), nullable=False, index=True)
    reference_type = db.Column(db.String(16), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.String(300), nullable=True)
    balance_after = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    product = db.relationship("Product", back_populates="movements")

    __table_args__ = (
        db.CheckConstraint("balance_after >= 0", name="ck_movements_balance_non_negative"),
        db.CheckConstraint("balance_after <= 1000000000", name="ck_movements_balance_max"),
        db.CheckConstraint(
            "typeof(balance_after) = 'integer' AND typeof(quantity_change) = 'integer'",
            name="ck_movements_integer",
        ),
        db.CheckConstraint(
            "movement_type IN ('opening','sale','purchase','adjustment','sale_void','purchase_void')",
            name="ck_movements_type",
        ),
        db.CheckConstraint(
            "(reference_type IS NULL AND reference_id IS NULL) OR "
            "(reference_type IN ('sale','purchase') AND reference_id IS NOT NULL)",
            name="ck_movements_reference",
        ),
        Index("ix_movements_ref", "reference_type", "reference_id"),
    )


class StockSnapshot:
    """Read-only helper used by templates for stock status badges."""

    @staticmethod
    def status_for(product: Product) -> str:
        if product.current_stock <= 0:
            return "out"
        if product.current_stock <= product.low_stock_threshold:
            return "low"
        return "in"
