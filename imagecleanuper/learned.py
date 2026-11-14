"""Back up what the app has learned (pseudocode section 22)."""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from .fileops import atomic_write
from .paths import long_path

FOLDER = "Learned Data"


def backup_learned_data(catalog_path: Path, data: Path, backup_dir: str, run_id: int, keep: int = 10) -> str:
    dest_dir = os.path.join(backup_dir, FOLDER)
    os.makedirs(long_path(dest_dir), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "catalog.db"
        # SQLite's backup API gives a consistent copy even while the catalog is open.
        src = sqlite3.connect(str(catalog_path))
        dst = sqlite3.connect(str(snap))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        def write(f):
            with zipfile.ZipFile(f, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                zf.write(snap, "catalog.db")
                models = data / "models"
                if models.is_dir():
                    for p in sorted(models.rglob("*")):
                        if p.is_file():
                            zf.write(p, f"models/{p.relative_to(models).as_posix()}")

        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dest = os.path.join(dest_dir, f"learned_{stamp}_run{run_id:04d}.zip")
        atomic_write(dest, write)
    prune(dest_dir, keep)
    return dest


def prune(dest_dir: str, keep: int) -> None:
    files = sorted(f for f in os.listdir(long_path(dest_dir)) if f.startswith("learned_") and f.endswith(".zip"))
    for old in files[:-keep] if keep > 0 else files:
        os.remove(long_path(os.path.join(dest_dir, old)))


def newest_learned_backup(backup_dir: str) -> str | None:
    d = os.path.join(backup_dir, FOLDER)
    if not os.path.isdir(long_path(d)):
        return None
    files = sorted(f for f in os.listdir(long_path(d)) if f.startswith("learned_") and f.endswith(".zip"))
    return os.path.join(d, files[-1]) if files else None


def restore_learned_data(zip_path: str, catalog_path: Path, data: Path) -> None:
    """Replace the catalog (and models) with a learned-data backup. The app must be idle."""
    with zipfile.ZipFile(long_path(zip_path)) as zf:
        def write_catalog(f):
            f.write(zf.read("catalog.db"))
        for suffix in ("-wal", "-shm"):
            p = Path(str(catalog_path) + suffix)
            if p.exists():
                p.unlink()
        atomic_write(catalog_path, write_catalog, overwrite=True)
        for name in zf.namelist():
            if name.startswith("models/") and not name.endswith("/"):
                target = data / name
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(target, lambda f, n=name: f.write(zf.read(n)), overwrite=True)
