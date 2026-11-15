"""Where every photo would go (pseudocode section 16). Phase 2: a read-only preview for the Report.

Implemented now: year (or Unknown Date / Unknown Year), Holidays, Videos, Old Family Photos,
_Duplicates, _Could Not Open, Other. People, Pets and categories are added in phase 3.
Nothing is copied or moved until phase 4 (Approve and sort).
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

from .catalog import Catalog
from .dates import HOLIDAY_NAMES


@dataclass
class Entry:
    """One photo in the preview. image = what to show as a thumbnail."""
    key: str               # "a<asset id>" or "f<file id>"
    name: str
    image: str | None      # derived image, or the original file
    original: str
    is_guess: bool = False


@dataclass
class Preview:
    folders: dict[str, list[Entry]] = field(default_factory=dict)   # "2019/Holidays/Christmas" -> entries
    totals: dict[str, int] = field(default_factory=dict)

    def tree(self) -> dict:
        """Nested dict of folder names → {"_count": n, children...} for the folder tree."""
        root: dict = {}
        for path, entries in self.folders.items():
            node = root
            for part in path.split("/"):
                node = node.setdefault(part, {"_count": 0})
                node["_count"] += len(entries)
        return root


def _sort_key(path: str):
    head = path.split("/")[0]
    special = head.startswith("_")
    order = 0 if head[:4].isdigit() else (1 if head == "Unknown Date" else (2 if head == "Old Family Photos" else 3))
    return (special, order, path)


def preview(cat: Catalog, derived_dir: str) -> Preview:
    rows = cat.q(
        """
        SELECT a.id, a.kind, a.cutout_index, a.derived_path, f.id AS file_id, f.orig_path, f.rel_path, f.kind AS fkind,
               d.date, d.year_only, d.is_guess, h.holiday, m.is_scan, x.status AS fix_status,
               (SELECT is_kept FROM dup_members dm WHERE dm.asset_id = a.id) AS dup_kept
        FROM assets a JOIN active_files f ON f.id = a.file_id
        LEFT JOIN dates d ON d.asset_id = a.id LEFT JOIN holidays h ON h.asset_id = a.id
        LEFT JOIN meta m ON m.asset_id = a.id LEFT JOIN fixes x ON x.asset_id = a.id
        WHERE f.status = 'analyzed'
        ORDER BY f.rel_path, a.cutout_index
        """)
    folders: dict[str, list[Entry]] = defaultdict(list)
    totals = defaultdict(int)
    cut_wrong = {r["file_id"] for r in rows if r["kind"] == "cutout" and r["fix_status"] == "undone"}
    whole_scan_done: set[int] = set()
    for r in rows:
        if r["kind"] == "cutout" and r["file_id"] in cut_wrong:
            # A cut-out was marked wrong: keep the whole scan as one photo instead.
            if r["file_id"] in whole_scan_done:
                continue
            whole_scan_done.add(r["file_id"])
            entry = Entry(f"f{r['file_id']}", os.path.basename(r["rel_path"]), r["orig_path"], r["orig_path"])
        else:
            suffix = f" ({chr(97 + r['cutout_index'])})" if r["kind"] == "cutout" else ""
            image = os.path.join(derived_dir, r["derived_path"]) if r["derived_path"] else r["orig_path"]
            entry = Entry(f"a{r['id']}", os.path.basename(r["rel_path"]) + suffix, image, r["orig_path"],
                          bool(r["is_guess"]))
        if r["dup_kept"] == 0:
            folders["_Duplicates"].append(entry)
            totals["duplicates"] += 1
            continue
        root = "Old Family Photos/" if r["is_scan"] else ""
        year = r["date"][:4] if r["date"] else ("Unknown Year" if r["is_scan"] else "Unknown Date")
        dests = []
        if r["fkind"] == "video":
            dests.append(f"{root}{year}/Videos")
        else:
            if r["holiday"]:
                name = HOLIDAY_NAMES.get(r["holiday"], r["holiday"].split(":", 1)[-1])
                dests.append(f"{root}{year}/Holidays/{name}")
            if not dests:
                dests.append(f"{root}{year}/Other")
        for d in dests:
            folders[d].append(entry)
        totals["sorted"] += 1
    for r in cat.q("SELECT id, orig_path, rel_path FROM active_files WHERE status='could_not_open' ORDER BY rel_path"):
        folders["_Could Not Open"].append(Entry(f"f{r['id']}", os.path.basename(r["rel_path"]), None, r["orig_path"]))
        totals["could_not_open"] += 1
    totals["need_review"] = cat.scalar(
        "SELECT COUNT(*) FROM fixes x JOIN assets a ON a.id=x.asset_id JOIN active_files f ON f.id=a.file_id "
        "WHERE x.status='review'") or 0
    totals["guesses"] = cat.scalar(
        "SELECT COUNT(*) FROM dates d JOIN assets a ON a.id=d.asset_id JOIN active_files f ON f.id=a.file_id "
        "WHERE d.is_guess=1") or 0
    ordered = {k: folders[k] for k in sorted(folders, key=_sort_key)}
    return Preview(ordered, dict(totals))
