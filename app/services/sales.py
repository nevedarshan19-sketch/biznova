"""Sales: creation, listing helpers and controlled voiding."""
import re
from uuid import uuid4

from sqlalchemy import update

from app.extensions import db
from app.models import Product, Sale, SaleItem, utcnow
from app.models.transactions import STATUS_COMPLETED, STATUS_VOIDED
from app.services import audit, idempotency, inventory
from app.services.transactions import atomic_write
from app.services.errors import (
    ConflictError,
    FormValidator,
    NotFoundError,
    ValidationError,
)
from app.services.money import (
    MAX_TOTAL_PAISE, MAX_TRANSACTION_LINES, MAX_UNIT_PRICE_PAISE,
    MoneyError, money_input, parse_paise, parse_product_id, parse_quantity,
)

_ITEM_KEY_RE = re.compile(r"items\[([0-9]+)\]\[(product_id|quantity|price)\]")
SCOPE = "sale.create"


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
        "customer_name": str(raw.get("customer_name", "") or "").strip(),
        "business_date": str(raw.get("business_date", "") or "").strip(),
        "notes": str(raw.get("notes", "") or "").strip(),
        "discount": str(raw.get("discount", "") or ""),
        "items": [
            {
                "product_id": str(row.get("product_id", "") or "").strip(),
                "quantity": str(row.get("quantity", "") or "").strip(),
                "price": "" if row.get("price") is None else str(row["price"]),
            }
            for row in rows.values()
        ],
    }
    return idempotency.payload_hash(canonical)


@atomic_write
def create_sale(raw, user_id: int | None, idempotency_key: str):
    """Create a sale atomically. Returns (sale, created)."""
    rows = collect_items(raw)
    request_hash = _payload_hash(raw, rows)
    record, replay = idempotency.reserve(idempotency_key, SCOPE, request_hash)
    if replay is not None:
        sale = db.session.get(Sale, replay["sale_id"])
        if sale is None:
            raise ConflictError("The original sale for this request no longer exists.")
        db.session.commit()  # Release the write reservation on replay too.
        return sale, False

    try:
        validator = FormValidator(raw)
        customer_name = validator.text("customer_name", "Customer name", required=False, max_len=120)
        notes = validator.text("notes", "Notes", required=False, max_len=500)
        business_date = validator.date(
            "business_date", "Business date", required=True
        )
        discount = validator.money(
            "discount", "Discount", required=False, allow_blank=True, default=0
        )

        if not rows:
            raise ValidationError(
                "Add at least one product to the sale.", {"items": "Add at least one product."}
            )

        parsed_items = []
        seen_products: set[int] = set()
        subtotal = 0
        for index, row in rows.items():
            try:
                product_id = parse_product_id(row.get("product_id"))
            except ValueError:
                validator.errors[f"items.{index}.product_id"] = "Choose a valid product."
                continue
            if product_id in seen_products:
                validator.errors[f"items.{index}.product_id"] = (
                    "This product is already on the sale. Combine the quantities into one line."
                )
                continue
            seen_products.add(product_id)

            product = db.session.get(Product, product_id)
            if product is None:
                validator.errors[f"items.{index}.product_id"] = "Product not found."
                continue
            if product.is_archived:
                validator.errors[f"items.{index}.product_id"] = (
                    f"{product.name} is archived and cannot be sold."
                )
                continue

            try:
                quantity = parse_quantity(row.get("quantity"), "Quantity", minimum=1)
            except ValueError as exc:
                validator.errors[f"items.{index}.quantity"] = str(exc)
                quantity = None

            price_raw = row.get("price")
            price_raw = "" if price_raw is None else str(price_raw)
            if price_raw:
                try:
                    unit_price = parse_paise(price_raw, "Unit price")
                except MoneyError as exc:
                    validator.errors[f"items.{index}.price"] = str(exc)
                    unit_price = None
            else:
                unit_price = product.selling_price_paise

            if unit_price is not None and (
                type(unit_price) is not int or not 0 <= unit_price <= MAX_UNIT_PRICE_PAISE
            ):
                validator.errors[f"items.{index}.price"] = "Unit price is outside the supported range."
                unit_price = None
            if quantity is not None and unit_price is not None:
                line_total = quantity * unit_price
                subtotal += line_total
                if subtotal > MAX_TOTAL_PAISE:
                    validator.errors["items"] = "Sale subtotal cannot exceed 10^15 paise."
                parsed_items.append(
                    {
                        "product": product,
                        "quantity": quantity,
                        "unit_price_paise": unit_price,
                        "line_total_paise": line_total,
                        "index": index,
                    }
                )

        discount_value = discount or 0
        if discount_value > subtotal:
            validator.errors["discount"] = "Discount cannot be more than the sale subtotal."
        validator.raise_if_errors()

        sale = Sale(
            reference=_new_reference(business_date),
            customer_name=customer_name or None,
            business_date=business_date,
            notes=notes or None,
            subtotal_paise=subtotal,
            discount_paise=discount_value,
            total_paise=subtotal - discount_value,
            created_by=user_id,
        )
        db.session.add(sale)
        db.session.flush()

        for item in parsed_items:
            product = item["product"]
            db.session.add(
                SaleItem(
                    sale_id=sale.id,
                    product_id=product.id,
                    product_name=product.name,
                    sku=product.sku,
                    unit=product.unit,
                    quantity=item["quantity"],
                    unit_price_paise=item["unit_price_paise"],
                    line_total_paise=item["line_total_paise"],
                )
            )
            inventory.change_stock(
                product,
                -item["quantity"],
                "sale",
                reference_type="sale",
                reference_id=sale.id,
            )

        audit.log_event(
            "sale.created",
            "sale",
            sale.id,
            f"Recorded sale {sale.reference} for {money_input(sale.total_paise)}",
            details={
                "reference": sale.reference,
                "items": len(parsed_items),
                "total_paise": sale.total_paise,
            },
            user_id=user_id,
        )
        idempotency.finish(record, {"sale_id": sale.id, "reference": sale.reference})
        db.session.commit()
        return sale, True
    except Exception:
        db.session.rollback()
        raise


def get_sale_or_404(sale_id: int) -> Sale:
    sale = db.session.get(Sale, sale_id)
    if sale is None:
        raise NotFoundError("Sale not found.")
    return sale


@atomic_write
def void_sale(sale_id: int, reason: str, user_id: int | None) -> Sale:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationError(
            "Please give a short reason for voiding this sale.",
            {"reason": "Please give a short reason (3+ characters)."},
        )
    if len(reason) > 300:
        raise ValidationError("Reason is too long.", {"reason": "Maximum 300 characters."})

    sale = get_sale_or_404(sale_id)
    if sale.status == STATUS_VOIDED:
        raise ConflictError("This sale has already been voided.")

    try:
        result = db.session.execute(
            update(Sale)
            .where(Sale.id == sale.id, Sale.status == STATUS_COMPLETED)
            .values(status=STATUS_VOIDED, voided_at=utcnow(), void_reason=reason)
        )
        if result.rowcount != 1:
            raise ConflictError("This sale has already been voided.")

        sale = get_sale_or_404(sale_id)
        db.session.expire(sale, ["status", "voided_at", "void_reason"])

        for item in sale.items:
            product = db.session.get(Product, item.product_id)
            if product is None:
                raise NotFoundError(
                    f"Product for line '{item.product_name}' no longer exists; cannot restore stock."
                )
            inventory.change_stock(
                product,
                item.quantity,
                "sale_void",
                reference_type="sale",
                reference_id=sale.id,
                reason=reason,
            )

        audit.log_event(
            "sale.voided",
            "sale",
            sale.id,
            f"Voided sale {sale.reference}",
            details={"reference": sale.reference, "reason": reason},
            user_id=user_id,
        )
        db.session.commit()
        return sale
    except Exception:
        db.session.rollback()
        raise
