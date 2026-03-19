"""
FastAPI backend for CV Explorer.

App assembly — wires up routers, middleware, startup, and static files.
"""

import collections
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .models import Base
from .routes import projects, labeling, admin, export, browse

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory log ring buffer (last 500 lines) for /api/debug/logs
# ---------------------------------------------------------------------------
_log_ring = collections.deque(maxlen=500)


class _RingHandler(logging.Handler):
    def emit(self, record):
        _log_ring.append(self.format(record))


_ring_handler = _RingHandler()
_ring_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.root.addHandler(_ring_handler)
logging.root.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated @app.on_event("startup"))
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    from .deps import configure_db

    print("[STARTUP] Beginning app startup...", flush=True)

    use_lakebase = os.environ.get("USE_LAKEBASE", "true").lower() != "false"
    engine = None
    lakebase_active = False

    if use_lakebase:
        try:
            print("[STARTUP] Attempting Lakebase init...", flush=True)
            from .lakebase import init_lakebase
            engine = init_lakebase()
            lakebase_active = True
            print("[STARTUP] Lakebase init succeeded", flush=True)
            log.info("Connected to Lakebase")
        except Exception as e:
            print(f"[STARTUP] Lakebase init failed: {e}", flush=True)
            log.warning("Lakebase init failed (%s), falling back to SQLite", e)
            use_lakebase = False

    if not use_lakebase:
        print("[STARTUP] Using SQLite backend", flush=True)
        from sqlalchemy import create_engine
        db_url = os.environ.get("DATABASE_URL", "sqlite:////tmp/cv_explorer.db")
        engine = create_engine(db_url, echo=False)

    from sqlalchemy.orm import sessionmaker
    session_factory = sessionmaker(bind=engine)
    configure_db(engine, session_factory, lakebase_active)

    print("[STARTUP] Creating tables...", flush=True)
    try:
        Base.metadata.create_all(engine)
        print("[STARTUP] Tables created OK", flush=True)
    except Exception as e:
        print(f"[STARTUP] create_all failed: {e}", flush=True)
        raise
    log.info("Database tables ready")
    print("[STARTUP] Startup complete!", flush=True)

    yield

    if engine:
        engine.dispose()
        log.info("Database engine disposed")


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="CV Explorer API",
    version="1.0.0",
    lifespan=lifespan,
)

_cors_origins = os.environ.get("CORS_ORIGINS", "").split(",")
_cors_origins = [o.strip() for o in _cors_origins if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(projects.router)
app.include_router(labeling.router)
app.include_router(admin.router)
app.include_router(export.router)
app.include_router(browse.router)


# ---------------------------------------------------------------------------
# Health & debug
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/debug/logs")
def debug_logs(n: int = 200):
    """Return last N log lines from the in-memory ring buffer."""
    lines = list(_log_ring)[-n:]
    return {"lines": lines, "count": len(lines), "total_buffered": len(_log_ring)}


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
        return HTMLResponse(
            content=(STATIC_DIR / "index.html").read_text(),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
else:
    @app.get("/")
    def root():
        return {"message": "CV Explorer API. Frontend not built — run 'npm run build' in frontend/."}
