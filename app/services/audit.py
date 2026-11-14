import json

from app.extensions import db
from app.models import AuditEvent


def log_event(
    event_type: str,
    entity_type: str,
    entity_id: int | None,
    summary: str,
    *,
    details: dict | None = None,
    user_id: int | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary[:300],
        details_json=json.dumps(details, default=str) if details else None,
        user_id=user_id,
    )
    db.session.add(event)
    return event
