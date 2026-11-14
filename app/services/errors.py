"""Small, explicit validation helpers shared by all services."""
from datetime import date, datetime

from app.services.money import MoneyError, parse_paise, parse_quantity


class ServiceError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, field_errors: dict | None = None):
        super().__init__(message)
        self.message = message
        self.field_errors = field_errors or {}


class ValidationError(ServiceError):
    status_code = 422
    code = "validation_error"


class NotFoundError(ServiceError):
    status_code = 404
    code = "not_found"


class ConflictError(ServiceError):
    status_code = 409
    code = "conflict"


class StockConflictError(ConflictError):
    code = "stock_conflict"


class AuthError(ServiceError):
    status_code = 401
    code = "auth_error"


class FormValidator:
    """Collects field errors without raising, so forms keep every value."""

    def __init__(self, form):
        self.form = form
        self.errors: dict[str, str] = {}

    def _add(self, key: str, message: str) -> None:
        if key not in self.errors:
            self.errors[key] = message

    def text(
        self,
        key: str,
        label: str,
        *,
        required: bool = False,
        max_len: int = 200,
        default: str = "",
    ) -> str:
        value = str(self.form.get(key, "") or "").strip()
        if not value:
            if required:
                self._add(key, f"{label} is required.")
            return default
        if len(value) > max_len:
            self._add(key, f"{label} must be {max_len} characters or fewer.")
        return value

    def integer(
        self,
        key: str,
        label: str,
        *,
        required: bool = True,
        minimum: int = 0,
        maximum: int = 1_000_000,
        default: int | None = None,
    ) -> int | None:
        raw = self.form.get(key, "")
        raw = "" if raw is None else str(raw).strip()
        if not raw:
            if required:
                self._add(key, f"{label} is required.")
            return default
        try:
            value = parse_quantity(raw, label, minimum=minimum, maximum=maximum)
        except ValueError as exc:
            self._add(key, str(exc))
            return default
        return value

    def money(
        self,
        key: str,
        label: str,
        *,
        required: bool = True,
        allow_blank: bool = False,
        default: int | None = None,
    ) -> int | None:
        raw = self.form.get(key, "")
        try:
            value = parse_paise(raw, label, required=required, allow_blank=allow_blank)
        except MoneyError as exc:
            self._add(key, str(exc))
            return default
        if value is None and required:
            return default
        return value

    def date(
        self,
        key: str,
        label: str,
        *,
        required: bool = True,
        default: date | None = None,
    ) -> date | None:
        raw = str(self.form.get(key, "") or "").strip()
        if not raw:
            if required:
                self._add(key, f"{label} is required.")
            return default
        try:
            value = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            self._add(key, f"{label} must be a valid date.")
            return default
        if value < date(2000, 1, 1) or value > date(2100, 12, 31):
            self._add(key, f"{label} must be between 2000 and 2100.")
            return default
        return value

    def choice(self, key: str, label: str, allowed, *, default: str = "") -> str:
        value = str(self.form.get(key, "") or "").strip()
        if value not in allowed:
            self._add(key, f"{label} is invalid.")
            return default
        return value

    def raise_if_errors(self, message: str = "Please correct the highlighted fields.") -> None:
        if self.errors:
            raise ValidationError(message, dict(self.errors))
