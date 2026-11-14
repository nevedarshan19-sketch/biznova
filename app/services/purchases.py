"""Purchases: stock-in transactions and controlled voiding."""
import re
from uuid import uuid4

from sqlalchemy import update

from app.extensions import db
from app.models import Product, Purchase, PurchaseItem, utcnow
from app.models.transactions import STATUS_COMPLETED, STATUS_VOIDED
from app.services import audit, idempotency, inventory
from app.services.transactions import atomic_write
from app.services.errors import (
    ConflictError,
    FormValidator,
    NotFoundError,
    StockConflictError,
    ValidationError,
)
from app.services.money import (
    MAX_TOTAL_PAISE, MAX_TRANSACTION_LINES, MAX_UNIT_PRICE_PAISE,
    MoneyError, money_input, parse_paise, parse_product_id, parse_quantity,
)

_ITEM_KEY_RE = re.compile(r"items\[([0-9]+)\]\[(product_id|quantity|cost)\]")
SCOPE = "purchase.create"


def collect_items(form) -> dict[int, dict[str, str]]:
    rows: dict[int, dict[str, str]] = {}
    for key in form.keys():
        match = _ITEM_KEY_RE.fullmatch(key)
        if match:
            if len(match.group(1)) > 6:
                raise ValidationError("Invalid product line index.", {"items": "Reload this form."})
            index, field = int(match.group(1)), match.group(2)
            if field in rows.get(index, {}) or (
                hasattr(form, "getlist") and len(form.getlist(key)) != 1
            ):
                raise ValidationError("Duplicate product line fields.", {"items": "Reload this form."})
            rows.setdefault(index, {})[field] = form.get(key)
            if len(rows) > MAX_TRANSACTION_LINES:
                raise ValidationError(
                    "A transaction may contain at most 100 product lines.",
                    {"items": "Remove extra lines (maximum 100)."},
                )
        elif str(key).startswith("items["):
            raise ValidationError("Invalid product line.", {"items": "Reload this form."})
    return dict(sorted(rows.items()))


def _new_reference(business_date) -> str:
    return uuid4().hex.upper()


def _payload_hash(raw, rows: dict[int, dict[str, str]]) -> str:
    canonical = {
        "supplier_name": str(raw.get("supplier_name", "") or "").strip(),
        "business_date": str(raw.get("business_date", "") or "").strip(),
        "notes": str(raw.get("notes", "") or "").strip(),
        "items": [
            {
                "product_id": str(row.get("product_id", "") or "").strip(),
                "quantity": str(row.get("quantity", "") or "").strip(),
                "cost": "" if row.get("cost") is None else str(row["cost"]),
            }
            for row in rows.values()
        ],
    }
    return idempotency.payload_hash(canonical)


@atomic_write
def create_purchase(raw, user_id: int | None, idempotency_key: str):
    rows = collect_items(raw)
    request_hash = _payload_hash(raw, rows)
    record, replay = idempotency.reserve(idempotency_key, SCOPE, request_hash)
    if replay is not None:
        purchase = db.session.get(Purchase, replay["purchase_id"])
        if purchase is None:
            raise ConflictError("The original purchase for this request no longer exists.")
        db.session.commit()  # Release the write reservation on replay too.
        return purchase, False

    try:
        validator = FormValidator(raw)
        supplier_name = validator.text("supplier_name", "Supplier name", required=False, max_len=120)
        notes = validator.text("notes", "Notes", required=False, max_len=500)
        business_date = validator.date("business_date", "Business date", required=True)

        if not rows:
            raise ValidationError(
                "Add at least one product to the purchase.", {"items": "Add at least one product."}
            )

        parsed_items = []
        seen_products: set[int] = set()
        total = 0
        for index, row in rows.items():
            try:
                product_id = parse_product_id(row.get("product_id"))
            except ValueError:
                validator.errors[f"items.{index}.product_id"] = "Choose a valid product."
                continue
            if product_id in seen_products:
                validator.errors[f"items.{index}.product_id"] = (
                    "This product is already on the purchase. Combine the quantities into one line."
                )
                continue
            seen_products.add(product_id)

            product = db.session.get(Product, product_id)
            if product is None:
                validator.errors[f"items.{index}.product_id"] = "Product not found."
                continue
            if product.is_archived:
                validator.errors[f"items.{index}.product_id"] = (
                    f"{product.name} is archived and cannot be purchased."
                )
                continue

            try:
                quantity = parse_quantity(row.get("quantity"), "Quantity", minimum=1)
            except ValueError as exc:
                validator.errors[f"items.{index}.quantity"] = str(exc)
                quantity = None

            cost_raw = row.get("cost")
            cost_raw = "" if cost_raw is None else str(cost_raw)
            if cost_raw:
                try:
                    unit_cost = parse_paise(cost_raw, "Unit cost")
                except MoneyError as exc:
                    validator.errors[f"items.{index}.cost"] = str(exc)
                    unit_cost = None
            else:
                unit_cost = product.purchase_price_paise

            if unit_cost is not None and (
                type(unit_cost) is not int or not 0 <= unit_cost <= MAX_UNIT_PRICE_PAISE
            ):
                validator.errors[f"items.{index}.cost"] = "Unit cost is outside the supported range."
                unit_cost = None
            if quantity is not None and unit_cost is not None:
                line_total = quantity * unit_cost
                total += line_total
                if total > MAX_TOTAL_PAISE:
                    validator.errors["items"] = "Purchase total cannot exceed 10^15 paise."
                parsed_items.append(
                    {
                        "product": product,
                        "quantity": quantity,
                        "unit_cost_paise": unit_cost,
                        "line_total_paise": line_total,
                    }
                )
        validator.raise_if_errors()

        purchase = Purchase(
            reference=_new_reference(business_date),
            supplier_name=supplier_name or None,
            business_date=business_date,
            notes=notes or None,
            total_paise=total,
            created_by=user_id,
        )
        db.session.add(purchase)
        db.session.flush()

        for item in parsed_items:
            product = item["product"]
            db.session.add(
                PurchaseItem(
                    purchase_id=purchase.id,
                    product_id=product.id,
                    product_name=product.name,
                    sku=product.sku,
                    unit=product.unit,
                    quantity=item["quantity"],
                    unit_cost_paise=item["unit_cost_paise"],
                    line_total_paise=item["line_total_paise"],
                )
            )
            inventory.change_stock(
                product,
                item["quantity"],
                "purchase",
                reference_type="purchase",
                reference_id=purchase.id,
            )

        audit.log_event(
            "purchase.created",
            "purchase",
            purchase.id,
            f"Recorded purchase {purchase.reference} for {money_input(purchase.total_paise)}",
            details={"reference": purchase.reference, "total_paise": purchase.total_paise},
            user_id=user_id,
        )
        idempotency.finish(
            record, {"purchase_id": purchase.id, "reference": purchase.reference}
        )
        db.session.commit()
        return purchase, True
    except Exception:
        db.session.rollback()
        raise


def get_purchase_or_404(purchase_id: int) -> Purchase:
    purchase = db.session.get(Purchase, purchase_id)
    if purchase is None:
        raise NotFoundError("Purchase not found.")
    return purchase


@atomic_write
def void_purchase(purchase_id: int, reason: str, user_id: int | None) -> Purchase:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationError(
            "Please give a short reason for voiding this purchase.",
            {"reason": "Please give a short reason (3+ characters)."},
        )
    if len(reason) > 300:
        raise ValidationError("Reason is too long.", {"reason": "Maximum 300 characters."})

    purchase = get_purchase_or_404(purchase_id)
    if purchase.status == STATUS_VOIDED:
        raise ConflictError("This purchase has already been voided.")

    try:
        result = db.session.execute(
            update(Purchase)
            .where(Purchase.id == purchase.id, Purchase.status == STATUS_COMPLETED)
            .values(status=STATUS_VOIDED, voided_at=utcnow(), void_reason=reason)
        )
        if result.rowcount != 1:
            raise ConflictError("This purchase has already been voided.")

        purchase = get_purchase_or_404(purchase_id)
        db.session.expire(purchase, ["status", "voided_at", "void_reason"])

        for item in purchase.items:
            product = db.session.get(Product, item.product_id)
            if product is None:
                raise NotFoundError(
                    f"Product for line '{item.product_name}' no longer exists; cannot reverse stock."
                )
            try:
                inventory.change_stock(
                    product,
                    -item.quantity,
                    "purchase_void",
                    reference_type="purchase",
                    reference_id=purchase.id,
                    reason=reason,
                )
            except StockConflictError:
                raise ConflictError(
                    f"Cannot void this purchase: {product.name} has only "
                    f"{product.current_stock} {product.unit} in stock, but "
                    f"{item.quantity} must be removed."
                )

        audit.log_event(
            "purchase.voided",
            "purchase",
            purchase.id,
            f"Voided purchase {purchase.reference}",
            details={"reference": purchase.reference, "reason": reason},
            user_id=user_id,
        )
        db.session.commit()
        return purchase
    except Exception:
        db.session.rollback()
        raise
