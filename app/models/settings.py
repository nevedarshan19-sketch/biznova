from app.extensions import db
from app.models.base import utcnow


class BusinessSettings(db.Model):
    """Singleton row (id=1) holding business-wide display preferences."""

    __tablename__ = "business_settings"

    id = db.Column(db.Integer, primary_key=True)
    business_name = db.Column(db.String(120), nullable=False, default="BizNova")
    currency = db.Column(db.String(3), nullable=False, default="INR")
    timezone = db.Column(db.String(64), nullable=False, default="Asia/Kolkata")
    theme = db.Column(db.String(10), nullable=False, default="system")
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        db.CheckConstraint("theme IN ('light', 'dark', 'system')", name="ck_settings_theme"),
        db.CheckConstraint("length(currency) = 3", name="ck_settings_currency"),
    )

    @classmethod
    def get(cls) -> "BusinessSettings":
        # Column defaults apply at INSERT, so transient read-only defaults
        # must be supplied explicitly. GET never adds or flushes this object.
        with db.session.no_autoflush:
            settings = db.session.get(cls, 1)
        if settings is None:
            settings = cls(
                id=1,
                business_name="BizNova",
                currency="INR",
                timezone="Asia/Kolkata",
                theme="system",
            )
        return settings
