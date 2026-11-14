"""Independent write units for SQLite; service functions own their commit.

Acquire the database write reservation before any business reads. A previous
read-only transaction is ended and its identity map expired so route-loaded
objects cannot supply stale stock, archive flags or transaction statuses.
Mutation helpers (inventory, audit, idempotency) run inside this boundary.
The factory must set SQLite's connection timeout to 15 seconds.
"""
from functools import wraps

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.extensions import db
from app.services.errors import ConflictError, NotFoundError

_ACTIVE = "biznova_atomic_write"
_WRITTEN = "biznova_uncommitted_write"


@event.listens_for(Session, "after_flush")
def _remember_flush(session, flush_context):
    # A flushed write is no longer present in session.new/dirty/deleted.
    session.info[_WRITTEN] = True


@event.listens_for(Session, "do_orm_execute")
def _remember_dml(execute_state):
    if execute_state.is_insert or execute_state.is_update or execute_state.is_delete:
        execute_state.session.info[_WRITTEN] = True


@event.listens_for(Session, "after_transaction_end")
def _forget_completed_write(session, transaction):
    if transaction.parent is None:
        session.info.pop(_WRITTEN, None)


def atomic_write(function):
    """Roll back the entire operation on failure, including validation/flush.

    Call with a clean session (prior reads are fine), not uncommitted caller
    edits. Reject those edits explicitly instead of silently losing them while
    refreshing ORM state. Nested calls share the outer write reservation.
    """
    @wraps(function)
    def wrapped(*args, **kwargs):
        session = db.session()
        if session.info.get(_ACTIVE):
            return function(*args, **kwargs)
        try:
            if session.new or session.dirty or session.deleted or session.info.get(_WRITTEN):
                raise ConflictError(
                    "There are uncommitted changes. Save them separately before this operation."
                )
            # SQLAlchemy autobegin may represent a real SQLite read transaction;
            # end it before issuing BEGIN, rather than nesting BEGIN in it.
            session.rollback()
            connection = session.connection()
            if connection.dialect.name != "sqlite":
                raise RuntimeError("Atomic writes require the configured SQLite database.")
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            session.expire_all()
            session.info[_ACTIVE] = True
            return function(*args, **kwargs)
        except BaseException:
            session.rollback()
            raise
        finally:
            session.info.pop(_ACTIVE, None)

    return wrapped


def fresh_entity(entity, model):
    """Re-fetch even a detached route-loaded entity under the write lock."""
    identity = inspect(entity).identity
    if identity is None:
        raise NotFoundError(f"{model.__name__} not found.")
    current = db.session.get(model, identity, populate_existing=True)
    if current is None:
        raise NotFoundError(f"{model.__name__} not found.")
    return current
