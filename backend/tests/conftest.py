"""
Shared test helpers for backend tests.

Uses the SQLite fallback configured by the app at startup when
USE_LAKEBASE=false and DATABASE_URL points to a local file. No
network, no Lakebase, no UC Volume calls.

Tests here use stdlib ``unittest`` for portability (pytest is not
always available in the container). The helpers below can be reused
by any TestCase that wants a fresh SQLite-backed app.
"""
from __future__ import annotations

import importlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def sqlite_app(tmp_dir: Path):
    """Yield a freshly-reloaded ``backend.main`` module bound to a temp SQLite DB.

    Sets USE_LAKEBASE=false and DATABASE_URL so the app uses its SQLite
    fallback. Env vars are restored on exit.
    """
    db_path = tmp_dir / "test.db"
    prev_use_lakebase = os.environ.get("USE_LAKEBASE")
    prev_db_url = os.environ.get("DATABASE_URL")
    os.environ["USE_LAKEBASE"] = "false"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    try:
        import backend.main as main_mod  # noqa: WPS433
        importlib.reload(main_mod)
        yield main_mod
    finally:
        if prev_use_lakebase is None:
            os.environ.pop("USE_LAKEBASE", None)
        else:
            os.environ["USE_LAKEBASE"] = prev_use_lakebase
        if prev_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_db_url


@contextmanager
def test_client(tmp_dir: Path):
    """Yield (TestClient, main_module, tmp_dir) for HTTP-level tests."""
    from fastapi.testclient import TestClient
    with sqlite_app(tmp_dir) as main_mod:
        with TestClient(main_mod.app) as c:
            yield c, main_mod, tmp_dir


def make_sample_volume(tmp_dir: Path) -> Path:
    """A fake local 'source volume' directory with tiny image stubs."""
    vol = tmp_dir / "vol"
    vol.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (vol / name).write_bytes(b"\xff\xd8\xff\xd9")
    return vol
