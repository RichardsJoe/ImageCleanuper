"""Your corrections to where a photo goes (Report M/Del, accuracy check, library edits).

Kept by content key (file fingerprint + cut-out position), so they survive re-analysis and always win
over the models (rule: locked answers win).
  holiday     value = holiday key, or "" for "not a holiday photo"
  year        value = "YYYY"
  not_person  value = person name (this person isn't in this photo)
  person      value = person name (this person is in this photo, even if no face was found)
  not_in      value = library folder ("2019/Other"): keep the photo out of that folder
  in          value = library folder: cancels an earlier not_in
"""

from __future__ import annotations

import time
from collections import defaultdict

from .catalog import Catalog


def add(cat: Catalog, content_key: str, kind: str, value: str, source: str) -> int:
    with cat.tx():
        return cat.db.execute("INSERT INTO photo_labels(content_key, kind, value, source, created_at) "
                              "VALUES (?,?,?,?,?)", (content_key, kind, value, source, time.time())).lastrowid


def undo(cat: Catalog, label_id: int) -> None:
    with cat.tx():
        cat.db.execute("UPDATE photo_labels SET undone=1 WHERE id=?", (label_id,))


def active(cat: Catalog) -> dict[str, dict]:
    """content_key → {"holiday": key, "year": "YYYY", "not_person": {name_lc: name}, "person": {...}}.
    For holiday/year the newest label wins; person labels add up (a later opposite label cancels)."""
    out: dict[str, dict] = defaultdict(dict)
    for r in cat.q("SELECT content_key, kind, value FROM photo_labels WHERE undone=0 ORDER BY id"):
        d = out[r["content_key"]]
        if r["kind"] in ("holiday", "year"):
            d[r["kind"]] = r["value"]
        elif r["kind"] in ("person", "not_person"):
            other = "not_person" if r["kind"] == "person" else "person"
            d.setdefault(other, {}).pop(r["value"].lower(), None)
            d.setdefault(r["kind"], {})[r["value"].lower()] = r["value"]   # lower-case name → name
        elif r["kind"] == "not_in":
            d.setdefault("not_in", set()).add(r["value"])
        elif r["kind"] == "in":
            d.setdefault("not_in", set()).discard(r["value"])
    return dict(out)


def asset_key(row) -> str:
    """Content key from a row with sha256, kind and cutout_index."""
    idx = row["cutout_index"]
    return f"{row['sha256']}:{row['kind']}:{'' if idx is None else idx}"
