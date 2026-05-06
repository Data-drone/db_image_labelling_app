"""
Shared dependencies injected into route handlers.
"""

import logging
import os
from datetime import timedelta

from fastapi import Request

log = logging.getLogger(__name__)

LOCK_TIMEOUT = timedelta(minutes=5)

# ---------------------------------------------------------------------------
# Database state — set by startup, used by get_db()
# ---------------------------------------------------------------------------
_engine = None
_session_factory = None
_use_lakebase = False


def configure_db(engine, session_factory, use_lakebase: bool):
    """Called once at startup to wire the database backend."""
    global _engine, _session_factory, _use_lakebase
    _engine = engine
    _session_factory = session_factory
    _use_lakebase = use_lakebase


def get_engine():
    if _use_lakebase:
        from .lakebase import get_engine as _lb_engine
        return _lb_engine()
    return _engine


def get_session_factory():
    if _use_lakebase:
        from .lakebase import get_session_factory as _lb_factory
        return _lb_factory()
    return _session_factory


def is_lakebase():
    return _use_lakebase


def get_db():
    """Yield a database session.

    For Lakebase, retries session creation if the pool is still
    provisioning a connection (ISCE / InvalidCachedStatementError).
    """
    import time
    from sqlalchemy import text

    if _use_lakebase:
        from .lakebase import get_session
        last_err = None
        for attempt in range(4):
            db = get_session()
            try:
                db.execute(text("SELECT 1"))
                break
            except Exception as exc:
                last_err = exc
                log.warning("get_db: session probe failed (attempt %d): %s", attempt + 1, exc)
                try:
                    db.close()
                except Exception:
                    pass
                time.sleep(1.0 * (attempt + 1))
        else:
            raise RuntimeError(
                f"Could not acquire a healthy Lakebase session after retries: {last_err}"
            ) from last_err
    else:
        db = _session_factory()
    try:
        yield db
    finally:
        db.close()


def get_user_email(request: Request) -> str:
    """Extract user email from Databricks Apps headers."""
    return (
        request.headers.get("X-Forwarded-Email")
        or request.headers.get("X-Forwarded-User")
        or "anonymous"
    )
