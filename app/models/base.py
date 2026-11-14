from datetime import datetime, timezone

from app.extensions import db


def utcnow() -> datetime:
    """Naive UTC timestamp, consistent for storage and comparisons."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
