"""
Unity Catalog Volume browsing helpers.

Uses the Databricks SDK when running on Databricks, with a local-folder
fallback for development.
"""

import os
from typing import Optional

import streamlit as st

from utils.config import ON_DATABRICKS, MEDIA_EXTENSIONS, is_image


# ---------------------------------------------------------------------------
# Databricks SDK helpers
# ---------------------------------------------------------------------------

def _get_workspace_client():
    """Return a Databricks WorkspaceClient (cached per session)."""
    if "db_client" not in st.session_state:
        try:
            from databricks.sdk import WorkspaceClient
            st.session_state.db_client = WorkspaceClient()
        except Exception as exc:
            st.error(f"Could not create Databricks client: {exc}")
            st.session_state.db_client = None
    return st.session_state.db_client


def list_catalogs() -> list[str]:
    """Return a sorted list of catalog names the user can see."""
    if not ON_DATABRICKS:
        return []
    w = _get_workspace_client()
    if w is None:
        return []
    try:
        return sorted(c.name for c in w.catalogs.list())
    except Exception as exc:
        st.error(f"Error listing catalogs: {exc}")
        return []


def list_schemas(catalog: str) -> list[str]:
    """Return a sorted list of schema names inside *catalog*."""
    if not ON_DATABRICKS or not catalog:
        return []
    w = _get_workspace_client()
    if w is None:
        return []
    try:
        return sorted(s.name for s in w.schemas.list(catalog_name=catalog))
    except Exception as exc:
        st.error(f"Error listing schemas: {exc}")
        return []


def list_volumes(catalog: str, schema: str) -> list[str]:
    """Return a sorted list of volume names inside *catalog.schema*."""
    if not ON_DATABRICKS or not catalog or not schema:
        return []
    w = _get_workspace_client()
    if w is None:
        return []
    try:
        return sorted(v.name for v in w.volumes.list(catalog_name=catalog, schema_name=schema))
    except Exception as exc:
        st.error(f"Error listing volumes: {exc}")
        return []


# ---------------------------------------------------------------------------
# File-system browsing (works on DBFS paths or local)
# ---------------------------------------------------------------------------

def list_directory(path: str) -> tuple[list[str], list[str]]:
    """
    List folders and media files at *path*.

    Returns (folders, files) where both are sorted lists of **basenames**.
    """
    folders: list[str] = []
    files: list[str] = []
    try:
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                folders.append(entry)
            elif is_media_file(entry):
                files.append(entry)
    except Exception as exc:
        st.error(f"Cannot read directory: {exc}")
    return folders, files


def is_media_file(name: str) -> bool:
    """Check whether *name* has a media extension."""
    return os.path.splitext(name.lower())[1] in MEDIA_EXTENSIONS


def count_images_in(path: str) -> int:
    """Recursively count image files under *path*."""
    total = 0
    try:
        for root, _dirs, filenames in os.walk(path):
            total += sum(1 for f in filenames if is_image(f))
    except Exception:
        pass
    return total


def get_local_folders(start: Optional[str] = None) -> list[str]:
    """
    Fallback: list top-level directories for local development.
    """
    start = start or os.path.expanduser("~")
    try:
        return sorted(
            d for d in os.listdir(start) if os.path.isdir(os.path.join(start, d))
        )
    except Exception:
        return []
