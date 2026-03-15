"""
FastAPI backend for CV Dataset Explorer.

Provides REST API endpoints for datasets, samples, annotations, and tags.
Reuses the same SQLite/PostgreSQL database as the Streamlit app.

Run with:
    uvicorn backend.main:app --reload --port 8000
"""

import json
import io
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from .models import (
    get_session, init_db, schedule_backup,
    Dataset, Sample, Annotation, Tag,
)
from .schemas import (
    DatasetCreate, DatasetOut, DatasetStats,
    SampleOut, SamplePage,
    AnnotationCreate, AnnotationOut,
    TagCreate, TagOut,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="CV Dataset Explorer API",
    description="REST API for browsing, annotating, and managing CV datasets.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}


def _is_volume_path(path: str) -> bool:
    return path.startswith("/Volumes/")


def _get_workspace_client():
    """Get a cached WorkspaceClient."""
    if not hasattr(_get_workspace_client, "_client"):
        from databricks.sdk import WorkspaceClient
        _get_workspace_client._client = WorkspaceClient()
    return _get_workspace_client._client


def _download_volume_file(path: str) -> Optional[bytes]:
    """Download a single file from a /Volumes/ path via the SDK."""
    try:
        w = _get_workspace_client()
        resp = w.files.download(path)
        return resp.contents.read()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def get_db():
    """Yield a database session, closing it after the request."""
    db = get_session()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Startup — demo data seeding
# ---------------------------------------------------------------------------
DEMO_VOLUME_PATH = os.environ.get(
    "DEMO_VOLUME_PATH",
    "/Volumes/brian_gen_ai/cv_explorer/demo_images",
)
DEMO_LOCAL_DIR = "/tmp/demo_images"


def _download_volume_to_local(volume_path: str, local_dir: str) -> bool:
    """Download all files from a UC volume to a local directory using the SDK."""
    if os.path.isdir(local_dir) and os.listdir(local_dir):
        return True
    try:
        w = _get_workspace_client()
        os.makedirs(local_dir, exist_ok=True)
        files = list(w.files.list_directory_contents(volume_path + "/"))
        for f in files:
            if f.is_directory:
                continue
            resp = w.files.download(f.path)
            local_path = os.path.join(local_dir, f.name)
            with open(local_path, "wb") as out:
                out.write(resp.contents.read())
        return bool(os.listdir(local_dir))
    except Exception as exc:
        print(f"Volume download failed: {exc}")
        return False


def _seed_demo_data():
    """Seed demo dataset using /Volumes/ paths (images served via SDK per request)."""
    image_dir = DEMO_VOLUME_PATH  # /Volumes/brian_gen_ai/cv_explorer/demo_images

    db = get_session()
    try:
        existing = db.query(Dataset).filter_by(name="COCO Demo").first()
        if existing:
            sample_count = db.query(Sample).filter_by(dataset_id=existing.id).count()
            if sample_count > 0:
                return
            # Stale dataset with 0 samples — delete and re-create
            db.delete(existing)
            db.flush()

        # List images in volume via SDK
        try:
            w = _get_workspace_client()
            entries = list(w.files.list_directory_contents(image_dir.rstrip("/") + "/"))
        except Exception as exc:
            print(f"Cannot list demo volume: {exc}")
            return

        image_files = sorted(
            e.name for e in entries
            if not e.is_directory and os.path.splitext(e.name)[1].lower() in IMAGE_EXTENSIONS
        )
        if not image_files:
            print("No images found in demo volume")
            return

        ds = Dataset(name="COCO Demo", description="Demo dataset from COCO images", image_dir=image_dir)
        db.add(ds)
        db.flush()

        for fname in image_files:
            fpath = image_dir.rstrip("/") + "/" + fname
            db.add(Sample(dataset_id=ds.id, filepath=fpath, filename=fname))
        db.flush()

        # Import COCO annotations if labels.json exists in the volume
        coco_data = _download_volume_file(image_dir.rstrip("/") + "/labels.json")
        if coco_data:
            try:
                coco = json.loads(coco_data)
                images_map = {img["id"]: img for img in coco.get("images", [])}
                categories = {cat["id"]: cat["name"] for cat in coco.get("categories", [])}
                samples = db.query(Sample).filter_by(dataset_id=ds.id).all()
                fname_to_sample = {s.filename: s for s in samples}
                for ann in coco.get("annotations", []):
                    img_info = images_map.get(ann.get("image_id"))
                    if not img_info:
                        continue
                    fname = img_info.get("file_name", "")
                    sample = fname_to_sample.get(fname)
                    if not sample:
                        continue
                    cat_name = categories.get(ann.get("category_id"), "unknown")
                    bbox = ann.get("bbox", [])
                    if bbox and len(bbox) == 4:
                        img_w = img_info.get("width", 1)
                        img_h = img_info.get("height", 1)
                        bbox_norm = json.dumps({
                            "x": bbox[0] / img_w, "y": bbox[1] / img_h,
                            "w": bbox[2] / img_w, "h": bbox[3] / img_h,
                        })
                        db.add(Annotation(
                            sample_id=sample.id, ann_type="detection",
                            label=cat_name, bbox_json=bbox_norm,
                        ))
                    else:
                        db.add(Annotation(
                            sample_id=sample.id, ann_type="classification",
                            label=cat_name,
                        ))
            except Exception as exc:
                print(f"COCO annotation import failed: {exc}")

        db.commit()
        print(f"Seeded demo dataset with {len(image_files)} images (volume paths)")
    except Exception as e:
        print(f"Demo seed failed: {e}")
        db.rollback()
    finally:
        db.close()


@app.on_event("startup")
def startup():
    init_db()
    _seed_demo_data()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------
@app.get("/api/datasets", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    """List all datasets with sample counts."""
    datasets = db.query(Dataset).order_by(Dataset.name).all()
    result = []
    for ds in datasets:
        count = db.query(Sample).filter_by(dataset_id=ds.id).count()
        out = DatasetOut(
            id=ds.id,
            name=ds.name,
            description=ds.description or "",
            image_dir=ds.image_dir or "",
            created_at=ds.created_at,
            sample_count=count,
        )
        result.append(out)
    return result


@app.post("/api/datasets", response_model=DatasetOut)
def create_dataset(payload: DatasetCreate, db: Session = Depends(get_db)):
    """Create a new dataset. If image_dir is provided, scan for images."""
    existing = db.query(Dataset).filter_by(name=payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Dataset '{payload.name}' already exists.")

    ds = Dataset(
        name=payload.name,
        description=payload.description,
        image_dir=payload.image_dir,
    )
    db.add(ds)
    db.flush()

    # Scan image_dir if provided
    sample_count = 0
    if payload.image_dir:
        if _is_volume_path(payload.image_dir):
            try:
                w = _get_workspace_client()
                for entry in w.files.list_directory_contents(payload.image_dir.rstrip("/") + "/"):
                    if not entry.is_directory:
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext in IMAGE_EXTENSIONS:
                            fpath = payload.image_dir.rstrip("/") + "/" + entry.name
                            db.add(Sample(dataset_id=ds.id, filepath=fpath, filename=entry.name))
                            sample_count += 1
            except Exception as e:
                print(f"Volume scan failed: {e}")
        elif os.path.isdir(payload.image_dir):
            for fname in sorted(os.listdir(payload.image_dir)):
                ext = os.path.splitext(fname)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    db.add(Sample(
                        dataset_id=ds.id,
                        filepath=os.path.join(payload.image_dir, fname),
                        filename=fname,
                    ))
                    sample_count += 1

    db.commit()
    db.refresh(ds)
    schedule_backup()

    return DatasetOut(
        id=ds.id,
        name=ds.name,
        description=ds.description or "",
        image_dir=ds.image_dir or "",
        created_at=ds.created_at,
        sample_count=sample_count,
    )


@app.get("/api/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: int, db: Session = Depends(get_db)):
    """Get a single dataset by ID."""
    ds = db.query(Dataset).filter_by(id=dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    count = db.query(Sample).filter_by(dataset_id=ds.id).count()
    return DatasetOut(
        id=ds.id,
        name=ds.name,
        description=ds.description or "",
        image_dir=ds.image_dir or "",
        created_at=ds.created_at,
        sample_count=count,
    )


@app.delete("/api/datasets/{dataset_id}")
def delete_dataset(dataset_id: int, db: Session = Depends(get_db)):
    """Delete a dataset and all its samples, annotations, and tags."""
    ds = db.query(Dataset).filter_by(id=dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    db.delete(ds)
    db.commit()
    return {"detail": "Deleted."}


@app.get("/api/datasets/{dataset_id}/stats", response_model=DatasetStats)
def dataset_stats(dataset_id: int, db: Session = Depends(get_db)):
    """Get comprehensive statistics for a dataset."""
    ds = db.query(Dataset).filter_by(id=dataset_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="Dataset not found.")

    total = db.query(Sample).filter_by(dataset_id=ds.id).count()
    labeled = (
        db.query(Sample)
        .filter(Sample.dataset_id == ds.id)
        .filter(Sample.tags.any(Tag.tag == "labeled"))
        .count()
    )

    class_rows = (
        db.query(Annotation.label)
        .join(Sample)
        .filter(Sample.dataset_id == ds.id)
        .distinct()
        .all()
    )
    classes = sorted([r[0] for r in class_rows])

    class_dist = {}
    for cls in classes:
        count = (
            db.query(Annotation)
            .join(Sample)
            .filter(Sample.dataset_id == ds.id)
            .filter(Annotation.label == cls)
            .count()
        )
        class_dist[cls] = count

    tag_rows = (
        db.query(Tag.tag)
        .join(Sample)
        .filter(Sample.dataset_id == ds.id)
        .distinct()
        .all()
    )
    tags = sorted([r[0] for r in tag_rows])

    tag_dist = {}
    for t in tags:
        count = (
            db.query(Tag)
            .join(Sample)
            .filter(Sample.dataset_id == ds.id)
            .filter(Tag.tag == t)
            .count()
        )
        tag_dist[t] = count

    return DatasetStats(
        total_samples=total,
        labeled_count=labeled,
        unlabeled_count=total - labeled,
        class_count=len(classes),
        classes=classes,
        tags=tags,
        class_distribution=class_dist,
        tag_distribution=tag_dist,
    )


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------
@app.get("/api/datasets/{dataset_id}/samples", response_model=SamplePage)
def list_samples(
    dataset_id: int,
    page: int = Query(0, ge=0),
    page_size: int = Query(24, ge=1, le=10000),
    label: Optional[str] = None,
    tag: Optional[str] = None,
    search: Optional[str] = None,
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
):
    """List samples with pagination and optional filters."""
    query = db.query(Sample).filter(Sample.dataset_id == dataset_id)

    if search:
        query = query.filter(Sample.filename.ilike(f"%{search}%"))
    if label:
        query = query.filter(Sample.annotations.any(Annotation.label == label))
    if tag:
        query = query.filter(Sample.tags.any(Tag.tag == tag))
    if min_confidence > 0:
        query = query.filter(
            Sample.annotations.any(Annotation.confidence >= min_confidence)
        )

    total = query.count()
    items = query.order_by(Sample.id).offset(page * page_size).limit(page_size).all()

    return SamplePage(
        items=[SampleOut.model_validate(s) for s in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/api/samples/{sample_id}", response_model=SampleOut)
def get_sample(sample_id: int, db: Session = Depends(get_db)):
    """Get a single sample by ID with its annotations and tags."""
    sample = db.query(Sample).filter_by(id=sample_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")
    return SampleOut.model_validate(sample)


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------
@app.get("/api/samples/{sample_id}/annotations", response_model=list[AnnotationOut])
def list_annotations(sample_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Annotation)
        .filter_by(sample_id=sample_id)
        .order_by(Annotation.id)
        .all()
    )


@app.post("/api/annotations", response_model=AnnotationOut)
def create_annotation(payload: AnnotationCreate, db: Session = Depends(get_db)):
    sample = db.query(Sample).filter_by(id=payload.sample_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    ann = Annotation(
        sample_id=payload.sample_id,
        ann_type=payload.ann_type,
        label=payload.label,
        bbox_json=payload.bbox_json,
        polygon_json=payload.polygon_json,
        confidence=payload.confidence,
    )
    db.add(ann)

    existing_tag = db.query(Tag).filter_by(sample_id=sample.id, tag="labeled").first()
    if not existing_tag:
        db.add(Tag(sample_id=sample.id, tag="labeled"))

    db.commit()
    db.refresh(ann)
    schedule_backup()
    return AnnotationOut.model_validate(ann)


@app.post("/api/annotations/batch", response_model=list[AnnotationOut])
def create_annotations_batch(
    annotations: list[AnnotationCreate],
    db: Session = Depends(get_db),
):
    results = []
    sample_ids = set()
    for payload in annotations:
        ann = Annotation(
            sample_id=payload.sample_id,
            ann_type=payload.ann_type,
            label=payload.label,
            bbox_json=payload.bbox_json,
            polygon_json=payload.polygon_json,
            confidence=payload.confidence,
        )
        db.add(ann)
        sample_ids.add(payload.sample_id)
        results.append(ann)

    for sid in sample_ids:
        existing_tag = db.query(Tag).filter_by(sample_id=sid, tag="labeled").first()
        if not existing_tag:
            db.add(Tag(sample_id=sid, tag="labeled"))

    db.commit()
    for ann in results:
        db.refresh(ann)
    schedule_backup()
    return [AnnotationOut.model_validate(a) for a in results]


@app.delete("/api/annotations/{annotation_id}")
def delete_annotation(annotation_id: int, db: Session = Depends(get_db)):
    ann = db.query(Annotation).filter_by(id=annotation_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="Annotation not found.")
    db.delete(ann)
    db.commit()
    return {"detail": "Deleted."}


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------
@app.get("/api/samples/{sample_id}/tags", response_model=list[TagOut])
def list_tags(sample_id: int, db: Session = Depends(get_db)):
    return db.query(Tag).filter_by(sample_id=sample_id).order_by(Tag.id).all()


@app.post("/api/tags", response_model=TagOut)
def create_tag(payload: TagCreate, db: Session = Depends(get_db)):
    sample = db.query(Sample).filter_by(id=payload.sample_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    existing = db.query(Tag).filter_by(sample_id=payload.sample_id, tag=payload.tag).first()
    if existing:
        return TagOut.model_validate(existing)

    tag = Tag(sample_id=payload.sample_id, tag=payload.tag)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    schedule_backup()
    return TagOut.model_validate(tag)


@app.delete("/api/tags/{tag_id}")
def delete_tag(tag_id: int, db: Session = Depends(get_db)):
    tag = db.query(Tag).filter_by(id=tag_id).first()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found.")
    db.delete(tag)
    db.commit()
    return {"detail": "Deleted."}


# ---------------------------------------------------------------------------
# Image serving — handles both local files and /Volumes/ paths via SDK
# ---------------------------------------------------------------------------
@app.get("/images/{sample_id}")
def serve_image(sample_id: int, db: Session = Depends(get_db)):
    """Serve an image file by sample ID."""
    sample = db.query(Sample).filter_by(id=sample_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    if _is_volume_path(sample.filepath):
        data = _download_volume_file(sample.filepath)
        if data is None:
            raise HTTPException(status_code=404, detail="Image file not found in volume.")
        return StreamingResponse(io.BytesIO(data), media_type="image/jpeg")
    else:
        if not os.path.exists(sample.filepath):
            raise HTTPException(status_code=404, detail="Image file not found on disk.")
        return FileResponse(sample.filepath)


@app.get("/images/{sample_id}/thumbnail")
def serve_thumbnail(
    sample_id: int,
    size: int = Query(300, ge=50, le=1000),
    db: Session = Depends(get_db),
):
    """Serve a resized thumbnail of an image."""
    sample = db.query(Sample).filter_by(id=sample_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    try:
        from PIL import Image

        if _is_volume_path(sample.filepath):
            data = _download_volume_file(sample.filepath)
            if data is None:
                raise HTTPException(status_code=404, detail="Image file not found in volume.")
            img = Image.open(io.BytesIO(data)).convert("RGB")
        else:
            if not os.path.exists(sample.filepath):
                raise HTTPException(status_code=404, detail="Image file not found on disk.")
            img = Image.open(sample.filepath).convert("RGB")

        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception:
        if _is_volume_path(sample.filepath):
            raise HTTPException(status_code=500, detail="Could not process image.")
        return FileResponse(sample.filepath)


# ---------------------------------------------------------------------------
# Directory browsing — SDK for /Volumes/, os for local
# ---------------------------------------------------------------------------
@app.get("/api/catalogs")
def list_catalogs():
    """List UC catalogs visible to the app."""
    try:
        w = _get_workspace_client()
        names = []
        for c in w.catalogs.list():
            names.append(c.name)
            if len(names) >= 200:
                break
        return sorted(names)
    except Exception as e:
        print(f"catalogs.list() failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schemas")
def list_schemas(catalog: str = Query(...)):
    """List schemas inside a catalog."""
    try:
        w = _get_workspace_client()
        return sorted(s.name for s in w.schemas.list(catalog_name=catalog))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/volumes")
def list_volumes(catalog: str = Query(...), schema: str = Query(...)):
    """List volumes inside a catalog.schema."""
    try:
        w = _get_workspace_client()
        return sorted(v.name for v in w.volumes.list(catalog_name=catalog, schema_name=schema))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/browse")
def browse_directory(path: str = Query(...)):
    """Browse a directory, returning folders and image files."""
    if _is_volume_path(path):
        try:
            w = _get_workspace_client()
            folders = []
            files = []
            for entry in w.files.list_directory_contents(path.rstrip("/") + "/"):
                if entry.is_directory:
                    folders.append({"name": entry.name, "image_count": 0})
                elif os.path.splitext(entry.name)[1].lower() in IMAGE_EXTENSIONS:
                    files.append({"name": entry.name, "path": path.rstrip("/") + "/" + entry.name})
            return {"path": path, "folders": sorted(folders, key=lambda x: x["name"]), "files": sorted(files, key=lambda x: x["name"])}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        if not os.path.isdir(path):
            raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

        folders = []
        files = []
        try:
            for entry in sorted(os.listdir(path)):
                full = os.path.join(path, entry)
                if os.path.isdir(full):
                    img_count = 0
                    for root, _, filenames in os.walk(full):
                        img_count += sum(
                            1 for f in filenames
                            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
                        )
                    folders.append({"name": entry, "image_count": img_count})
                elif os.path.splitext(entry)[1].lower() in IMAGE_EXTENSIONS:
                    files.append({"name": entry, "path": full})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return {"path": path, "folders": folders, "files": files}


# ---------------------------------------------------------------------------
# Static file serving (React build)
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"

if STATIC_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import HTMLResponse

    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="static-assets")

    @app.get("/vite.svg")
    def vite_svg():
        return FileResponse(str(STATIC_DIR / "vite.svg"))

    @app.get("/{path:path}")
    def serve_spa(path: str):
        if path.startswith("api/") or path.startswith("images/"):
            raise HTTPException(status_code=404)
        file_path = STATIC_DIR / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return HTMLResponse(content=(STATIC_DIR / "index.html").read_text())
else:
    @app.get("/")
    def root():
        return {"message": "CV Dataset Explorer API. Frontend not built yet — run 'npm run build' in frontend/."}
