"""Business expense recording with an audit trail."""
from sqlalchemy import update

from app.extensions import db
from app.models import Expense, utcnow
from app.models.transactions import STATUS_ACTIVE, STATUS_VOIDED
from app.services import audit
from app.services.money import money_input
from app.services.transactions import atomic_write, fresh_entity
from app.services.errors import ConflictError, FormValidator, NotFoundError, ValidationError


def _validate(form: FormValidator):
    category = form.text("category", "Category", required=True, max_len=60)
    amount = form.money("amount", "Amount", required=True)
    business_date = form.date("business_date", "Business date", required=True)
    payment_method = form.text("payment_method", "Payment method", required=False, max_len=40)
    description = form.text("description", "Description", required=False, max_len=300)
    form.raise_if_errors()
    if amount is None or amount <= 0:
        raise ValidationError("Amount must be more than zero.", {"amount": "Amount must be more than zero."})
    return {
        "category": category,
        "amount_paise": amount,
        "business_date": business_date,
        "payment_method": payment_method or None,
        "description": description or None,
    }


@atomic_write
def create_expense(form, user_id: int | None) -> Expense:
    data = _validate(FormValidator(form))
    expense = Expense(**data, created_by=user_id)
    db.session.add(expense)
    db.session.flush()
    audit.log_event(
        "expense.created",
        "expense",
        expense.id,
        f"Recorded {data['category']} expense of {money_input(data['amount_paise'])}",
        details={"category": data["category"], "amount_paise": data["amount_paise"]},
        user_id=user_id,
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return expense


@atomic_write
def update_expense(expense: Expense, form, user_id: int | None) -> Expense:
    expense = fresh_entity(expense, Expense)
    if expense.status == STATUS_VOIDED:
        raise ConflictError("Voided expenses cannot be edited.")
    data = _validate(FormValidator(form))
    before = {
        "category": expense.category,
        "amount_paise": expense.amount_paise,
        "business_date": str(expense.business_date),
        "payment_method": expense.payment_method,
        "description": expense.description,
    }
    for key, value in data.items():
        setattr(expense, key, value)
    audit.log_event(
        "expense.updated",
        "expense",
        expense.id,
        f"Updated {expense.category} expense",
        details={"before": before, "after": {**data, "business_date": str(data["business_date"])}},
        user_id=user_id,
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return expense


def get_expense_or_404(expense_id: int) -> Expense:
    expense = db.session.get(Expense, expense_id)
    if expense is None:
        raise NotFoundError("Expense not found.")
    return expense


@atomic_write
def void_expense(expense_id: int, reason: str, user_id: int | None) -> Expense:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValidationError(
            "Please give a short reason for voiding this expense.",
            {"reason": "Please give a short reason (3+ characters)."},
        )
    if len(reason) > 300:
        raise ValidationError("Reason is too long.", {"reason": "Maximum 300 characters."})

    expense = get_expense_or_404(expense_id)
    if expense.status == STATUS_VOIDED:
        raise ConflictError("This expense has already been voided.")
    try:
        result = db.session.execute(
            update(Expense)
            .where(Expense.id == expense.id, Expense.status == STATUS_ACTIVE)
            .values(status=STATUS_VOIDED, voided_at=utcnow(), void_reason=reason)
        )
        if result.rowcount != 1:
            raise ConflictError("This expense has already been voided.")
        audit.log_event(
            "expense.voided",
            "expense",
            expense.id,
            f"Voided {expense.category} expense",
            details={"reason": reason, "amount_paise": expense.amount_paise},
            user_id=user_id,
        )
        db.session.commit()
        db.session.expire(expense, ["status", "voided_at", "void_reason"])
        return expense
    except Exception:
        db.session.rollback()
        raise
