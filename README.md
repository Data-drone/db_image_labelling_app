# CV Dataset Explorer

A Streamlit app for browsing Unity Catalog Volumes, exploring computer-vision datasets with FiftyOne, and labeling images. Deploys as a **Databricks App** with zero code required from the end user.

## Quick Start (Databricks App)

1. **Import this repo** into your Databricks workspace (Repos > Add Repo)
2. **Create a Databricks App** pointing to the repo folder
3. The app auto-starts with `streamlit run app.py` (configured in `app.yaml`)

That's it. No environment setup, no config editing.

## Pages

| Page | What it does |
|------|-------------|
| **Home** | Welcome screen, quick-start links, session status |
| **Browse Volumes** | Navigate Catalog > Schema > Volume, preview images, create datasets |
| **Dataset Explorer** | Gallery with bounding-box overlays, class/tag/confidence filters, COCO export |
| **Labeling** | Classify images, draw bounding boxes, add tags, track progress |
| **Search** | Text or image similarity search (CLIP embeddings via FiftyOne Brain) |
| **Dashboard** | Stats, class distributions, labeling progress charts |

## Project Structure

```
cv-explorer-app/
  app.py                 # Home page + session init
  app.yaml               # Databricks App config
  requirements.txt       # Python deps (streamlit/plotly/sdk pre-installed)
  .streamlit/config.toml # Dark theme
  pages/
    1_Browse_Volumes.py
    2_Dataset_Explorer.py
    3_Labeling.py
    4_Search.py
    5_Dashboard.py
  utils/
    config.py            # Constants, path helpers
    volumes.py           # UC Volume listing (Databricks SDK)
    datasets.py          # FiftyOne CRUD + filtering + export
    labeling.py          # Annotation state + navigation
    drawing.py           # Image loading, bbox drawing, thumbnails
```

## Requirements

**Pre-installed on Databricks Apps** (do not add to requirements.txt):
- streamlit 1.38.0+
- databricks-sdk 0.33.0+
- plotly 5.24.1+
- mlflow-skinny

**Installed via requirements.txt:**
- fiftyone >= 1.0.0
- streamlit-drawable-canvas >= 0.9.3
- opencv-python-headless >= 4.8.0
- Pillow >= 10.0.0
- pandas >= 2.0.0
- numpy >= 1.24.0

## Local Development

```bash
cd cv-explorer-app
pip install -r requirements.txt
streamlit run app.py
```

When running locally (no `DATABRICKS_HOST` env var), the app falls back to local folder browsing instead of UC Volumes.

## Compute Sizing

- **Medium** (2 vCPU, 6 GB): Fine for browsing and labeling
- **Large** (4 vCPU, 12 GB): Recommended if computing CLIP embeddings on large datasets
