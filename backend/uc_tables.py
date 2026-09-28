"""Unity Catalog Delta table as a labeling sample catalog.

Lists ``image_path`` values via SQL warehouse statement execution and
inserts ``ProjectSample`` rows. Image bytes are still read with the Files
API from those paths — this module never SELECTs BINARY columns.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from .models import ProjectSample
from .volumes import _get_workspace_client

log = logging.getLogger(__name__)

_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_FQN_PART = re.compile(r"^[A-Za-z0-9_\-]+$")
_FILTER_FORBIDDEN = re.compile(
    r";|--|/\*|\*/|\b(union|insert|update|delete|drop|alter|merge|copy|grant|revoke)\b",
    re.IGNORECASE,
)
DEFAULT_MAX_ROWS = 50_000


class TableSourceError(Exception):
    """User-facing failure while scanning a UC table for samples."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class TableScanResult:
    added: int = 0
    skipped_existing: int = 0
    volume_hint: Optional[str] = None


def warehouse_id() -> str:
    return (
        os.environ.get("SQL_WAREHOUSE_ID", "").strip()
        or os.environ.get("DATABRICKS_WAREHOUSE_ID", "").strip()
    )


def max_table_source_rows() -> int:
    raw = os.environ.get("TABLE_SOURCE_MAX_ROWS", "").strip()
    if not raw:
        return DEFAULT_MAX_ROWS
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_MAX_ROWS
    return n if n > 0 else DEFAULT_MAX_ROWS


def parse_table_fqn(name: str) -> tuple[str, str, str]:
    """Split ``catalog.schema.table`` (optional backticks) into three parts."""
    if not name or not str(name).strip():
        raise ValueError("table name is required")
    raw = str(name).strip()
    parts = []
    for part in raw.split("."):
        p = part.strip().strip("`")
        if not p or not _FQN_PART.match(p):
            raise ValueError(
                f"invalid Unity Catalog table name '{name}'; "
                "use catalog.schema.table"
            )
        parts.append(p)
    if len(parts) != 3:
        raise ValueError(
            f"invalid Unity Catalog table name '{name}'; "
            "use catalog.schema.table"
        )
    return parts[0], parts[1], parts[2]


def quote_uc_id(part: str) -> str:
    return "`" + part.replace("`", "") + "`"


def validate_path_column(name: str) -> str:
    col = (name or "image_path").strip()
    if not _IDENT.match(col):
        raise ValueError(
            "image_path_column must be a simple identifier "
            "(letters, digits, underscore)"
        )
    return col


def validate_source_filter(sql: Optional[str]) -> Optional[str]:
    """Return a WHERE body or None. Rejectes multi-statement / DDL fragments."""
    if sql is None:
        return None
    body = str(sql).strip()
    if not body:
        return None
    if _FILTER_FORBIDDEN.search(body):
        raise ValueError(
            "source_filter must be a simple WHERE predicate "
            "(no semicolons, comments, or SQL keywords like UNION/DDL)"
        )
    return body


def normalize_image_path(path) -> str:
    """Turn Auto Loader / Spark volume paths into Files API ``/Volumes/...`` paths.

    Auto Loader ``cloudFiles`` / ``binaryFile`` often emit
    ``dbfs:/Volumes/catalog/schema/volume/file.jpg`` (sometimes with extra
    slashes). The labeling app reads bytes via the Files API, which wants
    ``/Volumes/...``.
    """
    if not isinstance(path, str):
        return ""
    p = path.strip().strip('"').strip("'")
    lower = p.lower()
    for prefix in ("dbfs:", "file:"):
        if lower.startswith(prefix):
            p = p[len(prefix):]
            break
    p = p.replace("\\", "/")
    idx = p.find("/Volumes/")
    if idx > 0:
        p = p[idx:]
    return p


def validate_image_path(path) -> str:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("image_path must be a non-empty string")
    path = normalize_image_path(path)
    if "\\" in path or ".." in path.split("/"):
        raise ValueError(f"invalid image_path '{path}'")
    if not path.startswith("/Volumes/"):
        raise ValueError(f"image_path must start with /Volumes/, got '{path}'")
    parts = path.split("/")
    if len(parts) < 6 or any(p in ("", ".", "..") for p in parts[1:]):
        raise ValueError(
            f"image_path must be /Volumes/catalog/schema/volume/file..., got '{path}'"
        )
    return path


def volume_hint_from_path(path: str) -> str:
    """``/Volumes/cat/sch/vol`` from a file path under that volume."""
    parts = path.split("/")
    if len(parts) >= 5:
        return "/".join(parts[:5])
    return path


def _execute_sql(statement: str, *, wait_timeout: str = "50s"):
    wid = warehouse_id()
    if not wid:
        raise TableSourceError(
            "SQL_WAREHOUSE_ID is not configured; cannot scan a Delta table. "
            "Set it on the app (SQL warehouse the service principal can use).",
            status_code=400,
        )
    w = _get_workspace_client()
    try:
        resp = w.statement_execution.execute_statement(
            warehouse_id=wid,
            statement=statement,
            wait_timeout=wait_timeout,
        )
    except TableSourceError:
        raise
    except Exception as e:
        raise TableSourceError(f"warehouse query failed: {e}", status_code=502) from e
    state = ""
    status = getattr(resp, "status", None)
    if status is not None:
        st = getattr(status, "state", None)
        state = str(getattr(st, "value", st) or "")
        err = getattr(status, "error", None)
        if err is not None and (
            "FAILED" in state.upper() or "CANCELED" in state.upper()
        ):
            msg = getattr(err, "message", None) or str(err)
            raise TableSourceError(f"warehouse query failed: {msg}", status_code=502)
    if "FAILED" in state.upper() or "CANCELED" in state.upper():
        raise TableSourceError(
            f"warehouse query failed (state={state})", status_code=502
        )
    return w, resp


def _iter_first_column(w, resp):
    result = getattr(resp, "result", None)
    data = getattr(result, "data_array", None) if result is not None else None
    if data:
        for row in data:
            yield row[0] if row else None
    statement_id = getattr(resp, "statement_id", None)
    next_idx = getattr(result, "next_chunk_index", None) if result is not None else None
    while statement_id and next_idx is not None:
        chunk = w.statement_execution.get_statement_result_chunk_n(
            statement_id=statement_id,
            chunk_index=next_idx,
        )
        chunk_data = getattr(chunk, "data_array", None)
        if chunk_data:
            for row in chunk_data:
                yield row[0] if row else None
        next_idx = getattr(chunk, "next_chunk_index", None)


def count_table_rows(full_name: str, source_filter: Optional[str] = None) -> Optional[int]:
    """Best-effort COUNT(*). Returns None if the warehouse is not configured."""
    if not warehouse_id():
        return None
    cat, sch, tbl = parse_table_fqn(full_name)
    fqn = f"{quote_uc_id(cat)}.{quote_uc_id(sch)}.{quote_uc_id(tbl)}"
    where = validate_source_filter(source_filter)
    sql = f"SELECT COUNT(*) FROM {fqn}"
    if where:
        sql += f" WHERE {where}"
    _w, resp = _execute_sql(sql, wait_timeout="50s")
    result = getattr(resp, "result", None)
    data = getattr(result, "data_array", None) if result is not None else None
    if not data or not data[0]:
        return None
    try:
        return int(data[0][0])
    except (TypeError, ValueError, IndexError):
        return None


def sample_table_paths(
    full_name: str,
    path_column: str = "image_path",
    source_filter: Optional[str] = None,
    limit: int = 12,
) -> list[str]:
    """Return a small list of normalized ``/Volumes/...`` paths for browse UI."""
    if not warehouse_id():
        return []
    n = max(0, min(int(limit), 50))
    if n == 0:
        return []
    cat, sch, tbl = parse_table_fqn(full_name)
    col = validate_path_column(path_column)
    where = validate_source_filter(source_filter)
    fqn = f"{quote_uc_id(cat)}.{quote_uc_id(sch)}.{quote_uc_id(tbl)}"
    sql = f"SELECT {quote_uc_id(col)} FROM {fqn}"
    if where:
        sql += f" WHERE {where}"
    sql += f" LIMIT {n}"
    w, resp = _execute_sql(sql, wait_timeout="50s")
    out = []
    seen = set()
    for raw in _iter_first_column(w, resp):
        try:
            path = validate_image_path(raw)
        except ValueError:
            continue
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
        if len(out) >= n:
            break
    return out


def scan_table_for_samples(
    db: Session,
    project_id: int,
    full_name: str,
    path_column: str = "image_path",
    source_filter: Optional[str] = None,
) -> TableScanResult:
    """Insert ProjectSample rows from a UC table.

    Existing filepaths on the project are left alone (incremental Auto Loader
    sync). Returns added / skipped counts plus a volume-root hint.
    """
    cat, sch, tbl = parse_table_fqn(full_name)
    col = validate_path_column(path_column)
    where = validate_source_filter(source_filter)
    fqn = f"{quote_uc_id(cat)}.{quote_uc_id(sch)}.{quote_uc_id(tbl)}"
    sql = f"SELECT {quote_uc_id(col)} FROM {fqn}"
    if where:
        sql += f" WHERE {where}"

    cap = max_table_source_rows()
    w, resp = _execute_sql(sql)

    existing = {
        normalize_image_path(fp)
        for (fp,) in db.query(ProjectSample.filepath).filter_by(project_id=project_id)
        if fp
    }
    seen: set[str] = set()
    result = TableScanResult()
    n_read = 0
    for raw in _iter_first_column(w, resp):
        n_read += 1
        if n_read > cap:
            raise TableSourceError(
                f"table {full_name} returned more than {cap} rows; "
                "narrow source_filter or raise TABLE_SOURCE_MAX_ROWS",
                status_code=400,
            )
        try:
            path = validate_image_path(raw)
        except ValueError as e:
            raise TableSourceError(str(e), status_code=400) from e
        if path in seen:
            continue
        seen.add(path)
        if result.volume_hint is None:
            result.volume_hint = volume_hint_from_path(path)
        if path in existing:
            result.skipped_existing += 1
            continue
        db.add(ProjectSample(
            project_id=project_id,
            filepath=path,
            filename=os.path.basename(path),
        ))
        existing.add(path)
        result.added += 1
    log.info(
        "scanned table %s for project %s: added=%d skipped_existing=%d hint=%s",
        full_name, project_id, result.added, result.skipped_existing, result.volume_hint,
    )
    return result


def labeled_table_fqn(source_table: str, project_id: int, snapshot_id: str) -> str:
    """Derive a unique managed-table name in the source table's schema."""
    catalog, schema, table = parse_table_fqn(source_table)
    snapshot = re.sub(r"[^A-Za-z0-9_]+", "_", snapshot_id or "export").strip("_")[:16]
    name = f"{table}_labeled_p{int(project_id)}_{snapshot}"
    return f"{catalog}.{schema}.{name}"


def _labeled_table_sql(
    source_table: str,
    destination_table: str,
    path_column: str,
    label_jsonl_path: str,
) -> str:
    s_cat, s_schema, s_table = parse_table_fqn(source_table)
    d_cat, d_schema, d_table = parse_table_fqn(destination_table)
    column = validate_path_column(path_column)
    label_path = validate_image_path(label_jsonl_path).replace("'", "''")
    source = ".".join(quote_uc_id(p) for p in (s_cat, s_schema, s_table))
    destination = ".".join(quote_uc_id(p) for p in (d_cat, d_schema, d_table))
    source_column = quote_uc_id(column)
    return (
        f"CREATE OR REPLACE TABLE {destination} "
        f"COMMENT 'CV Explorer labeled training snapshot from {s_cat}.{s_schema}.{s_table}' AS "
        f"SELECT src.*, "
        f"lab.annotations AS cv_annotations, "
        f"lab.project_id AS cv_project_id, "
        f"lab.snapshot_id AS cv_snapshot_id, "
        f"lab.exported_at AS cv_exported_at "
        f"FROM {source} src "
        f"INNER JOIN ("
        f"SELECT image_path, annotations, project_id, snapshot_id, exported_at "
        f"FROM read_files('{label_path}', format => 'json')"
        f") lab "
        f"ON regexp_replace(CAST(src.{source_column} AS STRING), '^dbfs:', '') "
        f"= CAST(lab.image_path AS STRING)"
    )


def attach_labeled_delta_table(
    project,
    export_dir: str,
    rows: list[dict],
    workspace,
    metadata,
) -> Optional[str]:
    """Publish labels as Delta while keeping image bytes in the source Volume."""
    if (getattr(project, "source_type", None) or "volume") != "table":
        return None
    if not getattr(project, "source_table", None):
        return None
    lineage = metadata.setdefault("lineage", {})
    try:
        if not rows:
            raise ValueError("no labeled rows to publish")
        snapshot_id = str(metadata.get("snapshot_id") or "export")
        destination = labeled_table_fqn(
            project.source_table, project.id, snapshot_id,
        )
        label_jsonl_path = f"{export_dir.rstrip('/')}/lineage.jsonl"
        enriched_rows = [
            {
                **row,
                "project_id": project.id,
                "snapshot_id": snapshot_id,
                "exported_at": metadata.get("exported_at"),
            }
            for row in rows
        ]
        payload = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in enriched_rows
        )
        workspace.files.upload(
            label_jsonl_path,
            io.BytesIO(payload.encode("utf-8")),
            overwrite=True,
        )
        sql = _labeled_table_sql(
            project.source_table,
            destination,
            getattr(project, "image_path_column", None) or "image_path",
            label_jsonl_path,
        )
        _execute_sql(sql, wait_timeout="50s")
        lineage["labeled_table_uc"] = destination
        lineage["label_jsonl_path"] = label_jsonl_path
        log.info("published labeled Delta table %s", destination)
        return destination
    except Exception as exc:
        msg = str(exc)
        log.warning("labeled Delta table not created: %s", msg)
        lineage["labeled_table_error"] = msg
        return None
