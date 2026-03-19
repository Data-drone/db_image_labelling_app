"""
Lakebase (PostgreSQL) auto-provisioning and connection management.

On startup:
1. Find or create a Lakebase Autoscaling project via Databricks SDK
2. Get the read-write endpoint and generate a short-lived token
3. Build a SQLAlchemy engine with the connection string
4. Start a background thread to refresh the token every 30 minutes
"""

import logging
import os
import threading
import time
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LAKEBASE_PROJECT_ID = os.environ.get("LAKEBASE_PROJECT_ID", "cv-explorer")
LAKEBASE_DISPLAY_NAME = os.environ.get("LAKEBASE_DISPLAY_NAME", "CV Explorer")
TOKEN_REFRESH_INTERVAL = 20 * 60  # 20 minutes (tokens typically expire in ~1 hour)


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
_engine: Optional[Engine] = None
_session_factory = None
_token_refresh_thread: Optional[threading.Thread] = None
_current_connection_url: Optional[str] = None
_lock = threading.Lock()


def _get_workspace_client():
    """Get a WorkspaceClient using the auto-injected service principal credentials."""
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


def ensure_lakebase_project():
    """Find or create the Lakebase Autoscaling project. Returns the project object."""
    from databricks.sdk.service.postgres import Project, ProjectSpec

    w = _get_workspace_client()
    project_name = f"projects/{LAKEBASE_PROJECT_ID}"

    try:
        project = w.postgres.get_project(name=project_name)
        log.info("Connected to existing Lakebase project: %s", project.name)
        return project
    except Exception:
        log.info("Lakebase project not found, creating: %s", LAKEBASE_PROJECT_ID)

    op = w.postgres.create_project(
        project=Project(spec=ProjectSpec(display_name=LAKEBASE_DISPLAY_NAME)),
        project_id=LAKEBASE_PROJECT_ID,
    )
    project = op.wait()
    log.info("Created Lakebase project: %s", project.name)
    return project


def get_endpoint(project):
    """Find the read-write endpoint for the project via branches."""
    from databricks.sdk.service.postgres import EndpointType
    w = _get_workspace_client()

    branches = list(w.postgres.list_branches(parent=project.name))
    if not branches:
        raise RuntimeError(f"No branches found for Lakebase project {project.name}")

    all_endpoints = []
    for branch in branches:
        all_endpoints.extend(w.postgres.list_endpoints(parent=branch.name))

    if not all_endpoints:
        raise RuntimeError(f"No endpoints found for Lakebase project {project.name}")

    # Prefer read-write endpoint
    for ep in all_endpoints:
        if ep.status and ep.status.endpoint_type == EndpointType.ENDPOINT_TYPE_READ_WRITE:
            return ep
    # Fallback to first endpoint
    return all_endpoints[0]


def _get_pg_username(endpoint) -> str:
    """Determine the PostgreSQL username from the Lakebase role mapping."""
    import base64, json
    w = _get_workspace_client()
    # Get the branch from the endpoint name (e.g. projects/x/branches/y/endpoints/z -> projects/x/branches/y)
    branch_name = "/".join(endpoint.name.split("/")[:4])
    roles = list(w.postgres.list_roles(parent=branch_name))
    if roles:
        return roles[0].status.postgres_role
    # Fallback: decode JWT sub claim from a generated credential
    cred = w.postgres.generate_database_credential(endpoint=endpoint.name)
    parts = cred.token.split(".")
    payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    return claims.get("sub", "token")


def generate_connection_string(endpoint, pg_username: str = None) -> str:
    """Generate a PostgreSQL connection string using a fresh token."""
    w = _get_workspace_client()
    cred = w.postgres.generate_database_credential(endpoint=endpoint.name)
    token = cred.token
    host = endpoint.status.hosts.host
    user = pg_username or "token"
    from urllib.parse import quote
    return f"postgresql://{quote(user, safe='')}:{quote(token, safe='')}@{host}:5432/databricks_postgres?sslmode=require"


def _build_engine(connection_url: str) -> Engine:
    """Create a SQLAlchemy engine from the connection URL."""
    return create_engine(
        connection_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def _refresh_token_loop(endpoint, pg_username):
    """Background thread that refreshes the database token periodically.

    Uses exponential backoff on failure (30s, 60s, 120s, max 5min) and
    resets to normal interval after a successful refresh.
    """
    global _engine, _current_connection_url, _session_factory

    consecutive_failures = 0
    MAX_BACKOFF = 5 * 60  # 5 minutes

    while True:
        if consecutive_failures == 0:
            time.sleep(TOKEN_REFRESH_INTERVAL)
        else:
            backoff = min(30 * (2 ** (consecutive_failures - 1)), MAX_BACKOFF)
            log.warning("Lakebase token refresh retry in %ds (attempt %d)", backoff, consecutive_failures + 1)
            time.sleep(backoff)

        try:
            new_url = generate_connection_string(endpoint, pg_username)
            new_engine = _build_engine(new_url)
            # Verify the new connection actually works before swapping
            with new_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            with _lock:
                old_engine = _engine
                _engine = new_engine
                _session_factory = sessionmaker(bind=_engine)
                _current_connection_url = new_url
                if old_engine:
                    old_engine.dispose()
            consecutive_failures = 0
            log.info("Lakebase token refreshed successfully")
        except Exception:
            consecutive_failures += 1
            log.exception("Failed to refresh Lakebase token (attempt %d)", consecutive_failures)


def setup_replica_identity(engine: Engine, table_names: list[str]):
    """Set REPLICA IDENTITY FULL on each table (required for Lakehouse Sync)."""
    with engine.connect() as conn:
        for table in table_names:
            try:
                conn.execute(text(f'ALTER TABLE "{table}" REPLICA IDENTITY FULL'))
                conn.commit()
                log.info("Set REPLICA IDENTITY FULL on %s", table)
            except Exception:
                log.warning("Could not set REPLICA IDENTITY on %s (may already be set)", table)


def init_lakebase() -> Engine:
    """
    Initialize the Lakebase connection. Call this on app startup.

    Returns the SQLAlchemy engine.
    """
    global _engine, _session_factory, _token_refresh_thread, _current_connection_url

    print("[LAKEBASE] Finding project...", flush=True)
    project = ensure_lakebase_project()
    print(f"[LAKEBASE] Project: {project.name}", flush=True)
    print("[LAKEBASE] Finding endpoint...", flush=True)
    endpoint = get_endpoint(project)
    print(f"[LAKEBASE] Endpoint: {endpoint.name}", flush=True)
    print("[LAKEBASE] Resolving PostgreSQL username...", flush=True)
    pg_username = _get_pg_username(endpoint)
    print(f"[LAKEBASE] PG username: {pg_username}", flush=True)
    print("[LAKEBASE] Generating connection string...", flush=True)
    connection_url = generate_connection_string(endpoint, pg_username)
    print(f"[LAKEBASE] Got connection string (host: {endpoint.status.hosts.host})", flush=True)

    with _lock:
        _current_connection_url = connection_url
        _engine = _build_engine(connection_url)
        _session_factory = sessionmaker(bind=_engine)

    # Verify the connection actually works before returning
    print("[LAKEBASE] Testing connection...", flush=True)
    with _engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("[LAKEBASE] Connection test passed", flush=True)

    # Start background token refresh
    _token_refresh_thread = threading.Thread(
        target=_refresh_token_loop,
        args=(endpoint, pg_username),
        daemon=True,
    )
    _token_refresh_thread.start()
    log.info("Lakebase initialized, token refresh thread started")

    return _engine


def get_engine() -> Engine:
    """Return the current SQLAlchemy engine. Call init_lakebase() first."""
    if _engine is None:
        raise RuntimeError("Lakebase not initialized. Call init_lakebase() first.")
    return _engine


def get_session():
    """Return a new database session."""
    if _session_factory is None:
        raise RuntimeError("Lakebase not initialized. Call init_lakebase() first.")
    return _session_factory()
