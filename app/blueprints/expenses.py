from flask import Blueprint, flash, g, redirect, render_template, request
from sqlalchemy import or_, select
from app.blueprints.common import attempt, page_number, period_from_request, search_term
from app.extensions import db
from app.models import AuditEvent, Expense
from app.security import login_required
from app.services import expenses
from app.services.clock import business_today
from app.services.money import money_input
from app.services.reports import expense_summary

bp = Blueprint("expenses", __name__, url_prefix="/expenses")


@bp.get("")
@login_required
def index():
    period = period_from_request()
    stmt = select(Expense).where(Expense.business_date >= period.start, Expense.business_date <= period.end)
    if search_term():
        stmt = stmt.where(or_(Expense.description.contains(search_term(), autoescape=True), Expense.category.contains(search_term(), autoescape=True)))
    if request.args.get("category"):
        stmt = stmt.where(Expense.category == request.args["category"])
    if request.args.get("status") in {"active", "voided"}:
        stmt = stmt.where(Expense.status == request.args["status"])
    pagination = db.paginate(stmt.order_by(Expense.business_date.desc(), Expense.id.desc()), page=page_number(), per_page=12, error_out=False)
    today = business_today(g.business.timezone)
    month_start = today.replace(day=1)
    return render_template(
        "expenses/list.html",
        pagination=pagination,
        period=period,
        categories=db.session.scalars(select(Expense.category).distinct().order_by(Expense.category)).all(),
        month=expense_summary(month_start, today),
        month_start=month_start,
        today=today,
    )


def expense_form(expense=None):
    errors, status = {}, 200
    if request.method == "POST":
        result, errors, status = attempt(lambda: expenses.update_expense(expense, request.form, g.user.id) if expense else expenses.create_expense(request.form, g.user.id))
        if not errors:
            flash("Expense saved.", "success")
            if request.form.get("return_to") == "list":
                return redirect("/expenses", code=303)
            return redirect(f"/expenses/{result.id}", code=303)
        form = request.form
    elif expense:
        form = {"category": expense.category, "amount": money_input(expense.amount_paise), "business_date": expense.business_date.isoformat(), "payment_method": expense.payment_method or "", "description": expense.description or ""}
    else:
        form = {"business_date": business_today(g.business.timezone).isoformat()}
    return render_template("expenses/form.html", expense=expense, form=form, errors=errors), status


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    return expense_form()


@bp.route("/<int(min=1,max=2147483647):expense_id>/edit", methods=["GET", "POST"])
@login_required
def edit(expense_id):
    return expense_form(expenses.get_expense_or_404(expense_id))


def show_detail(expense, errors=None, status=200):
    events = db.session.scalars(select(AuditEvent).where(AuditEvent.entity_type == "expense", AuditEvent.entity_id == expense.id).order_by(AuditEvent.id.desc())).all()
    return render_template("expenses/detail.html", expense=expense, audit_events=events, errors=errors or {}, form=request.form if request.method == "POST" else {}), status


@bp.get("/<int(min=1,max=2147483647):expense_id>")
@login_required
def detail(expense_id):
    return show_detail(expenses.get_expense_or_404(expense_id))


@bp.post("/<int(min=1,max=2147483647):expense_id>/void")
@login_required
def void(expense_id):
    expense = expenses.get_expense_or_404(expense_id)
    _, errors, status = attempt(lambda: expenses.void_expense(expense_id, request.form.get("reason", ""), g.user.id))
    if errors:
        return show_detail(expense, errors, status)
    flash("Expense voided and audit history preserved.", "success")
    return redirect(f"/expenses/{expense_id}", code=303)
