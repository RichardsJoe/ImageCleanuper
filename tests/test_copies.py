"""Duplicates and burst shots: grouping, picks, keep/remove choices, undo, and the plan."""


from images import save_jpeg, scene
from imagecleanuper import copies, plan
from imagecleanuper.app import Runner
from imagecleanuper.catalog import Catalog


def _run(paths, s):
    assert Runner(paths, s).run().state == "done"
    return Catalog(paths.catalog), str(paths.data / "derived")


def test_duplicate_group_keep_one_remove_rest(env):
    paths, s, m1, m2 = env
    base = scene(40, (1600, 1200))
    save_jpeg(base, m1 / "orig.jpg", date="2021:06:01 10:00:00", make="Apple")
    save_jpeg(base.resize((800, 600)), m2 / "Messenger" / "small.jpg", quality=70)
    save_jpeg(base.resize((640, 480)), m2 / "WhatsApp" / "smaller.jpg", quality=60)
    cat, derived = _run(paths, s)
    gs = copies.groups(cat, derived)
    assert len(gs) == 1 and gs[0].kind == "near" and len(gs[0].copies) == 3
    pick = gs[0].copies[gs[0].pick[0]]
    assert pick.name == "orig.jpg"                                   # most pixels
    assert len(plan.preview(cat, derived).folders["_Duplicates"]) == 2   # undecided extras
    copies.decide(cat, {c.content_key: c is pick for c in gs[0].copies})   # D: keep selected, remove rest
    pv = plan.preview(cat, derived)
    assert "_Duplicates" not in pv.folders and pv.totals["removed"] == 2
    assert copies.groups(cat, derived) == []                           # decided groups leave the queue
    assert copies.undo_last(cat)                                       # Ctrl+Z undoes the whole click
    assert len(copies.groups(cat, derived)) == 1


def test_burst_shots_grouped_and_kept_by_default(env):
    from PIL import ImageFilter
    paths, s, m1, _ = env
    base = scene(60, (1600, 1200))
    save_jpeg(base, m1 / "IMG_1.jpg", date="2021:06:01 10:00:00", make="Apple", model="iPhone 12")
    save_jpeg(scene(60, (1600, 1200), shift=25), m1 / "IMG_2.jpg", date="2021:06:01 10:00:01", make="Apple",
              model="iPhone 12")
    save_jpeg(base.filter(ImageFilter.GaussianBlur(4)), m1 / "IMG_3.jpg", date="2021:06:01 10:00:02", make="Apple",
              model="iPhone 12")
    save_jpeg(scene(61, (1600, 1200)), m1 / "IMG_9.jpg", date="2021:06:01 10:05:00", make="Apple", model="iPhone 12")
    cat, derived = _run(paths, s)
    gs = [g for g in copies.groups(cat, derived) if g.kind == "burst"]
    names = sorted(c.name for c in gs[0].copies)
    assert "IMG_9.jpg" not in names and len(names) >= 2
    assert gs[0].copies[gs[0].pick[0]].name != "IMG_3.jpg"              # the blurry frame isn't suggested
    pv = plan.preview(cat, derived)
    assert "_Duplicates" not in pv.folders                              # bursts stay in normal folders
