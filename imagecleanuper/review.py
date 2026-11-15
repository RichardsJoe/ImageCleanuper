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
                                         "alone. Looks good trims it; Undo keeps the whole photo."),
    "rotation": ("Is this the right way up?", "The app thinks this photo may be sideways or upside down but "
                                              "wasn't sure. Looks good turns it as shown; Leave as is keeps it."),
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


def _question(r) -> str:
    if r["kind"] == "cutout" and (r["cut_conf"] or 1) < 0.9:
        return "cutout"
    if r["rotation_suggested"]:
        return "rotation"
    return "cutout" if r["kind"] == "cutout" else "trim"


def queue(cat: Catalog, derived_dir: str) -> list[Item]:
    """Items still waiting for an answer, in folder order."""
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
    return out


def count(cat: Catalog) -> int:
    return cat.scalar("SELECT COUNT(*) FROM fixes x JOIN assets a ON a.id = x.asset_id "
                      "JOIN active_files f ON f.id = a.file_id WHERE x.status = 'review'") or 0


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
    """Ctrl+Z: take back the most recent answer. Returns the asset id it applied to, or None."""
    r = cat.one("SELECT * FROM answers WHERE undone=0 ORDER BY id DESC LIMIT 1")
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
