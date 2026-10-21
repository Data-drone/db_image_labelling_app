"""
Admin routes — database status, Lakebase provisioning.
"""

import logging
import os

from fastapi import APIRouter, HTTPException, Request

from ..deps import get_engine, get_session_factory
from ..models import Base, LabelingProject, ProjectSample, Annotation, init_db
from ..volumes import _get_workspace_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _admin_emails() -> set[str]:
    return {
        email.strip().lower()
        for email in os.environ.get("ADMIN_EMAILS", "").split(",")
        if email.strip()
    }


def _is_admin(request: Request) -> bool:
    allowed = _admin_emails()
    email = request.headers.get("X-Forwarded-Email", "").strip().lower()
    return bool(allowed) and bool(email) and email in allowed


def _require_admin(request: Request) -> str:
    email = request.headers.get("X-Forwarded-Email", "").strip().lower()
    if not _admin_emails():
        raise HTTPException(
            status_code=403,
            detail="Admin mutations are disabled. Configure ADMIN_EMAILS to enable them.",
        )
    if not email:
        raise HTTPException(
            status_code=403,
            detail="Databricks Apps user email is required for Admin access.",
        )
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return email


@router.get("/db-status")
def admin_db_status(request: Request):
    """Return current database backend info."""
    engine = get_engine()
    engine_url = str(engine.url) if engine else "none"
    is_sqlite = "sqlite" in engine_url
    is_lakebase = (
        "postgresql" in engine_url
        and (
            "databricks" in engine_url
            or bool(os.environ.get("PGHOST"))
        )
    )

    result = {
        "backend": "sqlite" if is_sqlite else ("lakebase" if is_lakebase else "postgresql"),
        "connected": engine is not None,
        "admin_mutations_enabled": _is_admin(request),
    }

    if is_sqlite:
        result["detail"] = "Using local SQLite (data lost on redeploy). Provision Lakebase for persistent storage."
        result["path"] = engine_url.replace("sqlite:///", "")
    elif is_lakebase:
        result["detail"] = "Connected to Lakebase (PostgreSQL). Data persists across redeploys."
        result["host"] = engine.url.host if engine else ""

    if engine:
        factory = get_session_factory()
        db = factory()
        try:
            result["tables"] = {
                "projects": db.query(LabelingProject).count(),
                "samples": db.query(ProjectSample).count(),
                "annotations": db.query(Annotation).count(),
            }
        finally:
            db.close()

    return result


@router.get("/lakebase-status")
def admin_lakebase_status():
    """Check if Lakebase projects exist and return their status."""
    try:
        w = _get_workspace_client()
        projects = []
        for p in w.postgres.list_projects():
            proj_id = p.name.replace("projects/", "")
            display = p.status.display_name if p.status else (p.spec.display_name if p.spec else proj_id)
            owner = p.status.owner if p.status else ""
            projects.append({
                "name": p.name,
                "display_name": display,
                "owner": owner,
                "state": "ACTIVE",
            })
        return {"available": True, "projects": projects}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lakebase-project/{project_id}")
def admin_lakebase_project_detail(project_id: str):
    """Get endpoint details for a specific Lakebase project."""
    try:
        w = _get_workspace_client()
        full_name = f"projects/{project_id}"
        branches = list(w.postgres.list_branches(parent=full_name))
        endpoints_info = []
        for branch in branches:
            for ep in w.postgres.list_endpoints(parent=branch.name):
                info = {"name": ep.name, "branch": branch.name}
                if ep.status:
                    info["state"] = str(ep.status.current_state).replace("EndpointStatusState.", "")
                    info["type"] = str(ep.status.endpoint_type).replace("EndpointType.", "")
                    if ep.status.hosts:
                        info["host"] = ep.status.hosts.host
                endpoints_info.append(info)
        return {"project_id": project_id, "endpoints": endpoints_info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/provision-lakebase")
def admin_provision_lakebase(body: dict, request: Request):
    """Provision a new Lakebase project or connect to an existing one."""
    _require_admin(request)
    project_id = (body.get("project_id") or "cv-explorer").strip()
    display_name = (body.get("display_name") or "CV Explorer").strip()

    try:
        w = _get_workspace_client()
        from databricks.sdk.service.postgres import Project, ProjectSpec

        full_name = f"projects/{project_id}"
        try:
            existing = w.postgres.get_project(name=full_name)
            return {
                "status": "exists",
                "message": f"Lakebase project '{project_id}' already exists.",
                "project_name": existing.name,
                "state": str(existing.status.state) if existing.status else "unknown",
            }
        except Exception:
            pass

        w.postgres.create_project(
            project=Project(spec=ProjectSpec(display_name=display_name)),
            project_id=project_id,
        )
        return {
            "status": "creating",
            "message": f"Lakebase project '{project_id}' is being created. This may take a few minutes.",
            "project_id": project_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset-database")
def admin_reset_database(request: Request):
    """Drop all tables and recreate them (destructive — dev/test only)."""
    _require_admin(request)
    engine = get_engine()
    if engine is None:
        raise HTTPException(status_code=500, detail="No database engine available")

    log.warning("ADMIN: dropping all tables and recreating schema")
    Base.metadata.drop_all(engine)
    init_db(engine)
    return {"status": "ok", "message": "All tables dropped and recreated."}


@router.post("/connect-lakebase")
def admin_connect_lakebase(body: dict, request: Request):
    """Switch the app's database backend to an existing Lakebase project."""
    _require_admin(request)
    from ..deps import configure_db
    from ..lakebase import (
        connect_endpoint,
        get_endpoint,
        get_engine as lakebase_get_engine,
        get_session_factory as lakebase_get_session_factory,
        prepare_lakebase_schema,
        resolve_branch_name,
        setup_replica_identity,
    )
    from ..models import TABLE_NAMES

    project_id = (body.get("project_id") or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    branch_id = (body.get("branch_id") or body.get("branch") or "").strip() or None

    try:
        w = _get_workspace_client()

        full_name = f"projects/{project_id}"
        project = w.postgres.get_project(name=full_name)
        branch = resolve_branch_name(project, branch_id)
        endpoint = get_endpoint(project, branch_name=branch)
        previous_engine = get_engine()

        def before_activate(new_engine):
            prepare_lakebase_schema(new_engine)
            Base.metadata.create_all(new_engine)
            try:
                setup_replica_identity(new_engine, TABLE_NAMES)
            except Exception as exc:
                log.warning("Replica identity setup: %s", exc)

        connect_endpoint(endpoint, before_activate=before_activate)
        live_engine = lakebase_get_engine()
        configure_db(
            live_engine,
            lakebase_get_session_factory(),
            use_lakebase=True,
        )
        if previous_engine is not None and previous_engine is not live_engine:
            previous_engine.dispose()

        host = endpoint.status.hosts.host if endpoint.status and endpoint.status.hosts else ""
        return {
            "status": "connected",
            "message": f"Connected to Lakebase project '{project_id}'. Tables created.",
            "host": host,
            "endpoint": endpoint.name,
            "branch": branch,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/test-serving")
def admin_test_serving(request: Request):
    """Quick check: send first sample through the model and return the raw response."""
    _require_admin(request)
    from ..inference import resolve_use_data_plane, predict_sample
    from ..deps import get_db
    import traceback

    w = _get_workspace_client()
    auth_type = w.config.auth_type

    db = next(get_db())
    from ..models import LabelingProject, ProjectSample
    project = db.query(LabelingProject).first()
    if not project:
        return {"status": "error", "error": "No projects in DB"}

    sample = db.query(ProjectSample).filter_by(project_id=project.id).first()
    if not sample:
        return {"status": "error", "error": "No samples in DB"}

    from ..volumes import read_image_bytes
    img = read_image_bytes(sample.filepath)
    if not img:
        return {"status": "error", "error": f"Cannot read {sample.filepath}"}

    try:
        preds = predict_sample(
            endpoint_name=project.serving_endpoint,
            image_bytes=img,
            task_type=project.task_type,
            class_list=list(project.class_list),
            endpoint_config=project.endpoint_config,
        )
        return {
            "status": "ok",
            "endpoint": project.serving_endpoint,
            "auth_type": auth_type,
            "use_data_plane": resolve_use_data_plane(project.endpoint_config),
            "adapter": (project.endpoint_config or {}).get("adapter", "generic"),
            "predictions": preds[:10],
            "sample_filepath": sample.filepath,
        }
    except Exception as e:
        return {
            "status": "error",
            "endpoint": project.serving_endpoint,
            "auth_type": auth_type,
            "error": str(e)[:500],
            "traceback": traceback.format_exc()[-1000:],
        }
