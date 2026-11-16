"""Finish: originals leave the photo folders only when fully accounted for; nothing is lost afterwards."""

import os

from images import save_jpeg, scanner_bed, scene
from imagecleanuper import copies, finish
from imagecleanuper.app import Runner, finish_library, recover_journal, sort_library
from imagecleanuper.catalog import Catalog


def _files(root):
    out = set()
    for dp, _, fs in os.walk(root):
        for f in fs:
            out.add(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/"))
    return out


def _setup(env):
    paths, s, m1, m2 = env
    save_jpeg(scene(1), m1 / "Phone" / "2019" / "xmas.jpg", date="2019:12:25 10:00:00", make="Apple")
    save_jpeg(scene(2), m1 / "Misc" / "IMG_7.jpg", date="2017:03:03 10:00:00", make="Apple")
    (m1 / "Misc" / "Thumbs.db").write_bytes(b"windows litter")
    (m1 / "Docs").mkdir()
    (m1 / "Docs" / "taxes.pdf").write_bytes(b"%PDF not a photo")
    bed = scanner_bed([scene(30, (700, 500)), scene(31, (500, 700)), scene(32, (800, 550))], [2, -3, 1])
    save_jpeg(bed, m2 / "Scans" / "Christmas 1987" / "page1.jpg", software="EPSON Scan")
    assert Runner(paths, s).run().state == "done"
    return paths, s, Catalog(paths.catalog), str(paths.data / "derived")


def test_finish_needs_sort_first(env):
    paths, s, cat, derived = _setup(env)
    p = finish.plan(cat, s.library, derived)
    assert not p.ready and "Approve and sort first" in p.problems[0]
    sort_library(paths, s)
    os.remove(os.path.join(s.library, "2019", "Holidays", "Christmas", "xmas.jpg"))
    p = finish.plan(cat, s.library, derived)
    assert not p.ready and "behind the plan" in p.problems[0]


def test_finish_moves_originals_and_keeps_library(env):
    paths, s, cat, derived = _setup(env)
    m1, m2 = env[2], env[3]
    sort_library(paths, s)
    lib_before = _files(s.library)
    xmas = os.path.join(s.library, "2019", "Holidays", "Christmas", "xmas.jpg")
    os.utime(m1 / "Phone" / "2019" / "xmas.jpg", (1577268000, 1577268000))      # 25 Dec 2019
    p = finish.plan(cat, s.library, derived)
    assert p.ready and len(p.move) == 3 and not p.left
    res = finish_library(paths, s)
    assert res.moved == 3 and not res.skipped
    assert not (m1 / "Phone").exists()                         # emptied folders deleted (2019 and Phone)
    assert not (m1 / "Misc").exists()                          # Thumbs.db alone doesn't keep a folder
    assert (m1 / "Docs" / "taxes.pdf").exists()                # anything else is left alone
    assert not (m2 / "Scans").exists()
    assert m1.exists() and m2.exists()                         # the photo folders themselves stay
    assert int(os.path.getmtime(xmas)) == 1577268000           # library copy has the original's date
    # The next run and sort keep everything, even though the originals are gone.
    assert Runner(paths, s).run().state == "done"
    again = sort_library(paths, s)
    assert again.removed == 0 and again.added == 0 and _files(s.library) == lib_before
    assert not finish.plan(cat, s.library, derived).ready      # nothing left to finish


def test_new_folder_after_finish_copies_from_library(env):
    paths, s, cat, derived = _setup(env)
    sort_library(paths, s)
    finish_library(paths, s)
    # Teach something new about a finished photo: it now also belongs in Pets.
    with cat.tx():
        aid = cat.scalar("SELECT a.id FROM assets a JOIN files f ON f.id=a.file_id WHERE f.rel_path LIKE '%IMG_7.jpg'")
        cat.db.execute("DELETE FROM predictions WHERE asset_id=?", (aid,))   # the model may have said Junk
        cat.db.execute("INSERT OR REPLACE INTO predictions(asset_id, check_key, prob, status) VALUES (?, 'pet', 0.9, 'yes')",
                       (aid,))
    res = sort_library(paths, s)
    assert res.added >= 1 and not res.failed
    assert "2017/Pets/IMG_7.jpg" in _files(s.library)
    assert res.removed <= 2                      # at most the old _Junk copy and its READ ME


def test_removed_copy_goes_to_recycle_bin(env):
    paths, s, m1, m2 = env
    base = scene(40, (1600, 1200))
    save_jpeg(base, m1 / "orig.jpg", date="2021:06:01 10:00:00", make="Apple")
    save_jpeg(base.resize((800, 600)), m2 / "WhatsApp" / "small.jpg", quality=70)
    assert Runner(paths, s).run().state == "done"
    cat = Catalog(paths.catalog)
    g = copies.groups(cat, str(paths.data / "derived"))[0]
    copies.decide(cat, {c.content_key: c.name == "orig.jpg" for c in g.copies})
    sort_library(paths, s)
    binned = []
    res = finish_library(paths, s, trash=lambda p: (binned.append(p), os.remove(p)))
    assert res.moved == 1 and res.recycled == 1
    assert [os.path.basename(b) for b in binned] == ["small.jpg"]


def test_changed_original_is_not_deleted(env):
    paths, s, cat, derived = _setup(env)
    sort_library(paths, s)
    victim = env[2] / "Misc" / "IMG_7.jpg"
    with open(victim, "ab") as f:
        f.write(b"edited after backup")
    res = finish_library(paths, s)
    assert victim.exists() and res.skipped and "changed" in res.skipped[0][1]


def test_crash_mid_finish_recovers(env):
    paths, s, cat, derived = _setup(env)
    fid, src = cat.one("SELECT id, orig_path FROM files WHERE rel_path LIKE '%IMG_7.jpg'")
    cat.journal("finish_move", {"src": src, "file_id": fid, "sha": "x"})
    os.remove(src)                                  # the delete happened, then the power went
    assert recover_journal(cat) == 1
    assert cat.scalar("SELECT finished_at FROM files WHERE id=?", (fid,)) is not None


def test_add_new_photos_folder(env):
    paths, s, cat, derived = _setup(env)
    sort_library(paths, s)
    drop = os.path.join(s.library, "_Add New Photos")
    assert os.path.exists(os.path.join(drop, "READ ME.txt"))
    os.makedirs(os.path.join(drop, "From Grandma"))
    from pathlib import Path
    save_jpeg(scene(9), Path(drop) / "From Grandma" / "beach.jpg", date="2016:08:10 10:00:00", make="Apple")
    assert Runner(paths, s).run().state == "done"
    sort_library(paths, s)
    assert any(f.startswith(("2016/", "_Junk/")) and f.endswith("beach.jpg") for f in _files(s.library))
    finish_library(paths, s)
    assert not os.path.exists(os.path.join(drop, "From Grandma"))     # emptied
    assert os.path.exists(os.path.join(drop, "READ ME.txt"))          # the drop folder itself stays
