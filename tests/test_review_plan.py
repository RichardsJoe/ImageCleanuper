"""Needs review answers and the Report preview."""

import os

from PIL import Image

from images import save_jpeg, scanner_bed, scene
from imagecleanuper import plan, review
from imagecleanuper.app import Runner
from imagecleanuper.catalog import Catalog


def _bordered(seed):
    photo = scene(seed, (600, 400)).point(lambda v: min(255, v // 2 + 90))  # low contrast: unsure trim
    img = Image.new("RGB", (700, 500), (250, 250, 250))
    img.paste(photo, (50, 50))
    return img


def _setup(env, unsure_trim=True):
    paths, s, m1, m2 = env
    save_jpeg(scene(1), m1 / "Phone" / "xmas.jpg", date="2019:12:25 10:00:00", make="Apple")
    save_jpeg(scene(2), m1 / "Misc" / "nodate.jpg")
    save_jpeg(_bordered(3), m1 / "Misc" / "bordered.jpg", date="2018:06:01 10:00:00", make="Apple")
    bed = scanner_bed([scene(30, (700, 500)), scene(31, (500, 700)), scene(32, (800, 550))], [2, -3, 1])
    save_jpeg(bed, m2 / "Scans" / "Christmas 1987" / "page1.jpg", software="EPSON Scan")
    Runner(paths, s).run()
    return paths, Catalog(paths.catalog), str(paths.data / "derived")


def test_preview_folders(env):
    paths, cat, derived = _setup(env)
    pv = plan.preview(cat, derived)
    assert "2019/Holidays/Christmas" in pv.folders
    assert "Unknown Date/Other" in pv.folders
    assert len(pv.folders["Old Family Photos/1987/Holidays/Christmas"]) == 3   # the three cut-outs
    keys = list(pv.folders)
    assert keys.index("2019/Holidays/Christmas") < keys.index("Unknown Date/Other") < \
        keys.index("Old Family Photos/1987/Holidays/Christmas")
    tree = pv.tree()
    assert tree["Old Family Photos"]["_count"] == 3


def _force_review(cat, asset_id, box=None):
    with cat.tx():
        cat.db.execute("UPDATE fixes SET status='review', trim_box=COALESCE(?, trim_box), trim_applied=0 "
                       "WHERE asset_id=?", (box, asset_id))


def test_trim_answer_and_undo(env):
    paths, cat, derived = _setup(env)
    aid = cat.scalar("SELECT a.id FROM assets a JOIN files f ON f.id=a.file_id WHERE f.rel_path LIKE '%bordered.jpg'")
    _force_review(cat, aid, "50,50,650,450")
    q = review.queue(cat, derived)
    item = [i for i in q if i.asset_id == aid][0]
    assert item.question == "trim" and item.trim_box == (50, 50, 650, 450)
    review.answer(cat, item, "looks_good", derived)
    d = cat.scalar("SELECT derived_path FROM assets WHERE id=?", (aid,))
    assert Image.open(os.path.join(derived, d)).size == (600, 400)          # trimmed version made
    assert aid not in [i.asset_id for i in review.queue(cat, derived)]       # answered, leaves the queue
    assert review.undo_last(cat, derived) == aid                             # Ctrl+Z
    assert cat.scalar("SELECT derived_path FROM assets WHERE id=?", (aid,)) is None   # back to untouched
    assert aid in [i.asset_id for i in review.queue(cat, derived)]           # and back in the queue


def test_rotate_cutout_and_undo(env):
    paths, cat, derived = _setup(env)
    aid = cat.scalar("SELECT id FROM assets WHERE kind='cutout' ORDER BY id LIMIT 1")
    _force_review(cat, aid)
    item = [i for i in review.queue(cat, derived) if i.asset_id == aid][0]
    before = Image.open(item.derived).size
    review.answer(cat, item, "rotate", derived)
    assert Image.open(item.derived).size == before[::-1]
    assert aid in [i.asset_id for i in review.queue(cat, derived)]           # rotate keeps it on screen
    review.undo_last(cat, derived)
    assert Image.open(item.derived).size == before


def test_cutout_marked_wrong_keeps_whole_scan(env):
    paths, cat, derived = _setup(env)
    aid = cat.scalar("SELECT id FROM assets WHERE kind='cutout' ORDER BY id LIMIT 1")
    _force_review(cat, aid)
    item = [i for i in review.queue(cat, derived) if i.asset_id == aid][0]
    review.answer(cat, item, "undo", derived)
    pv = plan.preview(cat, derived)
    entries = pv.folders["Old Family Photos/1987/Holidays/Christmas"]
    assert len(entries) == 1 and entries[0].key.startswith("f")             # the whole scan instead of 3
