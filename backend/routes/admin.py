"""
Admin routes — database status, Lakebase provisioning.
"""

import logging

from fastapi import APIRouter, HTTPException

from ..deps import get_engine, get_session_factory
from ..models import Base, LabelingProject, ProjectSample, Annotation
from ..volumes import _get_workspace_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/db-status")
def admin_db_status():
    """Return current database backend info."""
    engine = get_engine()
    engine_url = str(engine.url) if engine else "none"
    is_sqlite = "sqlite" in engine_url
    is_lakebase = "postgresql" in engine_url and "databricks" in engine_url

    result = {
        "backend": "sqlite" if is_sqlite else ("lakebase" if is_lakebase else "postgresql"),
        "connected": engine is not None,
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
def admin_provision_lakebase(body: dict):
    """Provision a new Lakebase project or connect to an existing one."""
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


@router.post("/connect-lakebase")
def admin_connect_lakebase(body: dict):
    """Switch the app's database backend to an existing Lakebase project."""
    from ..deps import configure_db

    project_id = (body.get("project_id") or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")

    try:
        w = _get_workspace_client()

        full_name = f"projects/{project_id}"
        project = w.postgres.get_project(name=full_name)

        branches = list(w.postgres.list_branches(parent=project.name))
        if not branches:
            raise HTTPException(status_code=400, detail="No branches found for this project.")
        endpoint = None
        for branch in branches:
            endpoints = list(w.postgres.list_endpoints(parent=branch.name))
            if endpoints:
                endpoint = endpoints[0]
                break
        if not endpoint:
            raise HTTPException(status_code=400, detail="No endpoints found for this project.")

        cred = w.postgres.generate_database_credential(endpoint=endpoint.name)
        host = endpoint.status.hosts.host
        conn_url = f"postgresql://token:{cred.token}@{host}:5432/databricks_postgres"

        from sqlalchemy import create_engine
        new_engine = create_engine(conn_url, echo=False, pool_pre_ping=True, pool_size=5, max_overflow=10)
        Base.metadata.create_all(new_engine)

        try:
            from ..lakebase import setup_replica_identity
            setup_replica_identity(new_engine, ["labeling_projects", "project_samples", "annotations"])
        except Exception as e:
            log.warning("Replica identity setup: %s", e)

        old_engine = get_engine()
        new_factory = sessionmaker(bind=new_engine)
        configure_db(new_engine, new_factory, use_lakebase=False)
        if old_engine:
            old_engine.dispose()

        return {
            "status": "connected",
            "message": f"Connected to Lakebase project '{project_id}'. Tables created.",
            "host": host,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
