"""Incremental, verified zip backups (pseudocode section 7, rules R2 and R3).

Each file's content is backed up once. New content goes into zip "parts" of at most
PART_MAX_FILES files / PART_MAX_BYTES. A part counts as a backup only after every
member has been read back and matched against its fingerprint.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import os
import shutil
import time
import zipfile
from dataclasses import dataclass
from typing import Callable

from . import log
from .catalog import Catalog
from .fileops import CHUNK, atomic_write_bytes, remove_quietly
from .log import UserError
from .paths import long_path

PART_MAX_FILES = 1000
PART_MAX_BYTES = 4 * 1024 ** 3

_log = log.get("backup")
Progress = Callable[[str, int, int], None]


@dataclass
class BackupResult:
    parts_written: int = 0
    files_backed_up: int = 0
    bytes_backed_up: int = 0
    already_backed_up: int = 0
    changed_during_backup: int = 0


def pending_files(cat: Catalog) -> list:
    """One row per content (sha256) that isn't in a verified backup yet."""
    return cat.q(
        """
        SELECT f.sha256, MIN(f.id) AS id, f.size, f.orig_path, f.rel_path, s.label
        FROM files f JOIN sources s ON s.id = f.source_id
        WHERE f.missing = 0 AND f.busy = 0
          AND f.sha256 NOT IN (SELECT e.sha256 FROM backup_entries e
                               JOIN backup_parts p ON p.id = e.part_id WHERE p.verified = 1)
        GROUP BY f.sha256
        ORDER BY MIN(f.id)
        """)


def all_backed_up(cat: Catalog) -> bool:
    return len(pending_files(cat)) == 0


def _chunks(rows: list, max_files: int, max_bytes: int):
    chunk, size = [], 0
    for r in rows:
        if chunk and (len(chunk) >= max_files or size + r["size"] > max_bytes):
            yield chunk
            chunk, size = [], 0
        chunk.append(r)
        size += r["size"]
    if chunk:
        yield chunk


def _zip_time(path: str) -> tuple:
    t = dt.datetime.fromtimestamp(os.stat(long_path(path)).st_mtime)
    if t.year < 1980:  # zip format can't store earlier dates
        t = dt.datetime(1980, 1, 1)
    return (t.year, t.month, t.day, t.hour, t.minute, t.second)


def _arcname(label: str, rel_path: str) -> str:
    return f"{label}/{rel_path.replace(os.sep, '/')}"


def backup_new_files(cat: Catalog, backup_dir: str, run_id: int,
                     progress: Progress | None = None,
                     max_files: int = PART_MAX_FILES, max_bytes: int = PART_MAX_BYTES) -> BackupResult:
    res = BackupResult()
    rows = pending_files(cat)
    if not rows:
        return res
    os.makedirs(long_path(backup_dir), exist_ok=True)
    need = sum(r["size"] for r in rows) * 1.05 + 1024 ** 3
    free = shutil.disk_usage(long_path(backup_dir)).free
    if free < need:
        raise UserError(
            f"Not enough space for the backup: it needs about {need / 1024**3:.1f} GB "
            f"and {free / 1024**3:.1f} GB is free.",
            "Free up space on that drive or choose another backup folder in Settings.")
    total = len(rows)
    done = 0
    today = dt.date.today().isoformat()
    for n, chunk in enumerate(_chunks(rows, max_files, max_bytes), 1):
        zip_name = f"photos_{today}_run{run_id:04d}_{n:03d}.zip"
        final = os.path.join(backup_dir, zip_name)
        while os.path.exists(long_path(final)):  # never overwrite an older backup
            n += 1000
            zip_name = f"photos_{today}_run{run_id:04d}_{n:03d}.zip"
            final = os.path.join(backup_dir, zip_name)
        tmp = final + ".partial"
        entry = cat.journal("backup_part", {"tmp": tmp, "final": final, "zip_name": zip_name})
        try:
            written = _write_part(tmp, chunk, res, progress, done, total)
            if not verify_zip(tmp, {a: s for a, s, _ in written}):
                raise UserError("The backup check failed: a file in the new backup didn't match.",
                                "Nothing was changed. Try running again; if it keeps failing, "
                                "the backup drive may be faulty.")
            os.replace(long_path(tmp), long_path(final))
            with cat.tx():
                cur = cat.db.execute(
                    "INSERT INTO backup_parts(zip_name, kind, created_run, file_count, bytes, verified, verified_at) "
                    "VALUES (?, 'photos', ?, ?, ?, 1, ?)",
                    (zip_name, run_id, len(written), sum(sz for _, _, sz in written), time.time()))
                cat.db.executemany(
                    "INSERT INTO backup_entries(part_id, sha256, arcname, size) VALUES (?,?,?,?)",
                    [(cur.lastrowid, s, a, sz) for a, s, sz in written])
                cat.db.execute("UPDATE journal SET state='done', updated_at=? WHERE id=?", (time.time(), entry))
        except BaseException:
            remove_quietly(tmp)
            if os.path.exists(long_path(final)) and not cat.scalar(
                    "SELECT 1 FROM backup_parts WHERE zip_name=?", (zip_name,)):
                remove_quietly(final)
            cat.journal_set(entry, "rolled_back")
            raise
        res.parts_written += 1
        done += len(chunk)
    write_manifest(cat, backup_dir)
    _log.info("backup: %s", res)
    return res


def _write_part(tmp: str, chunk: list, res: BackupResult, progress: Progress | None,
                done: int, total: int) -> list[tuple[str, str, int]]:
    """Write one zip part. Returns (arcname, sha256, size) as actually written."""
    written: list[tuple[str, str, int]] = []
    os.makedirs(long_path(os.path.dirname(os.path.abspath(tmp))), exist_ok=True)
    with open(long_path(tmp), "wb") as raw:
        with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for i, r in enumerate(chunk):
                arc = _arcname(r["label"], r["rel_path"])
                info = zipfile.ZipInfo(arc, date_time=_zip_time(r["orig_path"]))
                info.compress_type = zipfile.ZIP_STORED
                h = hashlib.sha256()
                size = 0
                with open(long_path(r["orig_path"]), "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                    for block in iter(lambda: src.read(CHUNK), b""):
                        h.update(block)
                        dst.write(block)
                        size += len(block)
                sha = h.hexdigest()
                if sha != r["sha256"]:
                    # The file changed since discovery. Record what was actually saved.
                    res.changed_during_backup += 1
                    _log.warning("changed during backup: %s", r["orig_path"])
                written.append((arc, sha, size))
                res.files_backed_up += 1
                res.bytes_backed_up += size
                if progress:
                    progress("Backing up", done + i + 1, total)
        raw.flush()
        os.fsync(raw.fileno())
    return written


def verify_zip(path: str, expected: dict[str, str]) -> bool:
    """Re-read every member and compare fingerprints; names must match exactly."""
    try:
        with zipfile.ZipFile(long_path(path)) as zf:
            names = zf.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected):
                return False
            for name in names:
                h = hashlib.sha256()
                with zf.open(name) as m:
                    for block in iter(lambda: m.read(CHUNK), b""):
                        h.update(block)
                if h.hexdigest() != expected[name]:
                    return False
    except (zipfile.BadZipFile, OSError):
        return False
    return True


def write_manifest(cat: Catalog, backup_dir: str) -> str:
    """A human-readable list of everything in the backups (sha256, size, original path, zip)."""
    rows = cat.q(
        """
        SELECT e.sha256, e.size, p.zip_name, e.arcname,
               (SELECT f.orig_path FROM files f WHERE f.sha256 = e.sha256 ORDER BY f.id LIMIT 1) AS orig_path
        FROM backup_entries e JOIN backup_parts p ON p.id = e.part_id
        WHERE p.verified = 1 ORDER BY p.id, e.arcname
        """)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sha256", "size", "original_path", "zip_file", "name_in_zip"])
    for r in rows:
        w.writerow([r["sha256"], r["size"], r["orig_path"] or "", r["zip_name"], r["arcname"]])
    path = os.path.join(backup_dir, "manifest.csv")
    atomic_write_bytes(path, buf.getvalue().encode("utf-8-sig"), overwrite=True)
    return path


def recover_backup_entry(cat: Catalog, entry_id: int, payload: dict) -> None:
    """Crash recovery: an unfinished part is never a backup (R2)."""
    remove_quietly(payload["tmp"])
    if not cat.scalar("SELECT 1 FROM backup_parts WHERE zip_name=? AND verified=1", (payload["zip_name"],)):
        remove_quietly(payload["final"])
        cat.journal_set(entry_id, "rolled_back")
    else:
        cat.journal_set(entry_id, "done")
