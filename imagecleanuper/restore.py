"""Get files back from the backups (pseudocode section 20)."""

from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Literal

from . import log
from .catalog import Catalog
from .fileops import CHUNK, remove_quietly, sha256_file
from .paths import long_path

Conflict = Literal["keep_both", "overwrite", "skip"]
ConflictHandler = Callable[[str], Conflict]
Progress = Callable[[str, int, int], None]
_log = log.get("restore")


@dataclass
class RestoreResult:
    restored: int = 0
    already_there: int = 0
    skipped: int = 0
    kept_both: int = 0
    failed: list[str] = field(default_factory=list)


def _entry_for(cat: Catalog, sha: str, up_to_part: int | None):
    sql = ("SELECT e.arcname, p.zip_name FROM backup_entries e JOIN backup_parts p ON p.id=e.part_id "
           "WHERE e.sha256=? AND p.verified=1")
    args: tuple = (sha,)
    if up_to_part is not None:
        sql += " AND p.id<=?"
        args += (up_to_part,)
    return cat.one(sql + " ORDER BY p.id LIMIT 1", args)


def _keep_both_name(dest: str) -> str:
    base, ext = os.path.splitext(dest)
    i = 1
    while True:
        cand = f"{base} (restored{'' if i == 1 else ' ' + str(i)}){ext}"
        if not os.path.exists(long_path(cand)):
            return cand
        i += 1


def _extract(backup_dir: str, zip_name: str, arcname: str, dest: str, sha: str) -> None:
    tmp = dest + ".partial"
    os.makedirs(long_path(os.path.dirname(dest)), exist_ok=True)
    h = hashlib.sha256()
    try:
        with zipfile.ZipFile(long_path(os.path.join(backup_dir, zip_name))) as zf, \
                zf.open(arcname) as src, open(long_path(tmp), "wb") as out:
            for block in iter(lambda: src.read(CHUNK), b""):
                h.update(block)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if h.hexdigest() != sha:
            raise ValueError("restored file did not match its fingerprint")
        os.replace(long_path(tmp), long_path(dest))
    except BaseException:
        remove_quietly(tmp)
        raise


def restore_files(cat: Catalog, backup_dir: str, file_ids: list[int] | None = None,
                  up_to_part: int | None = None, on_conflict: ConflictHandler | None = None,
                  progress: Progress | None = None) -> RestoreResult:
    """Put files back at their original paths. ``file_ids=None`` restores everything."""
    res = RestoreResult()
    if file_ids is None:
        rows = cat.q("SELECT id, sha256, orig_path FROM files ORDER BY id")
    else:
        marks = ",".join("?" * len(file_ids)) or "NULL"
        rows = cat.q(f"SELECT id, sha256, orig_path FROM files WHERE id IN ({marks}) ORDER BY id", tuple(file_ids))
    for i, r in enumerate(rows, 1):
        if progress:
            progress("Restoring", i, len(rows))
        entry = _entry_for(cat, r["sha256"], up_to_part)
        if entry is None:
            continue  # not in the chosen backup
        dest = r["orig_path"]
        if os.path.exists(long_path(dest)):
            if sha256_file(dest) == r["sha256"]:
                res.already_there += 1
                continue
            choice = on_conflict(dest) if on_conflict else "skip"  # never overwrite silently
            if choice == "skip":
                res.skipped += 1
                continue
            if choice == "keep_both":
                dest = _keep_both_name(dest)
                res.kept_both += 1
        try:
            _extract(backup_dir, entry["zip_name"], entry["arcname"], dest, r["sha256"])
            res.restored += 1
        except Exception as e:  # one bad file must not stop the rest
            _log.exception("restore failed for %s", dest)
            res.failed.append(f"{dest}: {e}")
    return res


def find_files(cat: Catalog, text: str, limit: int = 200) -> list:
    like = f"%{text}%"
    return cat.q("SELECT id, orig_path, size FROM files WHERE orig_path LIKE ? ORDER BY orig_path LIMIT ?",
                 (like, limit))


def backups(cat: Catalog) -> list:
    return cat.q("SELECT id, zip_name, created_run, file_count, bytes, verified_at FROM backup_parts "
                 "WHERE verified=1 ORDER BY id")


def original_of(cat: Catalog, library: str, library_file: str) -> dict | None:
    """Where a library photo came from: its original path, whether it's still there, and its backup zip."""
    from .paths import is_inside
    path = os.path.abspath(library_file)
    if not library or not is_inside(path, library):
        return None
    rel = os.path.relpath(path, os.path.abspath(library)).replace(os.sep, "/")
    row = cat.one("SELECT content_key FROM owned_files WHERE rel_path=? COLLATE NOCASE", (rel,))
    sha = row["content_key"].split(":")[0] if row else None
    if sha is None:
        from .fileops import sha256_file
        try:
            sha = sha256_file(path)
        except OSError:
            return None
    return _original_by_sha(cat, sha)


def original_by_file_id(cat: Catalog, file_id: int) -> dict | None:
    sha = cat.scalar("SELECT sha256 FROM files WHERE id=?", (file_id,))
    return _original_by_sha(cat, sha) if sha else None


def _original_by_sha(cat: Catalog, sha: str) -> dict | None:
    f = cat.one("SELECT id, orig_path, finished_at FROM files WHERE sha256=? ORDER BY finished_at IS NULL DESC, id "
                "LIMIT 1", (sha,))
    if not f:
        return None
    zip_name = cat.scalar("SELECT p.zip_name FROM backup_entries e JOIN backup_parts p ON p.id=e.part_id "
                          "WHERE e.sha256=? AND p.verified=1 ORDER BY p.id LIMIT 1", (sha,))
    return {"file_id": f["id"], "orig_path": f["orig_path"], "exists": os.path.exists(long_path(f["orig_path"])),
            "finished": f["finished_at"] is not None, "backup_zip": zip_name}
