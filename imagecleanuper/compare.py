"""Exact and near duplicates across the whole collection (pseudocode section 9)."""

from __future__ import annotations

import numpy as np

from .catalog import Catalog

# Strict on purpose: the same photo resized or re-saved passes; burst shots and edits should not.
NEAR_PHASH = 4
NEAR_DHASH = 6
ASPECT_TOLERANCE = 0.03


class _UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _popcount64(x: np.ndarray) -> np.ndarray:
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x)
    b = x.view(np.uint8).reshape(-1, 8)  # pragma: no cover - numpy < 2
    return np.unpackbits(b, axis=1).sum(axis=1)


def compare_all(cat: Catalog) -> dict:
    rows = cat.q(
        """
        SELECT a.id, a.kind, a.cutout_index, a.width, a.height, f.sha256, f.size, f.orig_path,
               h.phash, h.dhash, h.pixels, h.clip, h.sharpness, m.exif_date, m.exif_date_raw, m.camera_make,
               m.camera_model
        FROM assets a JOIN active_files f ON f.id = a.file_id
        LEFT JOIN hashes h ON h.asset_id = a.id LEFT JOIN meta m ON m.asset_id = a.id
        WHERE f.status = 'analyzed' AND f.kind = 'photo'
        ORDER BY a.id
        """)
    uf = _UnionFind()
    info = {r["id"]: r for r in rows}
    # Exact: identical files (same fingerprint), same cut-out position.
    by_content: dict[tuple, list[int]] = {}
    for r in rows:
        by_content.setdefault((r["sha256"], r["kind"], r["cutout_index"]), []).append(r["id"])
    for ids in by_content.values():
        for other in ids[1:]:
            uf.union(ids[0], other)
    # Near: same picture resized / re-saved (vectorised so thousands of photos stay fast).
    hashed = [r for r in rows if r["phash"] and r["dhash"]]
    if len(hashed) > 1:
        ids = np.array([r["id"] for r in hashed])
        ph = np.array([int(r["phash"], 16) for r in hashed], dtype=np.uint64)
        dh = np.array([int(r["dhash"], 16) for r in hashed], dtype=np.uint64)
        aspect = np.array([(r["width"] or 1) / (r["height"] or 1) for r in hashed])
        for i in range(len(hashed) - 1):
            pd = _popcount64(ph[i + 1:] ^ ph[i])
            dd = _popcount64(dh[i + 1:] ^ dh[i])
            ar = np.abs(aspect[i + 1:] - aspect[i]) / aspect[i]
            for j in np.nonzero((pd <= NEAR_PHASH) & (dd <= NEAR_DHASH) & (ar <= ASPECT_TOLERANCE))[0]:
                uf.union(int(ids[i]), int(ids[i + 1 + j]))
    groups: dict[int, list[int]] = {}
    for r in rows:
        groups.setdefault(uf.find(r["id"]), []).append(r["id"])
    counts = {"exact_groups": 0, "near_groups": 0, "extra_copies": 0}
    with cat.tx():
        cat.db.execute("DELETE FROM dup_members")
        cat.db.execute("DELETE FROM dup_groups")
        for members in groups.values():
            if len(members) < 2:
                continue
            exact = len({info[m]["sha256"] for m in members}) == 1
            kind = "exact" if exact else "near"
            # Different capture times (same camera, seconds apart) = burst shots, not copies:
            # a re-saved copy keeps the original's time or loses it, it never gets a new one.
            times = {info[m]["exif_date_raw"] for m in members if info[m]["exif_date_raw"]}
            cams = {(info[m]["camera_make"], info[m]["camera_model"]) for m in members if info[m]["exif_date_raw"]}
            if not exact and len(times) >= 2 and len(cams) == 1:
                kind = "burst"
            gid = cat.db.execute("INSERT INTO dup_groups(kind) VALUES (?)", (kind,)).lastrowid
            kept = (max(members, key=lambda m: info[m]["sharpness"] or 0) if kind == "burst"
                    else choose_best([info[m] for m in members]))
            for m in members:
                cat.db.execute("INSERT INTO dup_members(group_id, asset_id, is_kept) VALUES (?,?,?)",
                               (gid, m, int(m == kept)))
            if kind == "burst":
                counts["burst_groups"] = counts.get("burst_groups", 0) + 1
                continue
            counts["exact_groups" if exact else "near_groups"] += 1
            counts["extra_copies"] += len(members) - 1
        # Burst shots: photos not already in a duplicate group, taken seconds apart.
        from .copies import find_bursts
        taken = {m for members in groups.values() if len(members) >= 2 for m in members}
        counts.setdefault("burst_groups", 0)
        for members in find_bursts(rows, taken):
            gid = cat.db.execute("INSERT INTO dup_groups(kind) VALUES ('burst')").lastrowid
            sharpest = max(members, key=lambda m: info[m]["sharpness"] or 0)
            for m in members:
                cat.db.execute("INSERT INTO dup_members(group_id, asset_id, is_kept) VALUES (?,?,?)",
                               (gid, m, int(m == sharpest)))
            counts["burst_groups"] += 1
    return counts


def choose_best(members: list) -> int:
    """Deterministic (rule R11): most pixels, then has a camera date, then bigger file, then path."""
    best = sorted(members, key=lambda r: (-(r["pixels"] or 0), 0 if r["exif_date"] else 1,
                                          -(r["size"] or 0), r["orig_path"].lower(), r["id"]))
    return best[0]["id"]
