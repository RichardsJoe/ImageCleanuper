"""Find photos and videos in every source folder (pseudocode section 6)."""

from __future__ import annotations

import os
import re
import stat as stat_mod
import time
from dataclasses import dataclass, field
from typing import Callable

from . import log
from .catalog import Catalog
from .fileops import sha256_file
from .paths import IS_WINDOWS, is_inside, long_path, norm, strip_long

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".wmv", ".mts", ".m2ts"}
SKIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
SKIP_DIRS = {"$recycle.bin", "system volume information", "$windows.~bt", "$windows.~ws"}

_log = log.get("discover")
Progress = Callable[[str, int, int], None]


@dataclass
class DiscoverResult:
    found: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    missing: int = 0
    busy: int = 0
    other_files: int = 0
    unavailable_sources: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)


def file_kind(name: str) -> str | None:
    ext = os.path.splitext(name)[1].lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return None


def _skip_file(name: str) -> bool:
    low = name.lower()
    return low in SKIP_NAMES or low.startswith("._") or low.endswith(".partial")


def _hidden_or_system(path: str, name: str) -> bool:
    if name.startswith(".") or name.lower() in SKIP_DIRS:
        return True
    if IS_WINDOWS:
        try:
            attrs = os.stat(path, follow_symlinks=False).st_file_attributes
            return bool(attrs & (stat_mod.FILE_ATTRIBUTE_HIDDEN | stat_mod.FILE_ATTRIBUTE_SYSTEM))
        except OSError:
            return True
    return False


def _is_link(path: str) -> bool:
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)  # Python 3.12+
    return bool(isjunction and isjunction(path))


def sync_sources(cat: Catalog, masters: list[str]) -> None:
    """Make the sources table match the master folders in settings (ids stay stable)."""
    now = time.time()
    wanted = {norm(m): m for m in masters}
    rows = cat.q("SELECT id, path_key, kind FROM sources WHERE kind='folder'")
    with cat.tx():
        for r in rows:
            cat.db.execute("UPDATE sources SET enabled=? WHERE id=?", (1 if r["path_key"] in wanted else 0, r["id"]))
        existing = {r["path_key"] for r in rows}
        for key, path in wanted.items():
            if key in existing:
                continue
            base = re.sub(r"[^A-Za-z0-9 _-]+", "_", os.path.basename(strip_long(path).rstrip("\\/")) or "drive")
            cur = cat.db.execute(
                "INSERT INTO sources(kind, path, path_key, label, enabled, created_at) VALUES ('folder',?,?,?,1,?)",
                (path, key, f"tmp-{key}", now))
            cat.db.execute("UPDATE sources SET label=? WHERE id=?", (f"{cur.lastrowid:03d}_{base}", cur.lastrowid))


def discover_files(cat: Catalog, run_id: int, excluded: list[str],
                   progress: Progress | None = None,
                   hasher: Callable[[str], str] = sha256_file) -> DiscoverResult:
    res = DiscoverResult()
    sources = cat.q("SELECT * FROM sources WHERE enabled=1 ORDER BY id")
    pending = 0
    for src in sources:
        root = src["path"]
        if not os.path.isdir(long_path(root)):
            # Unplugged drive: report it, and do NOT mark its files missing.
            res.unavailable_sources.append(root)
            continue
        seen_keys: set[str] = set()

        def on_error(err: OSError) -> None:
            res.unreachable.append(f"{strip_long(getattr(err, 'filename', '') or '')}: {err.strerror}")

        for dirpath, dirs, files in os.walk(long_path(root), onerror=on_error, followlinks=False):
            kept = []
            for d in dirs:
                full = os.path.join(dirpath, d)
                if _is_link(full) or _hidden_or_system(full, d):
                    continue
                if any(is_inside(full, ex) for ex in excluded):
                    continue
                kept.append(d)
            dirs[:] = sorted(kept)
            for name in sorted(files):
                if _skip_file(name):
                    continue
                kind = file_kind(name)
                if kind is None:
                    res.other_files += 1
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(strip_long(full), strip_long(long_path(root)))
                rel_key = os.path.normcase(rel)
                seen_keys.add(rel_key)
                res.found += 1
                try:
                    pending += _record(cat, src["id"], rel, rel_key, strip_long(full), kind, run_id, res, hasher)
                except OSError as e:
                    res.unreachable.append(f"{strip_long(full)}: {e.strerror or e}")
                if progress and res.found % 50 == 0:
                    progress("Finding photos", res.found, 0)
                if pending >= 200:
                    cat.db.commit()
                    pending = 0
        cat.db.commit()
        pending = 0
        # Files we knew about in this source but didn't see this time.
        for row in cat.q("SELECT id, rel_key FROM files WHERE source_id=? AND missing=0", (src["id"],)):
            if row["rel_key"] not in seen_keys:
                cat.db.execute("UPDATE files SET missing=1, updated_at=? WHERE id=?", (time.time(), row["id"]))
                res.missing += 1
        cat.db.commit()
    _log.info("discover: %s", res)
    return res


def _record(cat: Catalog, source_id: int, rel: str, rel_key: str, full: str, kind: str,
            run_id: int, res: DiscoverResult, hasher: Callable[[str], str]) -> int:
    lp = long_path(full)
    st = os.stat(lp)
    row = cat.one("SELECT id, size, mtime_ns FROM files WHERE source_id=? AND rel_key=?", (source_id, rel_key))
    now = time.time()
    if row and row["size"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns:
        cat.db.execute("UPDATE files SET last_seen_run=?, missing=0, busy=0, rel_path=?, orig_path=? WHERE id=?",
                       (run_id, rel, full, row["id"]))
        res.unchanged += 1
        return 1
    sha = hasher(lp)
    st2 = os.stat(lp)
    if (st2.st_size, st2.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
        # Still being written (e.g. a copy in progress): hash once more, else try next run.
        sha = hasher(lp)
        st3 = os.stat(lp)
        if (st3.st_size, st3.st_mtime_ns) != (st2.st_size, st2.st_mtime_ns):
            res.busy += 1
            if row:
                cat.db.execute("UPDATE files SET busy=1, last_seen_run=? WHERE id=?", (run_id, row["id"]))
            return 1
        st = st3
    if row:
        cat.db.execute(
            "UPDATE files SET sha256=?, size=?, mtime_ns=?, kind=?, status='new', open_error=NULL, busy=0, "
            "missing=0, last_seen_run=?, rel_path=?, orig_path=?, updated_at=? WHERE id=?",
            (sha, st.st_size, st.st_mtime_ns, kind, run_id, rel, full, now, row["id"]))
        res.changed += 1
    else:
        cat.db.execute(
            "INSERT INTO files(source_id, rel_path, rel_key, orig_path, sha256, size, mtime_ns, kind, status, "
            "first_seen_run, last_seen_run, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?, 'new', ?,?,?,?)",
            (source_id, rel, rel_key, full, sha, st.st_size, st.st_mtime_ns, kind, run_id, run_id, now, now))
        res.new += 1
    return 1
