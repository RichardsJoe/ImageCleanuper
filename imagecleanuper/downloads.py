"""First-launch downloads of the AI engine and models (pseudocode section 4).

Every file has a known fingerprint. Downloads resume after a dropped connection,
and a file that fails its check is never used.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import log
from .fileops import CHUNK, sha256_file
from .log import UserError

Progress = Callable[[str, int, int], None]
_log = log.get("downloads")


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str
    size: int


# Filled in when the models are added (phase 2/3). Hosted on this repo's GitHub Releases.
MANIFEST: list[ModelFile] = []


def is_ready(item: ModelFile, dest_dir: Path) -> bool:
    p = dest_dir / item.name
    return p.exists() and p.stat().st_size == item.size and sha256_file(p) == item.sha256


def download(item: ModelFile, dest_dir: Path, progress: Progress | None = None,
             retries: int = 3, timeout: float = 60) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / item.name
    if is_ready(item, dest_dir):
        return final
    part = dest_dir / (item.name + ".partial")
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            have = part.stat().st_size if part.exists() else 0
            if have > item.size:
                part.unlink()
                have = 0
            req = urllib.request.Request(item.url, headers={"User-Agent": "ImageCleanuper"})
            if have:
                req.add_header("Range", f"bytes={have}-")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if have and resp.status != 206:  # server ignored the range: start over
                    have = 0
                mode = "ab" if have else "wb"
                with open(part, mode) as out:
                    while True:
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        out.write(block)
                        have += len(block)
                        if progress:
                            progress(f"Downloading {item.name}", have, item.size)
                    out.flush()
                    os.fsync(out.fileno())
            if have < item.size:
                raise IOError("connection closed early")
            h = hashlib.sha256()
            with open(part, "rb") as f:
                for block in iter(lambda: f.read(CHUNK), b""):
                    h.update(block)
            if h.hexdigest() != item.sha256:
                part.unlink()  # damaged or tampered: never use it
                raise IOError("downloaded file failed its check")
            os.replace(part, final)
            return final
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            last_error = e
            _log.warning("download %s attempt %d failed: %s", item.name, attempt, e)
    raise UserError(f"Couldn't download {item.name}.",
                    "Check your internet connection and start the app again; it will continue where it stopped.",
                    str(last_error))


def ensure_downloaded(dest_dir: Path, progress: Progress | None = None,
                      manifest: list[ModelFile] | None = None) -> None:
    for item in (MANIFEST if manifest is None else manifest):
        download(item, dest_dir, progress)
