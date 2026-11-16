"""Finish: originals leave the photo folders, the library keeps them (pseudocode section 19).

By the time you Finish, Approve and sort has already put a verified copy of every photo in the
library and every original is in a verified backup zip. So "moving" an original means:
  1. check it again (unchanged since the backup, still in a verified zip, copy in the library),
  2. give the library copies the original's dates (so Explorer still sorts them by date taken),
  3. delete the original (journaled first, rule R4).
Copies you chose to remove go to the Recycle Bin instead. Folders left completely empty are deleted.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from . import log, sync
from .catalog import Catalog
from .fileops import sha256_file
from .paths import is_inside, long_path

_log = log.get("finish")
# Windows/macOS leave these in photo folders; they don't stop a folder counting as empty.
LITTER = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}


def _trash(path: str) -> None:
    from send2trash import send2trash
    send2trash(path)


@dataclass
class Plan:
    move: list[dict] = field(default_factory=list)        # originals with a copy in the library
    recycle: list[dict] = field(default_factory=list)     # copies you chose to remove
    left: list[tuple[str, str]] = field(default_factory=list)   # (path, why it stays)
    problems: list[str] = field(default_factory=list)     # anything here blocks Finish
    backup_files: int = 0
    backup_date: float | None = None

    @property
    def ready(self) -> bool:
        return not self.problems and bool(self.move or self.recycle)


def _verified_shas(cat: Catalog) -> set[str]:
    return {r[0] for r in cat.q("SELECT DISTINCT e.sha256 FROM backup_entries e "
                                "JOIN backup_parts p ON p.id = e.part_id WHERE p.verified = 1")}


def plan(cat: Catalog, library: str, derived_dir: str, categories: dict | None = None) -> Plan:
    """What Finish would do now, and anything that must be sorted out first."""
    p = Plan()
    if not library:
        p.problems.append("Choose a library folder first (Settings).")
        return p
    if sync.last_sync(cat) is None:
        p.problems.append("Approve and sort first, so your library has a copy of every photo.")
        return p
    pending = sync.pending_changes(cat, library, derived_dir, categories)
    if pending.changes:
        p.problems.append(f"Your library is {pending.changes:,} change{'s' if pending.changes != 1 else ''} "
                          "behind the plan. Open the report and press Approve and sort first.")
    if cat.scalar("SELECT COUNT(*) FROM runs WHERE state='running'"):
        p.problems.append("A run is still going.")
    verified = _verified_shas(cat)
    p.backup_files = len(verified)
    p.backup_date = cat.scalar("SELECT MAX(verified_at) FROM backup_parts WHERE verified=1")
    in_library = {r[0].split(":")[0] for r in cat.q("SELECT content_key FROM owned_files")}
    removed = {r[0] for r in cat.q("SELECT content_key FROM copy_decisions WHERE decision='remove'")}
    files = cat.q("SELECT f.id, f.orig_path, f.sha256, f.size, f.status, f.source_id FROM active_files f "
                  "WHERE f.finished_at IS NULL AND f.missing = 0 ORDER BY f.orig_path")
    keys_of: dict[int, list[str]] = {}
    for r in cat.q("SELECT a.file_id, f.sha256 || ':' || a.kind || ':' || IFNULL(a.cutout_index, '') AS k "
                   "FROM assets a JOIN files f ON f.id = a.file_id"):
        keys_of.setdefault(r["file_id"], []).append(r["k"])
    for f in files:
        row = dict(f)
        if f["sha256"] not in verified:
            p.left.append((f["orig_path"], "not in a verified backup yet (run first)"))
        elif f["sha256"] in in_library:
            p.move.append(row)
        elif keys_of.get(f["id"]) and all(k in removed for k in keys_of[f["id"]]):
            p.recycle.append(row)
        else:
            p.left.append((f["orig_path"], "not in your library yet (run, then Approve and sort)"))
    return p


@dataclass
class Result:
    moved: int = 0
    recycled: int = 0
    folders_removed: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (path, why)


def _library_copies(cat: Catalog, library: str, sha: str) -> list[str]:
    """Library files that are byte-for-byte this original (not fixed or cut-out versions)."""
    return [os.path.join(library, *r[0].split("/"))
            for r in cat.q("SELECT rel_path FROM owned_files WHERE src_sha=? AND sha256=?", (sha, sha))]


def _remove_empty_folders(start_dirs: set[str], roots: list[str]) -> int:
    """Delete folders we emptied, deepest first, up to (never including) the photo folder itself."""
    n = 0
    for d in sorted(start_dirs, key=len, reverse=True):
        root = next((r for r in roots if is_inside(d, r)), None)
        cur = os.path.abspath(d)
        while root and os.path.abspath(cur) != os.path.abspath(root) and is_inside(cur, root):
            try:
                names = os.listdir(long_path(cur))
            except OSError:
                break
            if any(nm.lower() not in LITTER for nm in names):
                break
            try:
                for nm in names:
                    os.remove(long_path(os.path.join(cur, nm)))
                os.rmdir(long_path(cur))
                n += 1
            except OSError:
                break
            cur = os.path.dirname(cur)
    return n


def finish(cat: Catalog, library: str, derived_dir: str, roots: list[str], categories: dict | None = None,
           progress: Callable[..., None] | None = None, trash: Callable[[str], None] = _trash) -> Result:
    progress = progress or (lambda *a: None)
    progress("Checking", 0, 0)
    p = plan(cat, library, derived_dir, categories)
    if p.problems:
        from .log import UserError
        raise UserError("Finish can't start yet.", " ".join(p.problems))
    res = Result()
    total = len(p.move) + len(p.recycle)
    touched: set[str] = set()
    for i, (f, action) in enumerate([(f, "move") for f in p.move] + [(f, "recycle") for f in p.recycle], 1):
        progress("Finishing", i, total, f["orig_path"] if action == "move" else None)
        src = f["orig_path"]
        # Check again right now: never delete an original we can't account for.
        if f["sha256"] not in _verified_shas(cat):
            res.skipped.append((src, "not in a verified backup"))
            continue
        try:
            if sha256_file(src) != f["sha256"]:
                res.skipped.append((src, "changed since it was backed up (run again first)"))
                continue
        except FileNotFoundError:
            res.skipped.append((src, "already gone"))
            continue
        if action == "move":
            copies = [c for c in _library_copies(cat, library, f["sha256"]) if os.path.exists(long_path(c))]
            fixed = cat.scalar("SELECT COUNT(*) FROM owned_files WHERE content_key LIKE ? || ':%'", (f["sha256"],))
            if not copies and not fixed:
                res.skipped.append((src, "its library copy is missing (Approve and sort again)"))
                continue
            for c in copies:
                try:
                    shutil.copystat(long_path(src), long_path(c))   # keep the original's dates
                except OSError:
                    pass
        entry = cat.journal(f"finish_{action}", {"src": src, "file_id": f["id"], "sha": f["sha256"]})
        try:
            if action == "move":
                os.remove(long_path(src))
            else:
                trash(src)
        except OSError as e:
            cat.journal_set(entry, "rolled_back")
            res.skipped.append((src, f"couldn't be removed ({e.strerror or e})"))
            continue
        with cat.tx():
            cat.db.execute("UPDATE files SET finished_at=? WHERE id=?", (time.time(), f["id"]))
        cat.journal_set(entry, "done")
        touched.add(os.path.dirname(os.path.abspath(src)))
        if action == "move":
            res.moved += 1
        else:
            res.recycled += 1
    res.folders_removed = _remove_empty_folders(touched, roots)
    for path, why in res.skipped:
        _log.info("Finish left %s: %s", path, why)
    return res


def recover_entry(cat: Catalog, entry_id: int, payload: dict) -> None:
    """A crash mid-Finish: if the original is gone, record it as finished; otherwise nothing happened."""
    if not os.path.exists(long_path(payload["src"])):
        with cat.tx():
            cat.db.execute("UPDATE files SET finished_at=COALESCE(finished_at, ?) WHERE id=?",
                           (time.time(), payload["file_id"]))
        cat.journal_set(entry_id, "done")
    else:
        cat.journal_set(entry_id, "rolled_back")
