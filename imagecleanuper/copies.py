"""Duplicates and burst shots: grouping, the app's pick, and your keep/remove choices (wireframe 11).

Removing a copy takes it out of the library at once; the file itself goes to the Recycle Bin at
Finish, after the backup is verified (rules R1/R2). Choices are stored by content fingerprint so
they survive re-analysis, and each click is one batch that Ctrl+Z undoes.
"""

from __future__ import annotations

import datetime as dt
import os
import time
from dataclasses import dataclass

import numpy as np

from .catalog import Catalog

BURST_SECONDS = 3
BURST_SIMILARITY = 0.90      # CLIP cosine; same scene, slightly different moment


def _key(r) -> str:
    return f"{r['sha256']}:{r['kind']}:{r['cutout_index'] if r['cutout_index'] is not None else ''}"


def _when(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw.strip()[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def find_bursts(rows: list, taken: set[int]) -> list[list[int]]:
    """Groups of asset ids: same camera, each within BURST_SECONDS of the next, nearly the same picture."""
    from .ai import clip
    items = []
    for r in rows:
        t = _when(r["exif_date_raw"])
        if t is None or r["id"] in taken or not r["clip"]:
            continue
        items.append((r["camera_make"] or "", r["camera_model"] or "", t, r["id"], clip.from_blob(r["clip"])))
    items.sort(key=lambda x: (x[0], x[1], x[2]))
    groups, cur = [], []
    for it in items:
        if cur and it[:2] == cur[-1][:2] and (it[2] - cur[-1][2]).total_seconds() <= BURST_SECONDS \
                and float(np.dot(it[4], cur[-1][4])) >= BURST_SIMILARITY:
            cur.append(it)
        else:
            if len(cur) >= 2:
                groups.append([x[3] for x in cur])
            cur = [it]
    if len(cur) >= 2:
        groups.append([x[3] for x in cur])
    return groups


@dataclass
class Copy:
    asset_id: int
    image: str
    name: str
    folder: str
    pixels: int
    size: int
    sharpness: float
    content_key: str
    decision: str | None     # keep | remove | None (undecided)


@dataclass
class GroupView:
    group_id: int
    kind: str                # exact | near | burst
    copies: list[Copy]
    pick: list[int]          # indexes the app suggests keeping

    @property
    def decided(self) -> bool:
        return all(c.decision for c in self.copies)


def groups(cat: Catalog, derived_dir: str, undecided_only: bool = True) -> list[GroupView]:
    rows = cat.q("""SELECT g.id gid, g.kind gkind, dm.is_kept, a.id, a.kind, a.cutout_index, a.derived_path,
                           f.sha256, f.size, f.orig_path, f.rel_path, h.pixels, h.sharpness, cd.decision
                    FROM dup_groups g JOIN dup_members dm ON dm.group_id = g.id JOIN assets a ON a.id = dm.asset_id
                    JOIN active_files f ON f.id = a.file_id LEFT JOIN hashes h ON h.asset_id = a.id
                    LEFT JOIN copy_decisions cd ON cd.content_key = (f.sha256 || ':' || a.kind || ':' ||
                                                                     IFNULL(a.cutout_index, ''))
                    ORDER BY g.id, a.id""")
    by: dict[int, GroupView] = {}
    for r in rows:
        gv = by.setdefault(r["gid"], GroupView(r["gid"], r["gkind"], [], []))
        img = os.path.join(derived_dir, r["derived_path"]) if r["derived_path"] else r["orig_path"]
        gv.copies.append(Copy(r["id"], img, os.path.basename(r["rel_path"]), os.path.dirname(r["rel_path"]) or "(top)",
                              r["pixels"] or 0, r["size"] or 0, r["sharpness"] or 0.0, _key(r), r["decision"]))
        if r["is_kept"]:
            gv.pick.append(len(gv.copies) - 1)
    out = [g for g in by.values() if len(g.copies) >= 2 and (not undecided_only or not g.decided)]
    # Duplicates first (quick decisions), then bursts.
    return sorted(out, key=lambda g: (g.kind == "burst", g.group_id))


def count_undecided(cat: Catalog) -> int:
    return len(groups(cat, "", undecided_only=True))


def decide(cat: Catalog, keep: dict[str, bool]) -> int:
    """Save keep(True)/remove(False) for each content_key. One call = one batch for Ctrl+Z."""
    batch = int(time.time() * 1000)
    with cat.tx():
        for key, k in keep.items():
            prev = cat.scalar("SELECT decision FROM copy_decisions WHERE content_key=?", (key,))
            cat.db.execute("INSERT OR REPLACE INTO copy_decisions(content_key, decision, batch, prev, created_at) "
                           "VALUES (?,?,?,?,?)", (key, "keep" if k else "remove", batch, prev, time.time()))
    return batch


def undo_last(cat: Catalog) -> bool:
    batch = cat.scalar("SELECT MAX(batch) FROM copy_decisions")
    if batch is None:
        return False
    with cat.tx():
        for r in cat.q("SELECT content_key, prev FROM copy_decisions WHERE batch=?", (batch,)):
            if r["prev"]:
                cat.db.execute("UPDATE copy_decisions SET decision=?, prev=NULL, batch=0 WHERE content_key=?",
                               (r["prev"], r["content_key"]))
            else:
                cat.db.execute("DELETE FROM copy_decisions WHERE content_key=?", (r["content_key"],))
    return True


def removed_keys(cat: Catalog) -> set[str]:
    return {r["content_key"] for r in cat.q("SELECT content_key FROM copy_decisions WHERE decision='remove'")}
