"""Approve and sort: make the library match the plan (pseudocode section 18).

Copies only. Originals are never touched here (they move at Finish).
Rules: R3 atomic writes, R4 journal, R5 only files we made are ever changed or deleted,
R11 the same plan always gives the same file names.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from . import log, plan as plan_mod
from .catalog import Catalog
from .fileops import CopyVerifyError, atomic_copy, atomic_write_bytes, remove_quietly, sha256_file
from .log import UserError
from .paths import is_inside, long_path

_log = log.get("sync")
README = "READ ME.txt"
SPARE_BYTES = 200 * 1024 * 1024        # keep this much free on the library drive

READMES = {
    "_Duplicates": "Extra copies of photos that are already in your library.\n"
                   "Open Review > Duplicates & bursts in ImageCleanuper to pick which copy to keep.\n"
                   "Nothing here is deleted until you choose.",
    "_Junk": "Screenshots, receipts, blurry shots and other things that don't look like keepers.\n"
             "If something here is a real photo, say so in ImageCleanuper and it moves back.",
    "_Could Not Open": "Files ImageCleanuper couldn't read (damaged, or a format it doesn't know).\n"
                       "They are copied here untouched so nothing goes missing. The log says why.",
}

_BAD = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def clean_name(name: str) -> str:
    """A file or folder name Windows accepts (no : ? etc., no trailing dots, no CON)."""
    out = "".join("_" if (c in _BAD or ord(c) < 32) else c for c in name).strip().rstrip(". ")
    if not out:
        out = "_"
    if out.split(".")[0].upper() in _RESERVED:
        out = "_" + out
    return out


@dataclass
class Item:
    rel: str                 # library path, "/" separators
    ckey: str
    src: str | None = None   # file to copy
    data: bytes | None = None  # or bytes to write (READ MEs)
    src_sha: str | None = None  # known fingerprint of src (originals: from the catalog)


def _final_name(e: plan_mod.Entry) -> str:
    base = os.path.basename(e.original)
    stem, ext = os.path.splitext(base)
    if e.src and e.src != e.original:          # a fixed or cut-out photo: saved as JPEG
        ext = os.path.splitext(e.src)[1] or ".jpg"
    kind_idx = e.ckey.split(":")
    if len(kind_idx) >= 3 and kind_idx[1] == "cutout" and kind_idx[2] != "":
        stem += "_" + chr(97 + int(kind_idx[2]))
    return clean_name(stem + ext)


def planned_items(pv: plan_mod.Preview, sha_of: dict[str, str] | None = None) -> dict[str, Item]:
    """Every library file the plan wants, with clash-free names that depend only on content (R11)."""
    sha_of = sha_of or {}
    items: dict[str, Item] = {}
    for folder, entries in pv.folders.items():
        if not entries:
            continue
        safe_folder = "/".join(clean_name(p) for p in folder.split("/"))
        named = sorted(((_final_name(e), e) for e in entries if e.src),
                       key=lambda t: (t[0].lower(), os.path.basename(t[1].original).lower(), t[1].ckey))
        used: set[str] = set()
        seen_keys: set[str] = set()
        for name, e in named:
            if e.ckey in seen_keys:            # the same photo twice in one folder: once is enough
                continue
            seen_keys.add(e.ckey)
            stem, ext = os.path.splitext(name)
            final, n = name, 1
            while final.lower() in used:
                n += 1
                final = f"{stem}_{n}{ext}"
            used.add(final.lower())
            rel = f"{safe_folder}/{final}"
            items[rel] = Item(rel, e.ckey, src=e.src, src_sha=sha_of.get(e.src) if e.src == e.original else None)
        top = folder.split("/")[0]
        if top in READMES and folder == top:
            rel = f"{safe_folder}/{README}"
            items[rel] = Item(rel, f"readme:{top}", data=(READMES[top] + "\n").encode("utf-8"))
    return items


@dataclass
class Diff:
    add: list[Item] = field(default_factory=list)       # new library files
    update: list[tuple[Item, dict]] = field(default_factory=list)   # owned file whose content should change
    restore: list[Item] = field(default_factory=list)   # owned file that went missing
    remove: list[dict] = field(default_factory=list)    # owned files the plan no longer wants
    same: int = 0
    src_info: dict[str, tuple[str, str]] = field(default_factory=dict)  # rel -> (src_sha, src_stat)

    @property
    def changes(self) -> int:
        return len(self.add) + len(self.update) + len(self.restore) + len(self.remove)


def _stat(path: str) -> str:
    st = os.stat(long_path(path))
    return f"{st.st_size}:{st.st_mtime_ns}"


def _src_fingerprint(it: Item, owned: dict | None) -> tuple[str, str | None]:
    """(sha, stat) of what this item would write. Re-hashes only when the source changed."""
    if it.data is not None:
        import hashlib
        return hashlib.sha256(it.data).hexdigest(), None
    st = _stat(it.src)
    if it.src_sha:
        return it.src_sha, st
    if owned and owned["src_stat"] == st and owned["content_key"] == it.ckey:
        return owned["src_sha"], st
    return sha256_file(it.src), st


def diff(cat: Catalog, library: str, items: dict[str, Item]) -> Diff:
    owned = {r["rel_path"]: dict(r) for r in cat.q("SELECT * FROM owned_files")}
    d = Diff()
    for rel, it in items.items():
        o = owned.get(rel)
        if o is None:                     # new: fingerprinted while copying (keeps the Report quick)
            d.add.append(it)
            continue
        try:
            sha, st = _src_fingerprint(it, o)
        except FileNotFoundError:
            _log.warning("source for %s is missing; skipped", rel)
            continue
        d.src_info[rel] = (sha, st)
        dest = os.path.join(library, *rel.split("/"))
        if o["src_sha"] != sha or o["content_key"] != it.ckey:
            d.update.append((it, o))
        elif not os.path.exists(long_path(dest)):
            d.restore.append(it)
        else:
            d.same += 1
    d.remove = [o for rel, o in sorted(owned.items()) if rel not in items]
    return d


@dataclass
class SyncResult:
    added: int = 0
    updated: int = 0
    restored: int = 0
    removed: int = 0
    unchanged: int = 0
    edited_by_you: list[str] = field(default_factory=list)   # library files you changed: left alone
    in_the_way: list[str] = field(default_factory=list)      # someone else's file already at that name
    failed: list[str] = field(default_factory=list)


def _free_space_check(library: str, d: Diff) -> None:
    need = 0
    for it in d.add + d.restore + [u[0] for u in d.update]:
        if it.src:
            try:
                need += os.path.getsize(long_path(it.src))
            except OSError:
                pass
    probe = library
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    free = shutil.disk_usage(probe).free
    if need + SPARE_BYTES > free:
        gb = lambda n: f"{n / 1024 ** 3:.1f} GB"  # noqa: E731
        raise UserError("There isn't enough free space for your library.",
                        f"Sorting needs about {gb(need)} and {gb(free)} is free on that drive. "
                        "Free up some space or choose another library folder in Settings.")


def _write(cat: Catalog, library: str, it: Item, sha: str | None, st: str | None, overwrite: bool) -> None:
    dest = os.path.join(library, *it.rel.split("/"))
    entry = cat.journal("sync_write", {"dest": dest, "rel": it.rel})
    try:
        if it.data is not None:
            atomic_write_bytes(dest, it.data, overwrite=overwrite)
            written = sha
        else:
            written = atomic_copy(it.src, dest, expected_sha=sha, overwrite=overwrite)
            sha = sha or written
        now = time.time()
        size = os.path.getsize(long_path(dest))
        with cat.tx():
            cat.db.execute(
                "INSERT INTO owned_files(rel_path, content_key, src_sha, src_stat, sha256, size, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(rel_path) DO UPDATE SET content_key=excluded.content_key,"
                " src_sha=excluded.src_sha, src_stat=excluded.src_stat, sha256=excluded.sha256, size=excluded.size,"
                " updated_at=excluded.updated_at",
                (it.rel, it.ckey, sha, st, written, size, now, now))
        cat.journal_set(entry, "done")
    except BaseException:
        cat.journal_set(entry, "rolled_back")
        raise


def _drop_empty_dirs(library: str, dirs: set[str]) -> None:
    """Remove folders that became completely empty (anything else inside → left alone)."""
    lib = os.path.abspath(library)
    for d in sorted(dirs, key=len, reverse=True):
        cur = os.path.abspath(d)
        while cur != lib and is_inside(cur, lib):
            try:
                os.rmdir(long_path(cur))
            except OSError:
                break
            cur = os.path.dirname(cur)


def sync_library(cat: Catalog, library: str, derived_dir: str, categories: dict | None = None,
                 progress: Callable[..., None] | None = None) -> SyncResult:
    progress = progress or (lambda *a: None)
    if not library:
        raise UserError("Choose a library folder first.", "Settings > Folders.")
    progress("Getting ready to sort", 0, 0)
    pv = plan_mod.preview(cat, derived_dir, categories)
    sha_of = {r["orig_path"]: r["sha256"] for r in cat.q("SELECT orig_path, sha256 FROM active_files")}
    items = planned_items(pv, sha_of)
    d = diff(cat, library, items)
    _free_space_check(library, d)
    now = time.time()
    sync_id = cat.db.execute("INSERT INTO syncs(started_at, state) VALUES (?, 'running')", (now,)).lastrowid
    cat.db.commit()
    res = SyncResult(unchanged=d.same)
    total, done = d.changes, 0
    touched_dirs: set[str] = set()
    try:
        # Removals first, so a renamed folder (e.g. "mom" → "Mom") frees its names on Windows.
        for o in d.remove:
            dest = os.path.join(library, *o["rel_path"].split("/"))
            done += 1
            progress("Sorting into your library", done, total)
            if os.path.exists(long_path(dest)):
                if os.path.getsize(long_path(dest)) != o["size"] or sha256_file(dest) != o["sha256"]:
                    res.edited_by_you.append(o["rel_path"])     # you changed it: never delete (R5)
                    with cat.tx():
                        cat.db.execute("DELETE FROM owned_files WHERE rel_path=?", (o["rel_path"],))
                    continue
                entry = cat.journal("sync_remove", {"dest": dest, "rel": o["rel_path"]})
                remove_quietly(dest)
                cat.journal_set(entry, "done")
            touched_dirs.add(os.path.dirname(dest))
            with cat.tx():
                cat.db.execute("DELETE FROM owned_files WHERE rel_path=?", (o["rel_path"],))
            res.removed += 1
        _drop_empty_dirs(library, touched_dirs)

        for it, o in d.update:
            done += 1
            progress("Sorting into your library", done, total, it.src)
            dest = os.path.join(library, *it.rel.split("/"))
            if os.path.exists(long_path(dest)) and (os.path.getsize(long_path(dest)) != o["size"]
                                                    or sha256_file(dest) != o["sha256"]):
                res.edited_by_you.append(it.rel)
                continue
            sha, st = d.src_info[it.rel]
            try:
                _write(cat, library, it, sha, st, overwrite=True)
                res.updated += 1
            except (OSError, CopyVerifyError):
                _log.exception("could not update %s", it.rel)
                res.failed.append(it.rel)

        for it in d.add + d.restore:
            done += 1
            progress("Sorting into your library", done, total, it.src)
            if it.rel in d.src_info:
                sha, st = d.src_info[it.rel]
            else:
                try:
                    st = None if it.data is not None else _stat(it.src)
                except FileNotFoundError:
                    res.failed.append(it.rel)
                    continue
                sha = it.src_sha if it.data is None else _src_fingerprint(it, None)[0]
            dest = os.path.join(library, *it.rel.split("/"))
            if os.path.exists(long_path(dest)):
                sha = sha or sha256_file(it.src)
                if sha256_file(dest) != sha:      # not ours and different: never overwrite (R5)
                    res.in_the_way.append(it.rel)
                    continue
                # Identical file already there (e.g. a crash after the copy): take it over.
                with cat.tx():
                    cat.db.execute(
                        "INSERT OR REPLACE INTO owned_files(rel_path, content_key, src_sha, src_stat, sha256, size,"
                        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (it.rel, it.ckey, sha, st, sha, os.path.getsize(long_path(dest)), now, now))
                res.added += 1
                continue
            try:
                _write(cat, library, it, sha, st, overwrite=False)
                if it in d.restore:
                    res.restored += 1
                else:
                    res.added += 1
            except (OSError, CopyVerifyError):
                _log.exception("could not copy %s", it.rel)
                res.failed.append(it.rel)
    except BaseException:
        with cat.tx():
            cat.db.execute("UPDATE syncs SET state='stopped', ended_at=?, counts=? WHERE id=?",
                           (time.time(), json.dumps(_counts(res)), sync_id))
        raise
    for rel in res.edited_by_you:
        _log.info("left alone (edited by you): %s", rel)
    for rel in res.in_the_way:
        _log.info("not copied, another file is already there: %s", rel)
    with cat.tx():
        cat.db.execute("UPDATE syncs SET state='done', ended_at=?, counts=? WHERE id=?",
                       (time.time(), json.dumps(_counts(res)), sync_id))
    return res


def _counts(res: SyncResult) -> dict:
    return {"added": res.added, "updated": res.updated, "restored": res.restored, "removed": res.removed,
            "unchanged": res.unchanged, "edited_by_you": len(res.edited_by_you),
            "in_the_way": len(res.in_the_way), "failed": len(res.failed)}


def pending_changes(cat: Catalog, library: str, derived_dir: str, categories: dict | None = None) -> Diff:
    """What Approve and sort would do right now (for the Report)."""
    pv = plan_mod.preview(cat, derived_dir, categories)
    sha_of = {r["orig_path"]: r["sha256"] for r in cat.q("SELECT orig_path, sha256 FROM active_files")}
    return diff(cat, library, planned_items(pv, sha_of))


def last_sync(cat: Catalog) -> dict | None:
    r = cat.one("SELECT * FROM syncs WHERE state='done' ORDER BY id DESC LIMIT 1")
    if not r:
        return None
    out = dict(r)
    out["counts"] = json.loads(r["counts"] or "{}")
    return out


def recover_entry(cat: Catalog, entry_id: int, kind: str, payload: dict) -> None:
    """After a crash: a half-written copy is deleted; the next sort redoes it (R4)."""
    if kind == "sync_write":
        remove_quietly(payload["dest"] + ".partial")
    cat.journal_set(entry_id, "rolled_back")


__all__ = ["sync_library", "pending_changes", "planned_items", "clean_name", "last_sync", "recover_entry",
           "SyncResult", "Diff"]
