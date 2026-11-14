from sqlalchemy import CheckConstraint, Index

from app.extensions import db
from app.models.base import TimestampMixin, utcnow

STATUS_COMPLETED = "completed"
STATUS_VOIDED = "voided"
STATUS_ACTIVE = "active"


class Sale(TimestampMixin, db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(32), nullable=False, unique=True, index=True)
    customer_name = db.Column(db.String(120), nullable=True)
    business_date = db.Column(db.Date, nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    subtotal_paise = db.Column(db.Integer, nullable=False)
    discount_paise = db.Column(db.Integer, nullable=False, default=0)
    total_paise = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(12), nullable=False, default=STATUS_COMPLETED, index=True)
    voided_at = db.Column(db.DateTime, nullable=True)
    void_reason = db.Column(db.String(300), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    items = db.relationship("SaleItem", back_populates="sale", lazy="selectin")

    __table_args__ = (
        CheckConstraint("subtotal_paise >= 0", name="ck_sales_subtotal"),
        CheckConstraint("subtotal_paise <= 1000000000000000", name="ck_sales_subtotal_max"),
        CheckConstraint(
            "typeof(subtotal_paise) = 'integer' AND typeof(discount_paise) = 'integer' "
            "AND typeof(total_paise) = 'integer'", name="ck_sales_money_integer",
        ),
        CheckConstraint("discount_paise >= 0", name="ck_sales_discount"),
        CheckConstraint("total_paise >= 0", name="ck_sales_total"),
        CheckConstraint("discount_paise <= subtotal_paise", name="ck_sales_discount_within_subtotal"),
        CheckConstraint("total_paise = subtotal_paise - discount_paise", name="ck_sales_total_math"),
        CheckConstraint("status IN ('completed', 'voided')", name="ck_sales_status"),
        CheckConstraint(
            "(status = 'completed' AND voided_at IS NULL) OR "
            "(status = 'voided' AND voided_at IS NOT NULL)",
            name="ck_sales_void_state",
        ),
        Index("ix_sales_status_date", "status", "business_date"),
    )

    @property
    def is_voided(self) -> bool:
        return self.status == STATUS_VOIDED


class SaleItem(db.Model):
    """Immutable snapshot of what was sold, including historical prices."""

    __tablename__ = "sale_items"

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_name = db.Column(db.String(160), nullable=False)
    sku = db.Column(db.String(40), nullable=False)
    unit = db.Column(db.String(16), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price_paise = db.Column(db.Integer, nullable=False)
    line_total_paise = db.Column(db.Integer, nullable=False)

    sale = db.relationship("Sale", back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0 AND quantity <= 1000000", name="ck_sale_items_qty"),
        CheckConstraint(
            "typeof(quantity) = 'integer' AND typeof(unit_price_paise) = 'integer' "
            "AND typeof(line_total_paise) = 'integer'", name="ck_sale_items_integer",
        ),
        CheckConstraint(
            "unit_price_paise <= 99999999999 AND line_total_paise <= 1000000000000000",
            name="ck_sale_items_money_max",
        ),
        CheckConstraint("unit_price_paise >= 0", name="ck_sale_items_price"),
        CheckConstraint("line_total_paise = quantity * unit_price_paise", name="ck_sale_items_line_math"),
    )


class Purchase(TimestampMixin, db.Model):
    __tablename__ = "purchases"

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(32), nullable=False, unique=True, index=True)
    supplier_name = db.Column(db.String(120), nullable=True)
    business_date = db.Column(db.Date, nullable=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    total_paise = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(12), nullable=False, default=STATUS_COMPLETED, index=True)
    voided_at = db.Column(db.DateTime, nullable=True)
    void_reason = db.Column(db.String(300), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    items = db.relationship("PurchaseItem", back_populates="purchase", lazy="selectin")

    __table_args__ = (
        CheckConstraint(
            "total_paise >= 0 AND total_paise <= 1000000000000000 "
            "AND typeof(total_paise) = 'integer'", name="ck_purchases_total",
        ),
        CheckConstraint("status IN ('completed', 'voided')", name="ck_purchases_status"),
        CheckConstraint(
            "(status = 'completed' AND voided_at IS NULL) OR "
            "(status = 'voided' AND voided_at IS NOT NULL)",
            name="ck_purchases_void_state",
        ),
        Index("ix_purchases_status_date", "status", "business_date"),
    )

    @property
    def is_voided(self) -> bool:
        return self.status == STATUS_VOIDED


class PurchaseItem(db.Model):
    __tablename__ = "purchase_items"

    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(
        db.Integer, db.ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_name = db.Column(db.String(160), nullable=False)
    sku = db.Column(db.String(40), nullable=False)
    unit = db.Column(db.String(16), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_cost_paise = db.Column(db.Integer, nullable=False)
    line_total_paise = db.Column(db.Integer, nullable=False)

    purchase = db.relationship("Purchase", back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0 AND quantity <= 1000000", name="ck_purchase_items_qty"),
        CheckConstraint(
            "typeof(quantity) = 'integer' AND typeof(unit_cost_paise) = 'integer' "
            "AND typeof(line_total_paise) = 'integer'", name="ck_purchase_items_integer",
        ),
        CheckConstraint(
            "unit_cost_paise <= 99999999999 AND line_total_paise <= 1000000000000000",
            name="ck_purchase_items_money_max",
        ),
        CheckConstraint("unit_cost_paise >= 0", name="ck_purchase_items_cost"),
        CheckConstraint("line_total_paise = quantity * unit_cost_paise", name="ck_purchase_items_line_math"),
    )


class Expense(TimestampMixin, db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(60), nullable=False, index=True)
    amount_paise = db.Column(db.Integer, nullable=False)
    business_date = db.Column(db.Date, nullable=False, index=True)
    payment_method = db.Column(db.String(40), nullable=True)
    description = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(12), nullable=False, default=STATUS_ACTIVE, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    voided_at = db.Column(db.DateTime, nullable=True)
    void_reason = db.Column(db.String(300), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "amount_paise > 0 AND amount_paise <= 1000000000000000 "
            "AND typeof(amount_paise) = 'integer'", name="ck_expenses_amount",
        ),
        CheckConstraint("status IN ('active', 'voided')", name="ck_expenses_status"),
        CheckConstraint(
            "(status = 'active' AND voided_at IS NULL) OR "
            "(status = 'voided' AND voided_at IS NOT NULL)",
            name="ck_expenses_void_state",
        ),
        Index("ix_expenses_status_date", "status", "business_date"),
    )

    @property
    def is_voided(self) -> bool:
        return self.status == STATUS_VOIDED
