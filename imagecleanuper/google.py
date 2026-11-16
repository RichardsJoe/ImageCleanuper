"""Google Photos import via Google Takeout (pseudocode section 21).

1. Each Takeout zip is copied into the backup folder and checked (it IS the backup of its photos, rule R2).
2. Photos and videos are extracted to data\\imports\\<n>; Google's sidecar .json files give each photo's
   date (Google strips it from many files), kept in google_meta.
3. That folder becomes a photo source; the next run sorts the photos like any others.
Takeout quirks handled: names truncated to ~47 characters, "(1)" moved before ".json",
".supplemental-metadata" (sometimes truncated), "-edited" copies sharing the original's sidecar,
and the same photo in several album folders (exact duplicates are found later anyway).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from typing import Callable

from . import log
from .catalog import Catalog
from .discover import PHOTO_EXT, VIDEO_EXT
from .fileops import CHUNK, atomic_copy, atomic_write, sha256_file
from .log import UserError
from .paths import long_path, norm

_log = log.get("google")
TAKEOUT_URL = "https://takeout.google.com/settings/takeout/custom/photos"
BACKUP_SUBDIR = "Google Takeout"
_EDITED = re.compile(r"-(edited|bearbeitet|modifié|editado|modificato)$", re.I)
_NUM = re.compile(r"^(.*)\((\d+)\)$")


@dataclass
class ImportResult:
    import_id: int = 0
    zips: int = 0
    photos: int = 0
    dated: int = 0
    skipped: list[str] = field(default_factory=list)


def _is_media(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    return ext in PHOTO_EXT or ext in VIDEO_EXT


def _sidecar_base(json_name: str) -> tuple[str, str]:
    """('IMG_1.JPG', '(1)') from 'IMG_1.JPG.supplemental-metadata(1).json' and its truncated forms."""
    base = json_name[:-5] if json_name.lower().endswith(".json") else json_name
    num = ""
    m = _NUM.match(base)
    if m:
        base, num = m.group(1), f"({m.group(2)})"
    low = base.lower()
    tag = ".supplemental-metadata"
    for cut in range(len(tag), 1, -1):             # ".supplemental-metadata" or any truncated piece of it
        if low.endswith(tag[:cut]):
            base = base[:-cut]
            break
    return base, num


def match_sidecars(media: list[str], jsons: list[str]) -> dict[str, str]:
    """Map each media path (inside the zip) to its sidecar json path, per folder."""
    by_dir: dict[str, list[tuple[str, str, str]]] = {}
    for j in jsons:
        d, name = os.path.split(j)
        b, num = _sidecar_base(name)
        by_dir.setdefault(d, []).append((b, num, j))
    out = {}
    for mpath in media:
        d, name = os.path.split(mpath)
        stem, ext = os.path.splitext(name)
        num = ""
        m = _NUM.match(stem)
        if m:
            stem, num = m.group(1), f"({m.group(2)})"
        stem = _EDITED.sub("", stem)
        want = stem + ext
        best, best_len = None, 0
        for b, jnum, j in by_dir.get(d, []):
            if jnum != num:
                continue
            if b == want:
                best = j
                break
            # truncated sidecar name: a prefix of the media name (long enough to be safe)
            if len(b) >= 30 and want.startswith(b) and len(b) > best_len:
                best, best_len = j, len(b)
            # sidecar named without the extension (older exports)
            elif b == stem and best is None:
                best = j
        if best:
            out[mpath] = best
    return out


def _taken_date(data: bytes) -> str | None:
    try:
        meta = json.loads(data.decode("utf-8"))
        ts = int((meta.get("photoTakenTime") or {}).get("timestamp") or 0)
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    if ts <= 0:
        return None
    return dt.datetime.fromtimestamp(ts).date().isoformat()     # Google stores UTC; shown in local time


def check_zips(paths: list[str]) -> tuple[int, int, list[str]]:
    """(media files, total bytes, problems) for the zips you picked."""
    media, size, problems = 0, 0, []
    for p in paths:
        try:
            with zipfile.ZipFile(long_path(p)) as z:
                names = [i for i in z.infolist() if not i.is_dir()]
                media += sum(1 for i in names if _is_media(i.filename))
                size += os.path.getsize(long_path(p))
        except (OSError, zipfile.BadZipFile):
            problems.append(f"{os.path.basename(p)} isn't a readable zip (did the download finish?)")
    return media, size, problems


def import_zips(cat: Catalog, backup_dir: str, data_dir: str, zip_paths: list[str],
                progress: Callable[..., None] | None = None) -> ImportResult:
    progress = progress or (lambda *a: None)
    if not zip_paths:
        raise UserError("Choose the zip files from Google first.")
    _, size, problems = check_zips(zip_paths)
    if problems:
        raise UserError("Some zips can't be read.", " ".join(problems))
    import shutil
    free = shutil.disk_usage(data_dir if os.path.exists(data_dir) else os.path.dirname(data_dir)).free
    if size * 2.2 > free:
        raise UserError("There isn't enough free space for this import.",
                        f"It needs about {size * 2.2 / 1024 ** 3:.0f} GB (a backup copy plus the photos).")
    with cat.tx():
        iid = cat.db.execute("INSERT INTO google_imports(created_at, state, zips) VALUES (?, 'running', '[]')",
                             (time.time(),)).lastrowid
    res = ImportResult(import_id=iid)
    dest_root = os.path.join(data_dir, "imports", f"google_{iid:03d}")
    zips_done = []
    for zi, zp in enumerate(zip_paths, 1):
        zip_name = f"{BACKUP_SUBDIR}/{iid:03d}/{os.path.basename(zp)}"
        backup_copy = os.path.join(backup_dir, *zip_name.split("/"))
        progress("Copying Google's zips into your backup", zi, len(zip_paths))
        if not os.path.exists(long_path(backup_copy)):
            atomic_copy(zp, backup_copy, expected_sha=sha256_file(zp))
        with zipfile.ZipFile(long_path(backup_copy)) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            media = [i.filename for i in infos if _is_media(i.filename)]
            jsons = [i.filename for i in infos if i.filename.lower().endswith(".json")]
            sidecars = match_sidecars(media, jsons)
            entries, dates = [], []
            for mi, name in enumerate(media, 1):
                progress("Unpacking Google Photos", mi, len(media))
                dest = os.path.join(dest_root, *name.split("/"))
                h = hashlib.sha256()
                info = z.getinfo(name)

                def write(f, name=name):
                    with z.open(name) as src:
                        for block in iter(lambda: src.read(CHUNK), b""):
                            h.update(block)
                            f.write(block)
                try:
                    if not os.path.exists(long_path(dest)):
                        atomic_write(dest, write)
                        sha = h.hexdigest()
                    else:
                        sha = sha256_file(dest)
                except OSError as e:
                    res.skipped.append(f"{name}: {e}")
                    continue
                entries.append((sha, name, info.file_size))
                res.photos += 1
                if name in sidecars:
                    d = _taken_date(z.read(sidecars[name]))
                    if d:
                        dates.append((norm(dest), d, iid))
                        res.dated += 1
        with cat.tx():
            # The zip in the backup folder is the verified backup of everything in it.
            part = cat.db.execute(
                "INSERT INTO backup_parts(zip_name, kind, created_run, file_count, bytes, verified, verified_at) "
                "VALUES (?, 'google_takeout', NULL, ?, ?, 1, ?)",
                (zip_name, len(entries), os.path.getsize(long_path(backup_copy)), time.time())).lastrowid
            cat.db.executemany("INSERT OR IGNORE INTO backup_entries(part_id, sha256, arcname, size) VALUES (?,?,?,?)",
                               [(part, s, a, sz) for s, a, sz in entries])
            cat.db.executemany("INSERT OR REPLACE INTO google_meta(path_key, taken_date, import_id) VALUES (?,?,?)",
                               dates)
        zips_done.append(zip_name)
        res.zips += 1
    with cat.tx():
        now = time.time()
        cat.db.execute("INSERT INTO sources(kind, path, path_key, label, enabled, created_at) "
                       "VALUES ('google_import', ?, ?, ?, 1, ?)",
                       (dest_root, norm(dest_root), f"google_{iid:03d}", now))
        cat.db.execute("UPDATE google_imports SET state='done', zips=?, files=? WHERE id=?",
                       (json.dumps(zips_done), res.photos, iid))
    _log.info("Google import %s: %s photos from %s zips (%s with Google's date)", iid, res.photos, res.zips, res.dated)
    return res


def google_date_for(cat: Catalog, orig_path: str) -> str | None:
    return cat.scalar("SELECT taken_date FROM google_meta WHERE path_key=?", (norm(orig_path),))


def import_sources(cat: Catalog) -> list[str]:
    return [r[0] for r in cat.q("SELECT path FROM sources WHERE kind='google_import' AND enabled=1")]


def get_state(cat: Catalog, key: str) -> str | None:
    return cat.scalar("SELECT value FROM app_state WHERE key=?", (key,))


def set_state(cat: Catalog, key: str, value: str) -> None:
    with cat.tx():
        cat.db.execute("INSERT OR REPLACE INTO app_state(key, value) VALUES (?,?)", (key, value))
