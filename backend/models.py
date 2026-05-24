"""
SQLAlchemy models for CV Explorer — Phase 1 (Lakebase).

Tables:
- labeling_projects: project metadata
- project_samples: images within a project
- annotations: labels applied to samples
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, deferred, relationship

log = logging.getLogger(__name__)

EMBEDDING_DIM = 1024  # DINOv3 output dimension


class Base(DeclarativeBase):
    pass


class LabelingProject(Base):
    __tablename__ = "labeling_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, default="")
    task_type = Column(String(50), nullable=False)  # 'classification' or 'detection'
    class_list = Column(JSON, nullable=False)  # e.g. ["cat", "dog", "car"]
    source_volume = Column(Text, nullable=False)  # UC Volume path
    serving_endpoint = Column(String(255), nullable=True)  # Model Serving endpoint name
    endpoint_config = Column(JSON, nullable=True)  # response parsing overrides
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    version = Column(Integer, default=1, nullable=False)
    parent_project_id = Column(Integer, nullable=True)  # FK to parent version (null = original)

    samples = relationship(
        "ProjectSample", back_populates="project", cascade="all, delete-orphan",
    )
    annotations = relationship(
        "Annotation", back_populates="project", cascade="all, delete-orphan",
    )


class ProjectSample(Base):
    __tablename__ = "project_samples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    filepath = Column(Text, nullable=False)
    filename = Column(String(512), nullable=False)
    locked_by = Column(String(255), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="unlabeled", nullable=False)  # unlabeled, pre_labeled, labeled, skipped
    embedding = deferred(Column(JSON, nullable=True))
    # Native pgvector column for indexed similarity search (Postgres only).
    # On SQLite this column is skipped; the JSON `embedding` column is used as fallback.
    embedding_vec = deferred(Column(JSON, nullable=True))  # placeholder type; overridden at runtime for Postgres
    prediction_confidence = Column(Float, nullable=True)

    # Cached 2D UMAP projection coordinates are stored as umap_x/umap_y
    # columns in the DB but NOT mapped here — they're managed via raw SQL in
    # the cluster-map endpoint to avoid breaking queries when the columns
    # can't be added (e.g. insufficient table ownership on Lakebase).

    # Cached 2D UMAP projection coordinates are stored as umap_x/umap_y
    # columns in the DB but NOT mapped here — they're managed via raw SQL in
    # the cluster-map endpoint to avoid breaking queries when the columns
    # can't be added (e.g. insufficient table ownership on Lakebase).

    project = relationship("LabelingProject", back_populates="samples")
    annotations = relationship(
        "Annotation", back_populates="sample", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_project_samples_project_status", "project_id", "status"),
    )


class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sample_id = Column(Integer, ForeignKey("project_samples.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    label = Column(String(255), nullable=False)
    ann_type = Column(String(50), nullable=False)  # 'classification' or 'bbox'
    bbox_json = Column(JSON, nullable=True)  # {"x":..,"y":..,"w":..,"h":..}
    is_draft = Column(Boolean, nullable=False, default=False)  # model suggestions vs human-confirmed
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    sample = relationship("ProjectSample", back_populates="annotations")
    project = relationship("LabelingProject", back_populates="annotations")

    __table_args__ = (
        Index("ix_annotations_project", "project_id"),
        Index("ix_annotations_sample", "sample_id"),
    )


class PreannotateRun(Base):
    """Tracks async (Databricks Job) pre-annotation batch work."""

    __tablename__ = "preannotate_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    # queued = job submitted; running = worker executing; terminal: succeeded | failed | cancelled
    max_samples = Column(Integer, default=0, nullable=False)  # 0 = all matching samples
    include_pre_labeled = Column(Boolean, default=False, nullable=False)
    min_confidence = Column(Float, nullable=True)
    text_prompt = Column(Text, nullable=True)
    completed = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    skipped = Column(Integer, default=0, nullable=False)
    total_planned = Column(Integer, default=0, nullable=False)
    databricks_run_id = Column(BigInteger, nullable=True)  # jobs.run_now response (run id)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_preannotate_runs_project", "project_id"),
        Index("ix_preannotate_runs_status", "status"),
    )


class EmbeddingRun(Base):
    """Tracks in-app embedding generation runs so progress survives page reloads."""

    __tablename__ = "embedding_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    status = Column(String(32), nullable=False, default="running")
    completed = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    skipped = Column(Integer, default=0, nullable=False)
    total_planned = Column(Integer, default=0, nullable=False)
    force = Column(Boolean, default=False, nullable=False)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_embedding_runs_project", "project_id"),
    )


class FinetuneRun(Base):
    """Tracks async (Databricks Job) finetuning runs triggered after dataset export."""

    __tablename__ = "finetune_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    export_path = Column(Text, nullable=False)
    databricks_run_id = Column(BigInteger, nullable=True)
    databricks_run_url = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_finetune_runs_project", "project_id"),
        Index("ix_finetune_runs_status", "status"),
    )


class AnnotationHistory(Base):
    __tablename__ = "annotation_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sample_id = Column(Integer, ForeignKey("project_samples.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(Integer, ForeignKey("labeling_projects.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(50), nullable=False)  # 'create', 'update', 'delete'
    old_label = Column(String(255), nullable=True)
    new_label = Column(String(255), nullable=True)
    old_ann_type = Column(String(50), nullable=True)
    new_ann_type = Column(String(50), nullable=True)
    old_bbox_json = Column(JSON, nullable=True)
    new_bbox_json = Column(JSON, nullable=True)
    changed_by = Column(String(255), default="")
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_annotation_history_sample", "sample_id"),
        Index("ix_annotation_history_project", "project_id"),
    )


# ---------------------------------------------------------------------------
# Table management
# ---------------------------------------------------------------------------
TABLE_NAMES = [
    "labeling_projects",
    "project_samples",
    "annotations",
    "annotation_history",
    "preannotate_runs",
    "embedding_runs",
    "finetune_runs",
]


def ensure_preannotate_runs_table(engine) -> None:
    """Create ``preannotate_runs`` on existing DBs if missing (no Alembic)."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    if "preannotate_runs" in insp.get_table_names():
        return
    PreannotateRun.__table__.create(bind=engine, checkfirst=True)
    log.info("preannotate_runs table created")


def ensure_annotations_is_draft_column(engine) -> None:
    """Additive migration: ``annotations.is_draft`` for model vs human labels (no Alembic)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "annotations" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("annotations")}
    if "is_draft" in cols:
        return

    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "postgresql":
            conn.execute(
                text(
                    "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS is_draft "
                    "BOOLEAN NOT NULL DEFAULT false"
                )
            )
        else:
            conn.execute(
                text(
                    "ALTER TABLE annotations ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0"
                )
            )
    with engine.begin() as conn:
        if dialect == "postgresql":
            conn.execute(
                text(
                    "UPDATE annotations SET is_draft = true "
                    "WHERE created_by LIKE 'model:%'"
                )
            )
        else:
            conn.execute(
                text(
                    "UPDATE annotations SET is_draft = 1 "
                    "WHERE created_by LIKE 'model:%'"
                )
            )
    log.info("annotations.is_draft column added and model rows backfilled")


def _reassign_table_ownership(engine, table_names: list[str]) -> None:
    """Reassign ownership of tables to the current Postgres user.

    On Lakebase, tables created by a previous service principal identity
    are not owned by the current one. ALTER TABLE requires ownership.
    Tries multiple strategies: direct OWNER TO, then REASSIGN OWNED BY
    for each discovered previous owner.
    """
    if engine.dialect.name != "postgresql":
        return

    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT current_user")).fetchone()
            current_user = row[0]
    except Exception:
        return

    failed_tables = []
    for table in table_names:
        try:
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE IF EXISTS "{table}" OWNER TO "{current_user}"'))
            log.info("Reassigned ownership of %s to %s", table, current_user)
        except Exception:
            failed_tables.append(table)

    if not failed_tables:
        return

    # Strategy 2: find distinct owners of tables we couldn't reassign, then
    # REASSIGN OWNED BY <old_owner> TO <current_user>
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT tableowner FROM pg_tables "
                "WHERE tablename = ANY(:tables) AND tableowner != :me"
            ), {"tables": failed_tables, "me": current_user}).fetchall()
            old_owners = [r[0] for r in rows]
    except Exception:
        old_owners = []

    for old_owner in old_owners:
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f'REASSIGN OWNED BY "{old_owner}" TO "{current_user}"'
                ))
            log.info("Reassigned all objects owned by %s to %s", old_owner, current_user)
        except Exception as e:
            log.warning("Could not REASSIGN OWNED BY %s: %s", old_owner, e)

    # Retry failed tables after REASSIGN
    for table in failed_tables:
        try:
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE IF EXISTS "{table}" OWNER TO "{current_user}"'))
            log.info("Reassigned ownership of %s to %s (after REASSIGN OWNED BY)", table, current_user)
        except Exception as e:
            log.warning("Could not reassign ownership of %s: %s", table, e)


def _ensure_missing_columns(engine) -> None:
    """Add any columns defined in the models but absent from the DB (no Alembic).

    Only handles simple ADD COLUMN — does not change types or drop columns.
    """
    import warnings
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    dialect = engine.dialect.name
    existing_tables = set(insp.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Did not recognize type")
            existing_cols = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing_cols:
                continue
            if col.name == "embedding_vec":
                continue
            col_type = col.type.compile(dialect=engine.dialect)
            nullable = "NULL" if col.nullable else "NOT NULL"
            default_clause = ""
            if col.default is not None and col.default.is_scalar:
                val = col.default.arg
                if isinstance(val, str):
                    default_clause = f" DEFAULT '{val}'"
                elif isinstance(val, bool):
                    if dialect == "postgresql":
                        default_clause = f" DEFAULT {'true' if val else 'false'}"
                    else:
                        default_clause = f" DEFAULT {1 if val else 0}"
                elif isinstance(val, (int, float)):
                    default_clause = f" DEFAULT {val}"
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type} {nullable}{default_clause}'
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                log.info("Added column %s.%s (%s)", table.name, col.name, col_type)
            except Exception as e:
                log.warning("Could not add column %s.%s: %s", table.name, col.name, e)


def _ensure_pgvector(engine) -> bool:
    """Install the pgvector extension and upgrade the embedding_vec column.

    Returns True if pgvector is available (Postgres with extension), False otherwise.
    """
    if engine.dialect.name != "postgresql":
        return False

    from sqlalchemy import inspect, text

    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        log.info("pgvector extension enabled")
    except Exception as e:
        log.warning("Could not enable pgvector extension: %s", e)
        return False

    # Check if embedding_vec column exists and has the right type using
    # information_schema (avoids SQLAlchemy not recognizing the vector type).
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name = 'project_samples' AND column_name = 'embedding_vec'"
        )).fetchone()

    if row is None:
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "project_samples" ADD COLUMN "embedding_vec" vector({EMBEDDING_DIM})'
                ))
            log.info("Added project_samples.embedding_vec vector(%d) column", EMBEDDING_DIM)
        except Exception as e:
            log.warning("Could not add embedding_vec column: %s", e)
            return False
    elif row[0] != "vector":
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    'ALTER TABLE "project_samples" DROP COLUMN "embedding_vec"'
                ))
                conn.execute(text(
                    f'ALTER TABLE "project_samples" ADD COLUMN "embedding_vec" vector({EMBEDDING_DIM})'
                ))
            log.info("Replaced embedding_vec column with vector(%d) type", EMBEDDING_DIM)
        except Exception as e:
            log.warning("Could not replace embedding_vec column: %s", e)
            return False
    else:
        log.info("project_samples.embedding_vec already has vector type")

    return True


def _backfill_embedding_vec(engine) -> int:
    """Copy existing JSON embeddings into the native vector column.

    Returns the number of rows backfilled.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, embedding FROM project_samples "
            "WHERE embedding IS NOT NULL AND embedding_vec IS NULL"
        )).fetchall()

        if not rows:
            return 0

        count = 0
        for row_id, emb_json in rows:
            if isinstance(emb_json, str):
                import json
                emb_json = json.loads(emb_json)
            if not isinstance(emb_json, list) or len(emb_json) != EMBEDDING_DIM:
                continue
            vec_literal = "[" + ",".join(str(float(v)) for v in emb_json) + "]"
            conn.execute(
                text("UPDATE project_samples SET embedding_vec = :vec WHERE id = :id"),
                {"vec": vec_literal, "id": row_id},
            )
            count += 1
            if count % 500 == 0:
                conn.commit()
        conn.commit()

    log.info("Backfilled %d embedding_vec rows from JSON embeddings", count)
    return count


def _ensure_embedding_hnsw_index(engine) -> None:
    """Create an HNSW index on embedding_vec for fast cosine similarity search."""
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_project_samples_embedding_hnsw "
                "ON project_samples USING hnsw (embedding_vec vector_cosine_ops)"
            ))
        log.info("HNSW cosine index on project_samples.embedding_vec ready")
    except Exception as e:
        log.warning("Could not create HNSW index: %s", e)


def _ensure_umap_columns(engine) -> None:
    """Add umap_x/umap_y cache columns to project_samples if possible.

    These columns are not in the ORM model to avoid breaking queries when
    they can't be created (e.g. insufficient table ownership on Lakebase).
    """
    from sqlalchemy import text

    for col in ("umap_x", "umap_y"):
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "project_samples" ADD COLUMN "{col}" FLOAT NULL'
                ))
            log.info("Added project_samples.%s column", col)
        except Exception:
            pass  # Column already exists or insufficient privileges


def init_db(engine):
    """Create all tables and set REPLICA IDENTITY FULL for Lakehouse Sync."""
    _reassign_table_ownership(engine, TABLE_NAMES)

    pgvector_available = _ensure_pgvector(engine)

    Base.metadata.create_all(engine)
    log.info("Database tables created")
    _ensure_missing_columns(engine)
    ensure_annotations_is_draft_column(engine)
    ensure_preannotate_runs_table(engine)
    _ensure_umap_columns(engine)

    if pgvector_available:
        _ensure_pgvector(engine)
        _backfill_embedding_vec(engine)
        _ensure_embedding_hnsw_index(engine)

    try:
        from .lakebase import setup_replica_identity
        setup_replica_identity(engine, TABLE_NAMES)
    except Exception as e:
        log.warning("Replica identity setup skipped: %s", e)
