"""Changes you made by hand in the library (pseudocode section 23), checked at the start of each run.

  moved a photo to another folder  → counts as that correction (e.g. into People/Mom = "Mom is in it")
  deleted a photo from a folder    → it's kept out of that folder (never put back, nothing else deleted)
  edited a photo                   → your version is kept; the app never overwrites it
  added your own files             → left alone
Your changes are never "fixed" back. If most of the library vanished at once (a drive unplugged,
the folder renamed), nothing is learned from it and the next sort copies the files again.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import corrections, log
from .catalog import Catalog
from .fileops import sha256_file
from .paths import long_path
from .settings import Settings
from .sync import ADD_NEW, README

_log = log.get("handedits")


@dataclass
class Changes:
    moved: list[tuple[str, str]] = field(default_factory=list)     # (from, to)
    removed: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    skipped_reason: str = ""

    def counts(self) -> dict:
        return {"moved": len(self.moved), "removed": len(self.removed), "edited": len(self.edited),
                "unknown": len(self.unknown), "skipped": self.skipped_reason}


def _library_files(library: str) -> dict[str, int]:
    """rel path → size for every file in the library except the drop-in folder and READ MEs."""
    out = {}
    for dp, dirs, files in os.walk(long_path(library)):
        rel_dir = os.path.relpath(dp, long_path(library)).replace(os.sep, "/")
        if rel_dir == ADD_NEW or rel_dir.startswith(ADD_NEW + "/"):
            dirs[:] = []
            continue
        for f in files:
            if f == README or f.endswith(".partial"):
                continue
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            try:
                out[rel] = os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return out


def detect(cat: Catalog, s: Settings, library: str) -> Changes:
    ch = Changes()
    owned = [dict(r) for r in cat.q("SELECT * FROM owned_files WHERE content_key NOT LIKE 'readme:%'")]
    if not owned or not library:
        return ch
    if not os.path.isdir(long_path(library)):
        ch.skipped_reason = "library folder not found"
        return ch
    present = _library_files(library)
    owned_lc = {o["rel_path"].lower() for o in owned}
    unknown = {rel: size for rel, size in present.items() if rel.lower() not in owned_lc}
    missing = [o for o in owned if o["rel_path"] not in present]
    if len(missing) > max(20, len(owned) // 2):
        ch.skipped_reason = f"{len(missing)} library files are missing at once; treated as an accident"
        _log.warning("hand-edit check skipped: %s", ch.skipped_reason)
        return ch
    by_size: dict[int, list[str]] = {}
    for rel, size in unknown.items():
        by_size.setdefault(size, []).append(rel)
    hashed: dict[str, str] = {}
    claimed: set[str] = set()
    for o in missing:
        new_rel = None
        for rel in by_size.get(o["size"], []):
            if rel in claimed:
                continue
            if rel not in hashed:
                hashed[rel] = sha256_file(os.path.join(library, *rel.split("/")))
            if hashed[rel] == o["sha256"]:
                new_rel = rel
                break
        old_folder = o["rel_path"].rsplit("/", 1)[0]
        aid = _asset_for(cat, o["content_key"])
        if new_rel:
            claimed.add(new_rel)
            new_folder = new_rel.rsplit("/", 1)[0] if "/" in new_rel else ""
            ch.moved.append((o["rel_path"], new_rel))
            if aid:
                year = cat.scalar("SELECT substr(date,1,4) FROM dates WHERE asset_id=?", (aid,))
                corrections.move(cat, s, aid, o["content_key"], old_folder, new_folder, year, source="library")
        else:
            ch.removed.append(o["rel_path"])
            if aid:
                corrections.remove_from(cat, s, aid, o["content_key"], old_folder, source="library")
            else:
                corrections.labels_mod.add(cat, o["content_key"], "not_in", old_folder, "library")
        with cat.tx():
            cat.db.execute("DELETE FROM owned_files WHERE rel_path=?", (o["rel_path"],))
    for o in owned:
        rel = o["rel_path"]
        if rel not in present or present[rel] == o["size"]:
            continue
        ch.edited.append(rel)            # size changed: you edited it (kept as yours from now on)
        with cat.tx():
            cat.db.execute("DELETE FROM owned_files WHERE rel_path=?", (rel,))
    ch.unknown = sorted(rel for rel in unknown if rel not in claimed)
    for a, b in ch.moved:
        _log.info("you moved %s → %s", a, b)
    for r in ch.removed:
        _log.info("you removed %s", r)
    for r in ch.edited:
        _log.info("you edited %s", r)
    return ch


def _asset_for(cat: Catalog, content_key: str) -> int | None:
    sha, kind, idx = (content_key.split(":") + ["", ""])[:3]
    if kind == "file":
        return None
    return cat.scalar("SELECT a.id FROM assets a JOIN files f ON f.id=a.file_id WHERE f.sha256=? AND a.kind=? "
                      "AND IFNULL(a.cutout_index, '') = ? LIMIT 1", (sha, kind, idx))
