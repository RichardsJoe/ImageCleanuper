"""Scan cut-outs: list them and export them with readable names, so they're easy to check."""

from __future__ import annotations

import os
import string
from dataclasses import dataclass

from .catalog import Catalog
from .fileops import atomic_copy
from .paths import long_path

EXPORT_FOLDER = "Scan Cut-outs"


@dataclass
class Scan:
    file_id: int
    orig_path: str
    rel_path: str
    source_label: str
    cutouts: list[tuple[int, int, str, bool]]   # (asset_id, index, derived file name, needs a check)


def letter(i: int) -> str:
    """0 → a, 25 → z, 26 → aa ..."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = string.ascii_lowercase[r] + s
    return s


def scans_with_cutouts(cat: Catalog) -> list[Scan]:
    rows = cat.q(
        """
        SELECT f.id AS file_id, f.orig_path, f.rel_path, s.label, a.id AS asset_id, a.cutout_index, a.derived_path,
               COALESCE(x.cut_conf, 1) AS cut_conf
        FROM assets a JOIN active_files f ON f.id = a.file_id JOIN sources s ON s.id = f.source_id
        LEFT JOIN fixes x ON x.asset_id = a.id
        WHERE a.kind = 'cutout' AND a.derived_path IS NOT NULL
        ORDER BY f.rel_path, a.cutout_index
        """)
    scans: dict[int, Scan] = {}
    for r in rows:
        sc = scans.setdefault(r["file_id"], Scan(r["file_id"], r["orig_path"], r["rel_path"], r["label"], []))
        sc.cutouts.append((r["asset_id"], r["cutout_index"], r["derived_path"], r["cut_conf"] < 0.9))
    return list(scans.values())


def export_cutouts(cat: Catalog, derived_dir: str, dest_root: str) -> tuple[int, str]:
    """Copy every cut-out to dest_root, mirroring the original folders:
    <dest_root>/<source>/<folders>/<scan name>_a.jpg, _b.jpg ... Returns (count, folder)."""
    n = 0
    for sc in scans_with_cutouts(cat):
        rel_dir, name = os.path.split(sc.rel_path)
        stem = os.path.splitext(name)[0]
        out_dir = os.path.join(dest_root, sc.source_label, rel_dir)
        for _aid, idx, derived, _check in sc.cutouts:
            dest = os.path.join(out_dir, f"{stem}_{letter(idx)}.jpg")
            atomic_copy(os.path.join(derived_dir, derived), dest, overwrite=True)
            n += 1
    os.makedirs(long_path(dest_root), exist_ok=True)
    return n, dest_root
