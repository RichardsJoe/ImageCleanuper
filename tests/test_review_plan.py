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
    with cat.tx():
        cat.db.execute("DELETE FROM predictions")   # this test is about dates; ignore the image model's opinions
    pv = plan.preview(cat, derived)
    assert "2019/Holidays/Christmas" in pv.folders
    assert any(k.startswith("Unknown Date/") for k in pv.folders)   # Other, or a category the image model chose
    assert len(pv.folders["Old Family Photos/1987/Holidays/Christmas"]) == 3   # the three cut-outs
    keys = list(pv.folders)
    unknown = next(k for k in keys if k.startswith("Unknown Date/"))
    assert keys.index("2019/Holidays/Christmas") < keys.index(unknown) < \
        keys.index("Old Family Photos/1987/Holidays/Christmas")
    tree = pv.tree()
    assert tree["Old Family Photos"]["_count"] == 4          # three cut-outs + the original scan
    assert len(pv.folders["Old Family Photos/Original Scans"]) == 1


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


def test_rotation_suggestion_answer_and_undo(env):
    paths, cat, derived = _setup(env)
    aid = cat.scalar("SELECT id FROM assets WHERE kind='cutout' ORDER BY id LIMIT 1")
    with cat.tx():
        cat.db.execute("UPDATE fixes SET status='review', rotation_suggested=180, cut_conf=0.99 WHERE asset_id=?",
                       (aid,))
    item = [i for i in review.queue(cat, derived) if i.asset_id == aid][0]
    assert item.question == "rotation" and item.suggested == 180
    before = Image.open(item.derived).copy()
    review.answer(cat, item, "looks_good", derived)
    after = Image.open(item.derived)
    assert list(after.rotate(180).getdata())[:50] == list(before.getdata())[:50] or after.size == before.size
    assert cat.scalar("SELECT rotation FROM fixes WHERE asset_id=?", (aid,)) == 180
    review.undo_last(cat, derived)
    assert cat.scalar("SELECT rotation FROM fixes WHERE asset_id=?", (aid,)) == 0
    assert Image.open(item.derived).size == before.size


def test_no_faces_means_no_rotation():
    from imagecleanuper.analyze import fix
    assert fix.predict_sideways(scene(5)) == (0, 0.0)


def test_learns_from_answers(env, monkeypatch):
    """After 10+ yes/no answers on different photos, NEW unanswered photos that look like the
    'yes' examples lean yes, and ones like the 'no' examples lean no."""
    import numpy as np
    from imagecleanuper import content
    from imagecleanuper.ai import clip
    from imagecleanuper.settings import Settings
    paths, cat, derived = _setup(env)
    rng = np.random.default_rng(0)
    centre_pet, centre_other = rng.normal(size=512), rng.normal(size=512)
    fid = cat.scalar("SELECT id FROM files ORDER BY id LIMIT 1")
    made = {"pet": [], "other": []}
    with cat.tx():
        for kind, centre in (("pet", centre_pet), ("other", centre_other)):
            for _ in range(6):
                v = centre + rng.normal(scale=0.6, size=512)
                v /= np.linalg.norm(v)
                aid = cat.db.execute("INSERT INTO assets(file_id, kind, cutout_index, created_at) VALUES (?, 'cutout', ?, 0)",
                                     (fid, 100 + len(made["pet"]) + len(made["other"]))).lastrowid
                cat.db.execute("INSERT INTO hashes(asset_id, clip) VALUES (?,?)", (aid, clip.to_blob(v)))
                made[kind].append(aid)
    # The image model is unsure about everything (0.5), so only learning can decide.
    monkeypatch.setattr(content, "zero_shot", lambda m, ph, others, E, ls=100.0: np.full(len(E), 0.5))
    s = Settings()
    for aid in made["pet"][:5]:
        content.answer(cat, aid, "pet", True)
    for aid in made["other"][:5]:
        content.answer(cat, aid, "pet", False)
    content.classify_all(cat, s)
    prob = {r["asset_id"]: r["prob"] for r in cat.q("SELECT asset_id, prob FROM predictions WHERE check_key='pet'")}
    new_pet, new_other = made["pet"][5], made["other"][5]
    assert prob.get(new_pet, 0) > 0.5 > prob.get(new_other, 0)
