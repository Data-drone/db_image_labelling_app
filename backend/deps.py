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
    return _engine


def get_session_factory():
    return _session_factory


def get_db():
    """Yield a database session."""
    if _use_lakebase:
        from .lakebase import get_session
        db = get_session()
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
