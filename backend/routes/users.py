"""Databricks-backed user directory for project role assignment."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..models import AppUser
from ..schemas import WorkspaceUserDirectoryOut, WorkspaceUserOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


def _normalized_email(value: str | None) -> str:
    return (value or "").strip().lower()


def record_current_user(db: Session, request: Request) -> AppUser | None:
    """Record a principal proven by the authenticated Databricks Apps proxy."""
    email = _normalized_email(get_user_email(request))
    if not email or email == "anonymous":
        return None
    display_name = (
        request.headers.get("X-Forwarded-Preferred-Username")
        or request.headers.get("X-Forwarded-Email")
        or email
    ).strip()
    row = db.query(AppUser).filter_by(email=email).first()
    if row is None:
        row = AppUser(email=email)
        db.add(row)
    row.display_name = display_name
    row.last_seen_at = datetime.now(timezone.utc)
    db.flush()
    return row


def _workspace_users_from_scim() -> list[WorkspaceUserOut]:
    """List active human users with the App service principal when permitted."""
    from databricks.sdk import WorkspaceClient

    rows: dict[str, WorkspaceUserOut] = {}
    for user in WorkspaceClient().users.list(
        filter="active eq true",
        attributes="userName,displayName,active",
    ):
        if getattr(user, "active", True) is False:
            continue
        email = _normalized_email(getattr(user, "user_name", None))
        if not email or "@" not in email:
            continue
        rows[email] = WorkspaceUserOut(
            email=email,
            display_name=(getattr(user, "display_name", None) or email).strip(),
        )
    return sorted(rows.values(), key=lambda row: (row.display_name or row.email).lower())


def _can_list_workspace_directory(email: str) -> bool:
    """Workspace SCIM listing is an admin capability, not a general App user one."""
    allowed = {
        item.strip().lower()
        for item in os.environ.get("ADMIN_EMAILS", "").split(",")
        if item.strip() and item.strip().lower() != "__disabled__"
    }
    return bool(email) and email in allowed


def get_user_directory(
    db: Session,
    *,
    allow_workspace_scim: bool = True,
) -> tuple[list[WorkspaceUserOut], str, str | None]:
    if allow_workspace_scim and os.environ.get("DATABRICKS_HOST"):
        try:
            users = _workspace_users_from_scim()
            if users:
                return users, "workspace_scim", None
        except Exception as exc:
            log.info("Workspace SCIM user listing unavailable; using app visitors: %s", exc)

    users = [
        WorkspaceUserOut(email=row.email, display_name=row.display_name or row.email)
        for row in db.query(AppUser).order_by(AppUser.email).all()
    ]
    warning = (
        "Workspace directory access is unavailable. Showing active Databricks users "
        "who have already opened this app."
        if allow_workspace_scim
        else (
            "Showing Databricks users who have already opened this app. "
            "Workspace-wide user listing is limited to app admins."
        )
    )
    return users, "app_visitors", warning


def validate_active_project_users(
    db: Session,
    emails: list[str],
) -> None:
    """Reject principals that Databricks cannot establish as active."""
    requested = {_normalized_email(email) for email in emails if _normalized_email(email)}
    if not requested or not os.environ.get("DATABRICKS_HOST"):
        return
    users, source, _ = get_user_directory(db, allow_workspace_scim=True)
    active = {row.email for row in users}
    missing = sorted(requested - active)
    if missing:
        source_hint = (
            "active workspace directory"
            if source == "workspace_scim"
            else "users who have authenticated to this app"
        )
        raise HTTPException(
            status_code=422,
            detail=(
                "These users could not be verified against the "
                f"{source_hint}: {', '.join(missing)}. Grant them CAN USE on the "
                "Databricks App and ask them to open it once, then try again."
            ),
        )


@router.get("/directory", response_model=WorkspaceUserDirectoryOut)
def directory(request: Request, db: Session = Depends(get_db)):
    email = _normalized_email(get_user_email(request))
    if (
        (not email or email == "anonymous")
        and (os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_CLIENT_ID"))
    ):
        raise HTTPException(
            status_code=401,
            detail="Databricks user identity is required to list app users.",
        )
    record_current_user(db, request)
    users, source, warning = get_user_directory(
        db,
        allow_workspace_scim=_can_list_workspace_directory(email),
    )
    db.commit()
    return WorkspaceUserDirectoryOut(users=users, source=source, warning=warning)


@router.get("/me", response_model=WorkspaceUserOut)
def me(request: Request, db: Session = Depends(get_db)):
    row = record_current_user(db, request)
    if row is None:
        raise HTTPException(status_code=401, detail="Databricks user identity is unavailable.")
    result = WorkspaceUserOut(email=row.email, display_name=row.display_name)
    db.commit()
    return result
