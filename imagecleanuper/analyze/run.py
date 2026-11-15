"""Analyze each file once (section 8). Results are committed per file (rule R8)."""

from __future__ import annotations

import os
import time
from typing import Callable

from PIL import Image

from .. import log
from ..catalog import Catalog
from ..fileops import atomic_write, remove_quietly
from ..paths import long_path
from . import fix, hashes, imageio, scans

Progress = Callable[[str, int, int], None]
_log = log.get("analyze")

TAG_ORIENTATION = 0x0112
# Bump when analysis rules change so earlier results are redone on the next run.
# 1 = 0.2.0-0.2.2, 2 = close prints separated (0.2.3), 3 = prints touching the scan edges (0.2.4),
# 4 = lid found by edges + texture: light walls, Polaroid frames, tidy grids (0.2.5),
# 5 = single prints cut out; blank cards ignored (0.2.6),
# 6 = proposed trims kept for review (0.3.0),
# 7 = upside-down / sideways prints turned upright using faces (0.3.1),
# 8 = single prints in a corner are scans; no trims that leave empty page; Scan_ names (0.3.2).
ANALYSIS_VERSION = 8


def pending(cat: Catalog) -> list:
    """Files that are backed up (verified) but not analyzed yet."""
    return cat.q(
        """
        SELECT f.* FROM active_files f
        WHERE (f.status = 'new' OR (f.status IN ('analyzed', 'could_not_open') AND f.analysis_version < ?))
          AND f.busy = 0
          AND f.sha256 IN (SELECT e.sha256 FROM backup_entries e
                           JOIN backup_parts p ON p.id = e.part_id WHERE p.verified = 1)
        ORDER BY f.id
        """, (ANALYSIS_VERSION,))


def analyze_pending(cat: Catalog, derived_dir: str, progress: Progress | None = None) -> dict:
    rows = pending(cat)
    counts = {"analyzed": 0, "could_not_open": 0, "cutouts": 0, "reused": 0}
    for i, f in enumerate(rows, 1):
        if progress:
            progress("Looking at photos", i, len(rows), f["orig_path"])
        try:
            reused = _reuse_exact_copy(cat, f, derived_dir)
            if reused:
                counts["reused"] += 1
            else:
                counts["cutouts"] += analyze_file(cat, f, derived_dir)
            cat.db.execute("UPDATE files SET status='analyzed', open_error=NULL, analysis_version=?, updated_at=? "
                           "WHERE id=?", (ANALYSIS_VERSION, time.time(), f["id"]))
            counts["analyzed"] += 1
        except imageio.DecodeError as e:
            _clear_assets(cat, f["id"], derived_dir)
            cat.db.execute("UPDATE files SET status='could_not_open', open_error=?, analysis_version=?, updated_at=? "
                           "WHERE id=?", (str(e), ANALYSIS_VERSION, time.time(), f["id"]))
            counts["could_not_open"] += 1
        except Exception as e:  # one strange file must never stop the run
            _log.exception("analysis failed for %s", f["orig_path"])
            _clear_assets(cat, f["id"], derived_dir)
            cat.db.execute("UPDATE files SET status='could_not_open', open_error=?, analysis_version=?, updated_at=? "
                           "WHERE id=?", (f"The app hit a problem reading this file ({type(e).__name__}).",
                                          ANALYSIS_VERSION, time.time(), f["id"]))
            counts["could_not_open"] += 1
        cat.db.commit()  # R8: a crash redoes at most this one file
    return counts


def _clear_assets(cat: Catalog, file_id: int, derived_dir: str) -> None:
    """Remove earlier results for a file (it changed, or analysis is being redone)."""
    ids = [r["id"] for r in cat.q("SELECT id FROM assets WHERE file_id=?", (file_id,))]
    for aid in ids:
        p = cat.scalar("SELECT derived_path FROM assets WHERE id=?", (aid,))
        shared = p and cat.scalar("SELECT COUNT(*) FROM assets WHERE derived_path=? AND id<>?", (p, aid))
        if p and derived_dir and not shared:
            remove_quietly(os.path.join(derived_dir, p))
        for t in ("fixes", "meta", "hashes", "dates", "holidays", "dup_members"):
            cat.db.execute(f"DELETE FROM {t} WHERE asset_id=?", (aid,))
        cat.db.execute("DELETE FROM assets WHERE id=?", (aid,))


def _reuse_exact_copy(cat: Catalog, f, derived_dir: str) -> bool:
    """An identical file (same fingerprint) was already analyzed: copy its results instead of
    redoing the work. Dates from the file name are recomputed for this path."""
    src = cat.one("SELECT id FROM files WHERE sha256=? AND status='analyzed' AND analysis_version=? AND id<>? "
                  "ORDER BY id LIMIT 1", (f["sha256"], ANALYSIS_VERSION, f["id"]))
    if not src:
        return False
    _clear_assets(cat, f["id"], derived_dir)
    now = time.time()
    for a in cat.q("SELECT * FROM assets WHERE file_id=? ORDER BY id", (src["id"],)):
        cur = cat.db.execute(
            "INSERT INTO assets(file_id, kind, cutout_index, derived_path, width, height, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (f["id"], a["kind"], a["cutout_index"], a["derived_path"], a["width"], a["height"], now))
        new_id = cur.lastrowid
        for t in ("fixes", "meta", "hashes"):
            row = cat.one(f"SELECT * FROM {t} WHERE asset_id=?", (a["id"],))
            if row:
                cols = [k for k in row.keys() if k != "asset_id"]
                cat.db.execute(f"INSERT INTO {t}(asset_id, {', '.join(cols)}) VALUES (?{', ?' * len(cols)})",
                               (new_id, *[row[k] for k in cols]))
        name_date = imageio.parse_date_from_filename(f["rel_path"])
        cat.db.execute("UPDATE meta SET filename_date=? WHERE asset_id=?", (name_date, new_id))
    return True


def _new_asset(cat: Catalog, file_id: int, kind: str, index: int | None, w: int, h: int) -> int:
    cur = cat.db.execute(
        "INSERT INTO assets(file_id, kind, cutout_index, width, height, created_at) VALUES (?,?,?,?,?,?)",
        (file_id, kind, index, w, h, time.time()))
    return cur.lastrowid


def analyze_file(cat: Catalog, f, derived_dir: str) -> int:
    """Analyze one file. Returns the number of cut-outs made (0 for a normal photo)."""
    _clear_assets(cat, f["id"], derived_dir)
    name_date = imageio.parse_date_from_filename(f["rel_path"])
    if f["kind"] == "video":
        aid = _new_asset(cat, f["id"], "whole", None, 0, 0)
        cat.db.execute("INSERT INTO meta(asset_id, exif_date, filename_date) VALUES (?,?,?)",
                       (aid, imageio.read_video_date(f["orig_path"]), name_date))
        return 0
    img, info = imageio.open_image(f["orig_path"])
    try:
        upright, exif_rot = imageio.upright(img, info.orientation)
        scan = scans.detect_scan(upright, info)
        named_scan = os.path.basename(f["rel_path"]).lower().startswith("scan")
        if named_scan and not scan.is_scan and scans.find_photos_on_scan(upright):
            scan = scans.ScanCheck(True, 0.9, "file name says it's a scan, and prints were found on it")
        if scan.is_scan and named_scan:
            name_date = None   # "Scan_20170326…" is the day it was scanned, not when the photo was taken
        boxes = scans.find_photos_on_scan(upright) if scan.is_scan else []
        # One print or several: each is cut out. (find_photos_on_scan returns nothing when a
        # single print fills the page, which then goes through the normal border trim.)
        if len(boxes) >= 1:
            for i, box in enumerate(boxes):
                piece = scans.straighten_and_crop(upright, box)
                aid = _new_asset(cat, f["id"], "cutout", i, piece.width, piece.height)
                _process(cat, aid, piece, info, exif_rot, scan, name_date, derived_dir,
                         force_derived=True, cut_conf=box.confidence, check_upright=True)
            return len(boxes)
        aid = _new_asset(cat, f["id"], "whole", None, upright.width, upright.height)
        # Only scans can be the wrong way up: cameras and phones record which way is up, and
        # Messenger/WhatsApp copies are turned upright before they're sent.
        _process(cat, aid, upright, info, exif_rot, scan, name_date, derived_dir, check_upright=scan.is_scan)
        return 0
    finally:
        img.close()


def _process(cat: Catalog, aid: int, img: Image.Image, info: imageio.ImageInfo, exif_rot: int,
             scan: scans.ScanCheck, name_date: str | None, derived_dir: str,
             force_derived: bool = False, cut_conf: float | None = None, check_upright: bool = False) -> None:
    rot, rot_conf = fix.predict_sideways(img) if check_upright else (0, 0.0)
    suggested = 0
    status = "none"
    if rot and rot_conf >= fix.AUTO_ROTATE:
        img = img.rotate(rot, expand=True)
        status = "auto"
    elif rot:
        status = "review"
        suggested, rot = rot, 0  # cautious: don't rotate, ask "Is this the right way up?"
    trim = fix.find_border(img)
    trim_box = ",".join(map(str, trim.box)) if trim.box else None   # kept even when only proposed
    trim_applied = bool(trim.box and trim.confidence >= fix.AUTO_TRIM)
    if trim_applied:
        img = fix.apply_trim(img, trim)
        status = "auto" if status == "none" else status
    elif trim.box:
        status = "review"   # not applied: the review screen asks "Did this fix look right?"
    if cut_conf is not None and cut_conf < 0.9:
        status = "review"
    derived = None
    if force_derived or exif_rot or rot or trim_applied:
        derived = f"{aid}.jpg"
        save_derived(img, info, os.path.join(derived_dir, derived))
    w, h = img.size
    cat.db.execute("UPDATE assets SET derived_path=?, width=?, height=? WHERE id=?", (derived, w, h, aid))
    cat.db.execute(
        "INSERT INTO fixes(asset_id, exif_rotation, rotation, rotation_conf, rotation_suggested, trim_box, trim_conf, "
        "trim_applied, cut_conf, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (aid, exif_rot, rot, rot_conf or None, suggested, trim_box, trim.confidence or None, int(trim_applied),
         cut_conf, status))
    cat.db.execute(
        "INSERT INTO meta(asset_id, exif_date, exif_date_raw, camera_make, camera_model, software, filename_date, "
        "is_scan, scan_conf, scan_reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (aid, info.exif_date, info.exif_date_raw, info.make, info.model, info.software, name_date,
         1 if scan.is_scan else 0, scan.confidence, scan.reason))
    cat.db.execute("INSERT INTO hashes(asset_id, phash, dhash, pixels) VALUES (?,?,?,?)",
                   (aid, hashes.to_hex(hashes.phash(img)), hashes.to_hex(hashes.dhash(img)), w * h))


def save_derived(img: Image.Image, info: imageio.ImageInfo, dest: str) -> None:
    """Save a fixed photo as a new JPEG. Keeps the camera info but sets Orientation = 1,
    because the pixels are already upright (bug trap: double rotation)."""
    exif = Image.Exif()
    if info.exif_bytes:
        try:
            exif.load(info.exif_bytes)
        except Exception:
            exif = Image.Exif()
    exif[TAG_ORIENTATION] = 1
    rgb = imageio.to_rgb(img)
    os.makedirs(long_path(os.path.dirname(dest)), exist_ok=True)
    atomic_write(dest, lambda f: rgb.save(f, "JPEG", quality=95, exif=exif.tobytes(), subsampling=0),
                 overwrite=True)
