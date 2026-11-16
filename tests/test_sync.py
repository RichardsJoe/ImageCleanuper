"""Approve and sort: library files, stable names, never touching your own files (T16)."""

import os

from conftest import snapshot
from images import save_jpeg, scanner_bed, scene
from imagecleanuper import copies, sync
from imagecleanuper.app import Runner, recover_journal, sort_library
from imagecleanuper.catalog import Catalog


def _files(root):
    out = set()
    for dp, _, fs in os.walk(root):
        for f in fs:
            out.add(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/"))
    return out


def _setup(env):
    paths, s, m1, m2 = env
    save_jpeg(scene(1), m1 / "Phone" / "xmas.jpg", date="2019:12:25 10:00:00", make="Apple")
    save_jpeg(scene(2), m1 / "Misc" / "IMG_7.jpg", date="2017:03:03 10:00:00", make="Apple")
    save_jpeg(scene(5), m2 / "Other" / "IMG_7.jpg", date="2017:03:04 10:00:00", make="Apple")   # same name
    bed = scanner_bed([scene(30, (700, 500)), scene(31, (500, 700)), scene(32, (800, 550))], [2, -3, 1])
    save_jpeg(bed, m2 / "Scans" / "Christmas 1987" / "page1.jpg", software="EPSON Scan")
    assert Runner(paths, s).run().state == "done"
    cat = Catalog(paths.catalog)
    with cat.tx():
        cat.db.execute("DELETE FROM predictions")      # keep the folders predictable for the test
    return paths, s, cat


def test_sort_copies_plan_and_is_idempotent(env):
    paths, s, cat = _setup(env)
    before = snapshot(env[2]) | snapshot(env[3])
    res = sort_library(paths, s)
    lib = _files(s.library)
    assert "2019/Holidays/Christmas/xmas.jpg" in lib
    assert {f"Old Family Photos/1987/Holidays/Christmas/page1_{c}.jpg" for c in "abc"} <= lib
    assert "Old Family Photos/Original Scans/page1.jpg" in lib
    assert {"2017/Other/IMG_7.jpg", "2017/Other/IMG_7_2.jpg"} <= lib          # name clash → _2
    assert res.added == len(lib) and not res.failed
    assert snapshot(env[2]) | snapshot(env[3]) == before                   # sources untouched (R1)
    again = sort_library(paths, s)
    assert again.added == again.updated == again.removed == 0 and again.unchanged == len(lib)
    # Which file gets _2 depends only on content, so it never swaps between sorts (R11).
    owned = {r["rel_path"]: r["content_key"] for r in cat.q("SELECT * FROM owned_files")}
    assert owned["2017/Other/IMG_7.jpg"] < owned["2017/Other/IMG_7_2.jpg"]


def test_missing_file_restored_and_pending_count(env):
    paths, s, cat = _setup(env)
    sort_library(paths, s)
    os.remove(os.path.join(s.library, "2019", "Holidays", "Christmas", "xmas.jpg"))
    d = sync.pending_changes(cat, s.library, str(paths.data / "derived"))
    assert len(d.restore) == 1 and d.changes == 1
    assert sort_library(paths, s).restored == 1


def test_removed_copy_leaves_library_and_empty_folder_goes(env):
    paths, s, m1, m2 = env
    base = scene(40, (1600, 1200))
    save_jpeg(base, m1 / "orig.jpg", date="2021:06:01 10:00:00", make="Apple")
    save_jpeg(base.resize((800, 600)), m2 / "small.jpg", quality=70)
    assert Runner(paths, s).run().state == "done"
    cat = Catalog(paths.catalog)
    sort_library(paths, s)
    assert "_Duplicates/small.jpg" in _files(s.library)
    assert "_Duplicates/READ ME.txt" in _files(s.library)
    g = copies.groups(cat, str(paths.data / "derived"))[0]
    copies.decide(cat, {c.content_key: c.name == "orig.jpg" for c in g.copies})
    res = sort_library(paths, s)
    assert res.removed == 2                                   # the copy and the READ ME
    assert not os.path.exists(os.path.join(s.library, "_Duplicates"))   # emptied folder deleted


def test_never_deletes_or_overwrites_your_files(env):
    paths, s, cat = _setup(env)
    sort_library(paths, s)
    edited = os.path.join(s.library, "2019", "Holidays", "Christmas", "xmas.jpg")
    with open(edited, "ab") as f:
        f.write(b"edited by you")
    mine = os.path.join(s.library, "2019", "Holidays", "Christmas", "my notes.txt")
    with open(mine, "w") as f:
        f.write("mine")
    # Make the plan drop that photo (its holiday is gone), so the sort wants to remove it.
    with cat.tx():
        cat.db.execute("UPDATE dates SET date='2019-12-01' WHERE asset_id IN (SELECT a.id FROM assets a "
                       "JOIN files f ON f.id=a.file_id WHERE f.rel_path LIKE '%xmas.jpg')")
        cat.db.execute("DELETE FROM holidays")
    res = sort_library(paths, s)
    assert res.edited_by_you == ["2019/Holidays/Christmas/xmas.jpg"]
    assert os.path.exists(edited) and os.path.exists(mine)
    assert "2019/Other/xmas.jpg" in _files(s.library)
    # A file of yours already where a photo should go is never overwritten.
    theirs = os.path.join(s.library, "2017", "Other", "IMG_7.jpg")
    os.remove(theirs)
    with open(theirs, "w") as f:
        f.write("someone else's file")
    with cat.tx():
        cat.db.execute("DELETE FROM owned_files WHERE rel_path='2017/Other/IMG_7.jpg'")
    res = sort_library(paths, s)
    assert res.in_the_way == ["2017/Other/IMG_7.jpg"]
    assert open(theirs).read() == "someone else's file"


def test_crash_leftovers_are_cleaned(env):
    paths, s, cat = _setup(env)
    dest = os.path.join(s.library, "2019", "x.jpg")
    os.makedirs(os.path.dirname(dest))
    open(dest + ".partial", "wb").write(b"half")
    cat.journal("sync_write", {"dest": dest, "rel": "2019/x.jpg"})
    assert recover_journal(cat) == 1
    assert not os.path.exists(dest + ".partial")


def test_clean_name():
    assert sync.clean_name("Mom: 2019?") == "Mom_ 2019_"
    assert sync.clean_name("con.jpg") == "_con.jpg"
    assert sync.clean_name("trailing. ") == "trailing"
