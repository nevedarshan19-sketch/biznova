"""Persistent idempotency keys for create operations.

A key is reserved inside the same database transaction as the business write.
The first request commits the business row and the stored response together;
a retry with the same key and payload returns that stored response, while a
key reused with different content is rejected with 409.
"""
import hashlib
import json

from sqlalchemy import select

from app.extensions import db
from app.models import IdempotencyRecord
from app.services.errors import ConflictError, ValidationError


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find(key: str) -> IdempotencyRecord | None:
    return db.session.execute(
        select(IdempotencyRecord).where(IdempotencyRecord.key == key)
    ).scalar_one_or_none()


def reserve(key: str, scope: str, request_hash: str) -> tuple[IdempotencyRecord | None, dict | None]:
    """Return (record, replay_response).

    replay_response is set when this key already completed successfully.
    """
    key = (key or "").strip()
    if not key:
        raise ValidationError(
            "This form has expired. Reload the page and try again.",
            {"_form": "This form has expired. Reload the page and try again."},
        )
    if len(key) > 80:
        raise ValidationError("Invalid request key.", {"_form": "Invalid request key."})

    existing = _find(key)
    if existing is not None:
        if existing.scope != scope or existing.request_hash != request_hash:
            raise ConflictError(
                "This request key was already used for different details. Reload the page and try again."
            )
        if not existing.response_json:
            raise ConflictError("A request with this key is still being processed. Please retry in a moment.")
        return existing, json.loads(existing.response_json)

    record = IdempotencyRecord(key=key, scope=scope, request_hash=request_hash)
    db.session.add(record)
    # The caller holds BEGIN IMMEDIATE, so competing reservations cannot race.
    # Never roll back and resume here: that would release the operation's lock.
    db.session.flush()
    return record, None


def finish(record: IdempotencyRecord | None, response: dict) -> None:
    if record is not None:
        record.response_json = json.dumps(response, sort_keys=True)
