import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from imagecleanuper.app import AppPaths  # noqa: E402
from imagecleanuper.settings import Settings  # noqa: E402


def write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def env(tmp_path):
    """A fake PC: two photo folders, a library and a backup folder, and an app folder."""
    root = tmp_path / "app"
    root.mkdir()
    paths = AppPaths.default(root)
    m1 = tmp_path / "Pictures"
    m2 = tmp_path / "Old Phone Backup"
    m1.mkdir()
    m2.mkdir()
    s = Settings(library=str(tmp_path / "SortedPhotos"), backup=str(tmp_path / "MasterPhotoBackup"),
                 masters=[str(m1), str(m2)])
    return paths, s, m1, m2


def snapshot(folder: Path) -> dict:
    """Fingerprint every file under a folder (to prove sources are never modified)."""
    from imagecleanuper.fileops import sha256_file
    out = {}
    for dirpath, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(dirpath, f)
            out[os.path.relpath(p, folder)] = (sha256_file(p), os.stat(p).st_mtime_ns)
    return out
