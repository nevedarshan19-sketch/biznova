import json
from flask import current_app, g, request
from sqlalchemy.exc import OperationalError
from app.extensions import db
from app.services.errors import ServiceError
from app.services.reports import resolve_period


def attempt(operation):
    try:
        return operation(), {}, 200
    except ServiceError as error:
        db.session.rollback()
        return None, {"_form": error.message, **error.field_errors}, error.status_code
    except OperationalError:
        db.session.rollback()
        current_app.logger.warning(json.dumps({"event": "write_unavailable", "path": request.path}))
        return None, {"_form": "Database temporarily unavailable. Your entries are preserved. Retry this same form shortly."}, 503
    except Exception as error:
        db.session.rollback()
        current_app.logger.error(json.dumps({"event": "write_failed", "type": type(error).__name__, "path": request.path}))
        return None, {"_form": "We could not confirm this operation. Your entries are preserved; retry with the same form."}, 500


def period_from_request():
    return resolve_period(request.args, g.business.timezone)


def page_number():
    return max(1, min(request.args.get("page", 1, type=int), 100000))


def search_term():
    return request.args.get("q", "").strip()[:160]
