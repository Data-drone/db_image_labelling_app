# Databricks Image Labelling App

A Streamlit-based image annotation tool designed to deploy as a **Databricks App**. Supports classification, bounding box, polygon/segmentation annotation, and tagging — with autosave, COCO export, and a Databricks-themed dark UI.

Built on SQLAlchemy (SQLite for local dev, PostgreSQL for production) — no heavyweight dependencies like FiftyOne or MongoDB required.

## Features

- **4 labeling modes**: Classification, Bounding Box, Polygon (segmentation), Tagging
- **Autosave**: annotations are persisted immediately as you draw (toggle on/off)
- **Interactive canvas**: draw bounding boxes and polygons directly on images using `streamlit-drawable-canvas`
- **Undo / Clear**: remove the last annotation or reset the canvas
- **Dataset management**: create datasets from Unity Catalog Volumes or local folders
- **Gallery view**: browse images with annotation overlays
- **Search**: filter samples by filename, label, or tag
- **Dashboard**: class distribution charts, labeling progress, dataset statistics
- **COCO JSON export**: export annotations in standard COCO format
- **Databricks integration**: browse UC Volumes, deploy as a Databricks App

## Pages

| Page | Description |
|------|-------------|
| **Home** | Welcome screen with quick-start links and session status |
| **Browse Volumes** | Navigate Catalog > Schema > Volume, preview images, create datasets |
| **Dataset Explorer** | Image gallery with bounding-box and polygon overlays, filtering, COCO export |
| **Labeling** | Annotate images — classify, draw boxes/polygons, tag, with autosave |
| **Search** | Filter samples by filename, label, or tag |
| **Dashboard** | Class distributions, labeling progress, dataset stats |

## Quick Start

### Local Development

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501. The app uses SQLite by default — a database file is created automatically at `/tmp/cv_explorer.db`.

To load images locally, use the **Dataset Explorer** page to create a dataset from a folder of images on disk.

### Deploy to Databricks

1. Push this repo to a Databricks Git folder or import it into Repos
2. Create a Databricks App pointing to the repo folder
3. Set the `DATABASE_URL` environment variable to a PostgreSQL connection string for persistent storage:
   ```
   postgresql://user:password@host:5432/dbname
   ```
4. The app auto-starts via `app.yaml` with `streamlit run app.py`

When `DATABRICKS_HOST` is detected, the app enables UC Volume browsing via the Databricks SDK.

## Project Structure

```
db_image_labelling_app/
├── app.py                          # Home page + session init
├── app.yaml                        # Databricks App config
├── requirements.txt                # Python dependencies
├── .streamlit/
│   └── config.toml                 # Databricks dark theme
├── pages/
│   ├── 1_📁_Browse_Volumes.py     # UC Volume browser + dataset creation
│   ├── 2_🖼️_Dataset_Explorer.py   # Image gallery with overlays
│   ├── 3_🏷️_Labeling.py          # Annotation interface (4 modes + autosave)
│   ├── 4_🔍_Search.py             # Filename/label/tag search
│   └── 5_📊_Dashboard.py          # Stats and charts
└── utils/
    ├── config.py                    # Constants (classes, tags, extensions)
    ├── database.py                  # SQLAlchemy models (Dataset, Sample, Annotation, Tag)
    ├── datasets.py                  # Dataset CRUD, filtering, COCO import/export
    ├── drawing.py                   # Image loading, bbox/polygon overlay rendering
    ├── labeling.py                  # Annotation state, navigation, save functions
    └── volumes.py                   # Databricks UC Volume listing
```

## Database Schema

The app uses SQLAlchemy with four tables:

- **datasets** — name, description, image directory path
- **samples** — filepath, filename, linked to a dataset
- **annotations** — classification labels, bounding boxes (normalised JSON), polygon coordinates (normalised JSON), confidence scores
- **tags** — per-sample tags (e.g. "labeled", "flagged", "skip", "good", "bad")

Bounding boxes are stored as normalised `[0, 1]` coordinates: `{"x": float, "y": float, "w": float, "h": float}`.

Polygons are stored as lists of normalised `[x, y]` coordinate pairs: `[[0.1, 0.2], [0.3, 0.4], ...]`.

## Requirements

```
streamlit>=1.32.0,<1.45.0
streamlit-drawable-canvas==0.9.3
sqlalchemy>=2.0.0
Pillow>=10.0.0
pandas>=2.0.0
numpy>=1.24.0
plotly>=5.0.0
```

On Databricks Apps, `streamlit`, `plotly`, `pandas`, `numpy`, and `databricks-sdk` are pre-installed.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:////tmp/cv_explorer.db` | SQLAlchemy database URL. Use PostgreSQL for production. |
| `DATABRICKS_HOST` | (unset) | Auto-detected on Databricks. Enables UC Volume browsing. |

### Default Classes and Tags

Edit `utils/config.py` to customise:

- `DEFAULT_CLASSES` — annotation class labels (default: car, truck, person, bicycle, sign)
- `QUICK_TAGS` — one-click tags in tagging mode (default: good, bad, review, skip, flagged)
- `IMAGE_EXTENSIONS` — supported image file types

## Known Limitations

- `streamlit-drawable-canvas` v0.9.3 uses a deprecated Streamlit internal API (`image_to_url`). The Labeling page includes a monkey-patch for compatibility with Streamlit >= 1.39. This will be resolved when the canvas library releases an update.
- Polygon mode uses Fabric.js path objects — complex self-intersecting polygons may not extract correctly.
- The canvas background image is rendered as a base64 data URL, which can be slow for very large images (>5MB).

## Future Roadmap

- **Model-assisted labeling**: integrate with Databricks Model Serving (YOLO, SAM 2) for pre-annotation
- **Vector search**: add pgvector or Databricks Vector Search for similarity-based image retrieval
- **Keyboard shortcuts**: speed up annotation workflow
- **Multi-user support**: concurrent annotation with user tracking
