"""Needs review: questions the app couldn't decide on its own (wireframe 09, pseudocode section 14).

Phase 2 questions:
  trim     "Did this fix look right?"          a border trim the app proposed but didn't apply
  cutout   "Was this scan cut out correctly?"  a cut-out the app wasn't sure about
Answers: looks_good (Enter) · undo (U) · rotate (R, turn 90° clockwise; stays on the item).
Every answer is saved immediately and can be taken back with Ctrl+Z (rule R9).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from PIL import Image

from .analyze import imageio
from .analyze.run import save_derived
from .catalog import Catalog
from .fileops import remove_quietly
from .paths import long_path

QUESTIONS = {
    "trim": ("Did this fix look right?", "The app found a plain border to trim but wasn't sure, so it left the photo "
                                         "alone. Accept change trims it; Undo keeps the whole photo."),
    "rotation": ("Is this the right way up?", "The app thinks this photo may be sideways or upside down but "
                                              "wasn't sure. Accept change turns it as shown; Leave as is keeps it."),
    "cutout": ("Was this scan cut out correctly?", "The app wasn't sure about this cut-out (two prints merged, "
                                                   "or one print cut in two). Undo keeps the whole scan instead."),
}


@dataclass
class Item:
    asset_id: int
    question: str           # trim | cutout
    title: str
    subtitle: str
    original: str           # the source file (before)
    derived: str | None     # current fixed image, if any (after)
    trim_box: tuple[int, int, int, int] | None
    kind: str               # whole | cutout
    file_id: int
    rotation: int
    suggested: int = 0      # suggested extra rotation (counter-clockwise degrees) for "rotation"
    check_key: str = ""     # for "content": pet | junk | cat:<key>


def _question(r) -> str:
    if r["kind"] == "cutout" and (r["cut_conf"] or 1) < 0.9:
        return "cutout"
    if r["rotation_suggested"]:
        return "rotation"
    return "cutout" if r["kind"] == "cutout" else "trim"


def _content_title(key: str, folder: str) -> tuple[str, str]:
    if key == "pet":
        return "Is this a pet?", "The image model thinks there may be a pet in this photo. Yes files it under Pets."
    if key == "junk":
        return "Is this junk?", ("It may be a screenshot, meme or accidental photo. Yes moves it to _Junk "
                                 "(nothing is deleted); No keeps it with your photos.")
    return f"Does this belong in {folder}?", f"The image model thinks this may be a {folder} photo. Yes adds it there."


def queue(cat: Catalog, derived_dir: str, categories: dict | None = None) -> list[Item]:
    """Items still waiting for an answer, in folder order: fixes first, then pets/categories/junk."""
    rows = cat.q(
        """
        SELECT a.id, a.kind, a.derived_path, a.file_id, f.orig_path, f.rel_path, x.trim_box, x.rotation,
               x.cut_conf, x.rotation_suggested
        FROM fixes x JOIN assets a ON a.id = x.asset_id JOIN active_files f ON f.id = a.file_id
        WHERE x.status = 'review'
        ORDER BY f.rel_path, a.cutout_index
        """)
    out = []
    for r in rows:
        q = _question(r)
        box = tuple(int(v) for v in r["trim_box"].split(",")) if (r["trim_box"] and q == "trim") else None
        title, sub = QUESTIONS[q]
        out.append(Item(r["id"], q, title, sub, r["orig_path"],
                        os.path.join(derived_dir, r["derived_path"]) if r["derived_path"] else None,
                        box, r["kind"], r["file_id"], r["rotation"] or 0, r["rotation_suggested"] or 0))
    for r in cat.q("""SELECT p.asset_id, p.check_key, a.kind, a.derived_path, a.file_id, f.orig_path
                      FROM predictions p JOIN assets a ON a.id = p.asset_id JOIN active_files f ON f.id = a.file_id
                      WHERE p.status = 'review' ORDER BY p.check_key, f.rel_path, a.cutout_index"""):
        key = r["check_key"]
        folder = (categories or {}).get(key.split(":", 1)[-1], (key.split(":", 1)[-1].title(), ""))[0]
        title, sub = _content_title(key, folder)
        out.append(Item(r["asset_id"], "content", title, sub, r["orig_path"],
                        os.path.join(derived_dir, r["derived_path"]) if r["derived_path"] else None,
                        None, r["kind"], r["file_id"], 0, 0, key))
    return out


def count(cat: Catalog) -> int:
    fixes = cat.scalar("SELECT COUNT(*) FROM fixes x JOIN assets a ON a.id = x.asset_id "
                       "JOIN active_files f ON f.id = a.file_id WHERE x.status = 'review'") or 0
    skipped = skipped_checks(cat)
    content = sum(n for k, n in cat.q("SELECT p.check_key, COUNT(*) FROM predictions p JOIN assets a "
                                      "ON a.id = p.asset_id JOIN active_files f ON f.id = a.file_id "
                                      "WHERE p.status = 'review' GROUP BY p.check_key") if k not in skipped)
    return fixes + content


# ---- yes/no questions as a grid (wireframe 09b): many photos per screen -------------------------
GRID_PAGE = 15


def _last_run(cat: Catalog) -> str:
    return str(cat.scalar("SELECT MAX(id) FROM runs") or 0)


def skipped_checks(cat: Catalog) -> set[str]:
    """Questions you chose to leave until after the next run ("Skip the rest of …")."""
    run = _last_run(cat)
    return {r[0][len("skip_check:"):] for r in cat.q("SELECT key, value FROM app_state WHERE key LIKE 'skip_check:%'")
            if r[1] == run}


def skip_check(cat: Catalog, check_key: str) -> None:
    with cat.tx():
        cat.db.execute("INSERT OR REPLACE INTO app_state(key, value) VALUES (?,?)",
                       (f"skip_check:{check_key}", _last_run(cat)))


@dataclass
class GridPage:
    check: str
    title: str
    subtitle: str
    items: list           # [(asset_id, image path)]
    page: int
    pages: int
    total: int


def grid_page(cat: Catalog, derived_dir: str, categories: dict | None = None) -> GridPage | None:
    """The next page of photos for the first unsure check, most uncertain first (they teach the most)."""
    skipped = skipped_checks(cat)
    mid = 0.475
    rows = cat.q("""SELECT p.asset_id, p.check_key, p.prob, a.derived_path, f.orig_path
                    FROM predictions p JOIN assets a ON a.id = p.asset_id JOIN active_files f ON f.id = a.file_id
                    WHERE p.status = 'review' ORDER BY p.check_key""")
    by: dict[str, list] = {}
    for r in rows:
        if r["check_key"] not in skipped:
            by.setdefault(r["check_key"], []).append(r)
    if not by:
        return None
    order = sorted(by, key=lambda k: (0 if k == "pet" else 1 if k.startswith("cat:") else 2, k))
    check = order[0]
    items = sorted(by[check], key=lambda r: (abs(r["prob"] - mid), r["asset_id"]))
    total = len(items)
    page_items = [(r["asset_id"], os.path.join(derived_dir, r["derived_path"]) if r["derived_path"] else r["orig_path"])
                  for r in items[:GRID_PAGE]]
    folder = (categories or {}).get(check.split(":", 1)[-1], (check.split(":", 1)[-1].title(), ""))[0]
    if check == "pet":
        title, sub = "Which of these have a pet in them?", "Picked ones go in Pets."
    elif check == "junk":
        title, sub = "Which of these are junk?", "Screenshots, memes, accidental shots. Picked ones go to _Junk (nothing is deleted)."
    else:
        title, sub = f"Which of these belong in {folder}?", f"Picked ones go in {folder}."
    pages = (total + GRID_PAGE - 1) // GRID_PAGE
    return GridPage(check, title, sub, page_items, 1, pages, total)


def answer_page(cat: Catalog, check: str, yes: list[int], no: list[int]) -> list[int]:
    """Save a whole page at once. Returns the answer ids (for Ctrl+Z)."""
    from . import content
    return [content.answer(cat, aid, check, True) for aid in yes] + \
           [content.answer(cat, aid, check, False) for aid in no]


def undo_page(cat: Catalog, answer_ids: list[int]) -> None:
    with cat.tx():
        for aid in answer_ids:
            r = cat.one("SELECT asset_id, check_key FROM content_answers WHERE id=?", (aid,))
            cat.db.execute("UPDATE content_answers SET undone=1 WHERE id=?", (aid,))
            if r and r["asset_id"]:
                cat.db.execute("INSERT OR REPLACE INTO predictions(asset_id, check_key, prob, status) "
                               "VALUES (?,?,0.5,'review')", (r["asset_id"], r["check_key"]))


# ---- rendering a whole photo from its original + current fixes ----------------------
def render_whole(cat: Catalog, asset_id: int, derived_dir: str) -> str | None:
    """(Re)build the fixed version of a whole photo from the untouched original:
    camera rotation → extra rotation → trim (if applied). Returns the derived file name or None."""
    r = cat.one("SELECT a.file_id, f.orig_path, x.exif_rotation, x.rotation, x.trim_box, x.trim_applied "
                "FROM assets a JOIN files f ON f.id = a.file_id JOIN fixes x ON x.asset_id = a.id WHERE a.id=?",
                (asset_id,))
    name = f"{asset_id}.jpg"
    dest = os.path.join(derived_dir, name)
    img, info = imageio.open_image(r["orig_path"])
    try:
        up, exif_rot = imageio.upright(img, info.orientation)
        changed = bool(exif_rot)
        if r["trim_applied"] and r["trim_box"]:
            up = up.crop(tuple(int(v) for v in r["trim_box"].split(",")))  # box is in upright coordinates
            changed = True
        if r["rotation"]:
            up = up.rotate(r["rotation"], expand=True)
            changed = True
        if not changed:
            remove_quietly(dest)
            cat.db.execute("UPDATE assets SET derived_path=NULL, width=?, height=? WHERE id=?",
                           (up.width, up.height, asset_id))
            return None
        save_derived(up, info, dest)
        cat.db.execute("UPDATE assets SET derived_path=?, width=?, height=? WHERE id=?",
                       (name, up.width, up.height, asset_id))
        return name
    finally:
        img.close()


def _rotate_file(path: str, ccw_degrees: int) -> tuple[int, int]:
    """Turn a saved cut-out in place (counter-clockwise degrees, like PIL)."""
    img = Image.open(long_path(path))
    img.load()
    out = img.rotate(ccw_degrees % 360, expand=True)
    exif = img.getexif()
    from .fileops import atomic_write
    atomic_write(path, lambda f: out.save(f, "JPEG", quality=95, exif=exif.tobytes(), subsampling=0),
                 overwrite=True)
    return out.size


# ---- answers ------------------------------------------------------------------------
def _state(cat: Catalog, asset_id: int) -> dict:
    r = cat.one("SELECT x.status, x.rotation, x.trim_applied, a.derived_path FROM fixes x "
                "JOIN assets a ON a.id = x.asset_id WHERE x.asset_id=?", (asset_id,))
    return dict(r)


def answer(cat: Catalog, item: Item, value: str, derived_dir: str) -> int:
    """Apply an answer and save it. Returns the answer id."""
    assert value in ("looks_good", "undo", "rotate"), value
    if item.question == "content":   # Yes / No
        from . import content
        return content.answer(cat, item.asset_id, item.check_key, value == "looks_good")
    prev = _state(cat, item.asset_id)
    with cat.tx():
        if value == "rotate":
            rot = (prev["rotation"] - 90) % 360   # stored counter-clockwise, like PIL
            cat.db.execute("UPDATE fixes SET rotation=? WHERE asset_id=?", (rot, item.asset_id))
            if item.kind == "whole":
                render_whole(cat, item.asset_id, derived_dir)
            else:
                w, h = _rotate_file(os.path.join(derived_dir, prev["derived_path"]), -90)
                cat.db.execute("UPDATE assets SET width=?, height=? WHERE id=?", (w, h, item.asset_id))
        elif item.question == "rotation":
            if value == "looks_good":
                rot = (prev["rotation"] + item.suggested) % 360
                cat.db.execute("UPDATE fixes SET rotation=?, status='accepted' WHERE asset_id=?", (rot, item.asset_id))
                if item.kind == "whole":
                    render_whole(cat, item.asset_id, derived_dir)
                else:
                    w, h = _rotate_file(os.path.join(derived_dir, prev["derived_path"]), item.suggested)
                    cat.db.execute("UPDATE assets SET width=?, height=? WHERE id=?", (w, h, item.asset_id))
            else:
                cat.db.execute("UPDATE fixes SET status='undone' WHERE asset_id=?", (item.asset_id,))
        elif item.question == "trim":
            applied = 1 if value == "looks_good" else 0
            cat.db.execute("UPDATE fixes SET trim_applied=?, status=? WHERE asset_id=?",
                           (applied, "accepted" if value == "looks_good" else "undone", item.asset_id))
            render_whole(cat, item.asset_id, derived_dir)
        else:  # cutout
            cat.db.execute("UPDATE fixes SET status=? WHERE asset_id=?",
                           ("accepted" if value == "looks_good" else "undone", item.asset_id))
        cur = cat.db.execute("INSERT INTO answers(asset_id, question, value, prev, created_at) VALUES (?,?,?,?,?)",
                             (item.asset_id, item.question, value, json.dumps(prev), time.time()))
    return cur.lastrowid


def undo_last(cat: Catalog, derived_dir: str) -> int | None:
    """Ctrl+Z: take back the most recent answer (fix or pet/category). Returns the asset id, or None."""
    r = cat.one("SELECT * FROM answers WHERE undone=0 ORDER BY id DESC LIMIT 1")
    c = cat.one("SELECT * FROM content_answers WHERE undone=0 ORDER BY id DESC LIMIT 1")
    if c and (not r or c["created_at"] >= r["created_at"]):
        from . import content
        content.undo_last(cat)
        return c["asset_id"]
    if not r:
        return None
    prev = json.loads(r["prev"])
    kind = cat.scalar("SELECT kind FROM assets WHERE id=?", (r["asset_id"],))
    with cat.tx():
        current = cat.scalar("SELECT rotation FROM fixes WHERE asset_id=?", (r["asset_id"],)) or 0
        delta = (current - prev["rotation"]) % 360
        if delta and kind == "cutout":   # cut-outs were turned in place: turn them back
            d = cat.scalar("SELECT derived_path FROM assets WHERE id=?", (r["asset_id"],))
            w, h = _rotate_file(os.path.join(derived_dir, d), -delta)
            cat.db.execute("UPDATE assets SET width=?, height=? WHERE id=?", (w, h, r["asset_id"]))
        cat.db.execute("UPDATE fixes SET status=?, rotation=?, trim_applied=? WHERE asset_id=?",
                       (prev["status"], prev["rotation"], prev["trim_applied"], r["asset_id"]))
        if kind == "whole":
            render_whole(cat, r["asset_id"], derived_dir)
        cat.db.execute("UPDATE answers SET undone=1 WHERE id=?", (r["id"],))
    return r["asset_id"]
