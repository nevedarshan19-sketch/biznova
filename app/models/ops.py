from app.extensions import db
from app.models.base import utcnow


class AuditEvent(db.Model):
    __tablename__ = "audit_events"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(40), nullable=False, index=True)
    entity_type = db.Column(db.String(32), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=True, index=True)
    summary = db.Column(db.String(300), nullable=False)
    details_json = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class IdempotencyRecord(db.Model):
    """Persistent idempotency key. Unique constraint makes retries safe."""

    __tablename__ = "idempotency_records"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), nullable=False, unique=True, index=True)
    scope = db.Column(db.String(40), nullable=False)
    request_hash = db.Column(db.String(64), nullable=False)
    response_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
