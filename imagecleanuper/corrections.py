"""Corrections from the Report (M move, Del remove) and from files you moved in the library.

Each correction is saved as an answer that wins over the models and teaches them:
  People/<Name>   remove → "not this person"         add → "this person is in it"
  Holidays/<H>    remove → "not a holiday photo"     add → "this holiday"
  Pets / <Cat>    remove → No for that check         add → Yes for that check
  _Junk           remove → "not junk"                add → "junk"
  <year>/...      add into another year → that year
  anything else   remove → keep it out of that folder (nothing is deleted)
Every function returns undo tokens for Ctrl+Z.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import labels as labels_mod
from .catalog import Catalog
from .dates import HOLIDAY_NAMES
from .settings import Settings

_YEAR = re.compile(r"^(\d{4}|Unknown Date|Unknown Year)$")


@dataclass
class Folder:
    kind: str            # person | holiday | check | junk | other | duplicates | special
    year: str | None = None
    value: str | None = None     # person name, holiday key, check key
    old: bool = False            # under Old Family Photos


def parse(folder: str, s: Settings) -> Folder:
    parts = folder.split("/")
    old = parts[0] == "Old Family Photos"
    if old:
        parts = parts[1:]
    if not parts or not parts[0]:
        return Folder("special", old=old)
    if parts[0] == "_Junk":
        return Folder("junk")
    if parts[0] == "_Duplicates":
        return Folder("duplicates")
    if parts[0].startswith("_") or not _YEAR.match(parts[0]):
        return Folder("special", old=old)
    year = parts[0] if parts[0][:4].isdigit() else None
    rest = parts[1:]
    if len(rest) >= 2 and rest[0] == "People":
        return Folder("person", year, rest[1], old)
    if len(rest) >= 2 and rest[0] == "Holidays":
        name = rest[1]
        key = next((k for k, v in HOLIDAY_NAMES.items() if v == name), "event:" + name)
        return Folder("holiday", year, key, old)
    if len(rest) == 1 and rest[0] == "Pets":
        return Folder("check", year, "pet", old)
    if len(rest) == 1:
        for key, (label, *_rest) in s.categories.items():
            if rest[0] == label:
                return Folder("check", year, f"cat:{key}", old)
    return Folder("other", year, None, old)


def _label(cat: Catalog, ck: str, kind: str, value: str, source: str) -> tuple:
    return ("label", labels_mod.add(cat, ck, kind, value, source))


def _content(cat: Catalog, asset_id: int, ck: str, check: str, value: bool) -> tuple:
    pred = cat.one("SELECT prob, status FROM predictions WHERE asset_id=? AND check_key=?", (asset_id, check))
    with cat.tx():
        caid = cat.db.execute("INSERT INTO content_answers(asset_id, content_key, check_key, value, best, created_at)"
                              " VALUES (?,?,?,?,0,?)", (asset_id, ck, check, int(value), time.time())).lastrowid
        if value:
            cat.db.execute("INSERT OR REPLACE INTO predictions(asset_id, check_key, prob, status) "
                           "VALUES (?,?,1.0,'yes')", (asset_id, check))
        else:
            cat.db.execute("DELETE FROM predictions WHERE asset_id=? AND check_key=?", (asset_id, check))
    return ("content", caid, asset_id, check, tuple(pred) if pred else None)


def remove_from(cat: Catalog, s: Settings, asset_id: int, ck: str, folder: str, source: str = "report") -> list:
    """Take a photo out of one folder of the plan."""
    f = parse(folder, s)
    if f.kind == "person":
        return [_label(cat, ck, "not_person", f.value, source)]
    if f.kind == "holiday":
        return [_label(cat, ck, "holiday", "", source)]
    if f.kind == "check":
        return [_content(cat, asset_id, ck, f.value, False)]
    if f.kind == "junk":
        return [_content(cat, asset_id, ck, "junk", False)]
    if f.kind == "duplicates":
        return []                     # decided on the Duplicates screen instead
    return [_label(cat, ck, "not_in", folder, source)]


def add_to(cat: Catalog, s: Settings, asset_id: int, ck: str, folder: str, current_year: str | None,
           source: str = "report") -> list:
    """Put a photo into a folder of the plan."""
    f = parse(folder, s)
    tokens = [_label(cat, ck, "in", folder, source)]      # undo any earlier "keep out"
    if f.year and f.year != current_year:
        tokens.append(_label(cat, ck, "year", f.year, source))
    if f.kind == "person":
        tokens.append(_label(cat, ck, "person", f.value, source))
    elif f.kind == "holiday":
        tokens.append(_label(cat, ck, "holiday", f.value, source))
    elif f.kind == "check":
        tokens.append(_content(cat, asset_id, ck, f.value, True))
    elif f.kind == "junk":
        tokens.append(_content(cat, asset_id, ck, "junk", True))
    return tokens


def move(cat: Catalog, s: Settings, asset_id: int, ck: str, src: str, dest: str, current_year: str | None,
         source: str = "report") -> list:
    tokens = []
    if parse(src, s).kind not in ("other", "special") or parse(src, s).year == parse(dest, s).year:
        tokens += remove_from(cat, s, asset_id, ck, src, source)
    return tokens + add_to(cat, s, asset_id, ck, dest, current_year, source)


def undo(cat: Catalog, tokens: list) -> None:
    with cat.tx():
        for t in reversed(tokens):
            if t[0] == "label":
                cat.db.execute("UPDATE photo_labels SET undone=1 WHERE id=?", (t[1],))
            elif t[0] == "content":
                _, caid, asset_id, check, pred = t
                cat.db.execute("UPDATE content_answers SET undone=1 WHERE id=?", (caid,))
                if pred:
                    cat.db.execute("INSERT OR REPLACE INTO predictions(asset_id, check_key, prob, status) "
                                   "VALUES (?,?,?,?)", (asset_id, check, pred[0], pred[1]))
                else:
                    cat.db.execute("DELETE FROM predictions WHERE asset_id=? AND check_key=?", (asset_id, check))


def asset_of(cat: Catalog, entry_key: str) -> tuple[int, str, str | None] | None:
    """(asset id, content key, year) for a Report entry ("a<id>"), or None for whole files."""
    if not entry_key.startswith("a"):
        return None
    r = cat.one("SELECT a.id, a.kind, a.cutout_index, f.sha256, d.date FROM assets a JOIN files f ON f.id=a.file_id "
                "LEFT JOIN dates d ON d.asset_id=a.id WHERE a.id=?", (int(entry_key[1:]),))
    if not r:
        return None
    return r["id"], labels_mod.asset_key(r), (r["date"] or "")[:4] or None
