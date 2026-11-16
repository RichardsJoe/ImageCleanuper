"""Settings › People: rename, merge, name a stranger, best samples; settings that switch defaults off."""

import time

import numpy as np

from imagecleanuper import people, settings as settings_mod
from imagecleanuper.catalog import Catalog


def _cat(tmp_path):
    cat = Catalog(tmp_path / "c.db")
    now = time.time()
    with cat.tx():
        fid = cat.db.execute("INSERT INTO sources(kind, path, path_key, label, enabled, created_at) "
                             "VALUES ('folder','x','x','x',1,?)", (now,)).lastrowid
        cat.db.execute("INSERT INTO files(id, source_id, rel_path, rel_key, orig_path, size, mtime_ns, sha256, kind, "
                       "status, created_at, updated_at) VALUES "
                       "(1, ?, 'a.jpg', 'a.jpg', 'x/a.jpg', 1, 0, 'aa', 'photo', 'analyzed', ?, ?)",
                       (fid, now, now))
        cat.db.execute("INSERT INTO assets(id, file_id, kind, created_at) VALUES (1, 1, 'whole', ?)", (now,))
    return cat


def _face(cat, emb, person=None, stranger=None):
    with cat.tx():
        return cat.db.execute("INSERT INTO faces(asset_id, x, y, w, h, embedding, person_id, stranger_id, crop_path) "
                              "VALUES (1,0,0,9,9,?,?,?, 'c.jpg')",
                              (np.full(128, emb, np.float32).tobytes(), person, stranger)).lastrowid


def test_rename_merge_and_name_stranger(tmp_path):
    cat = _cat(tmp_path)
    f1 = _face(cat, 1.0)
    people.name_group(cat, [f1], "mom")
    f2 = _face(cat, 2.0)
    people.name_group(cat, [f2], "Mother")
    mother = cat.scalar("SELECT id FROM people WHERE name='Mother'")
    assert people.rename_person(cat, mother, "MOM") == "Mom"            # same name as an existing person: merge
    mom = cat.scalar("SELECT id FROM people WHERE name='Mom'")
    assert {r[0] for r in cat.q("SELECT person_id FROM faces")} == {mom}
    assert [p["name"] for p in people.people_summary(cat)] == ["Mom"]
    f3 = _face(cat, 3.0)
    people.mark_stranger(cat, [f3])
    sid = people.strangers_summary(cat)[0]["id"]
    people.name_stranger(cat, sid, "aunt sue")
    assert cat.scalar("SELECT p.name FROM faces f JOIN people p ON p.id=f.person_id WHERE f.id=?", (f3,)) == "Aunt Sue"
    assert people.strangers_summary(cat) == []
    assert cat.scalar("SELECT COUNT(*) FROM face_refs WHERE stranger_id IS NOT NULL") == 0


def test_best_sample_toggle(tmp_path):
    cat = _cat(tmp_path)
    f1 = _face(cat, 1.0)
    people.name_group(cat, [f1], "Calvin")
    assert people.toggle_best(cat, f1) is True
    assert cat.scalar("SELECT best FROM face_refs") == 1
    assert people.toggle_best(cat, f1) is False
    assert cat.scalar("SELECT best FROM face_refs") == 0


def test_switched_off_defaults_stay_off(tmp_path):
    s = settings_mod.Settings(library="L", backup="B", masters=["M"])
    s.holiday_windows.pop("valentines_day")
    s.categories.pop("projects")
    s.custom_events.append(("Calvin's Birthday", 7, 14, 0, 1))
    settings_mod.save(s, tmp_path / "s.ini")
    s2, _ = settings_mod.load(tmp_path / "s.ini")
    assert "valentines_day" not in s2.holiday_windows and "christmas" in s2.holiday_windows
    assert "projects" not in s2.categories and "scenic" in s2.categories
    assert s2.custom_events == [("Calvin's Birthday", 7, 14, 0, 1)]


def test_background_faces_are_not_asked_about(tmp_path):
    cat = _cat(tmp_path)
    with cat.tx():
        cat.db.execute("UPDATE assets SET width=3000, height=2000")
        cat.db.execute("INSERT INTO assets(id, file_id, kind, width, height, created_at) VALUES (2,1,'whole',3000,2000,0)")
    main = [_face(cat, 1.0), _face(cat, 1.0)]
    bg = [_face(cat, 5.0), _face(cat, 5.0)]
    with cat.tx():
        cat.db.execute("UPDATE faces SET w=400, h=400, sharp=250 WHERE id IN (?,?)", main)    # big and sharp
        cat.db.execute("UPDATE faces SET w=110, h=110, sharp=7 WHERE id IN (?,?)", bg)        # small and blurry
        cat.db.execute("UPDATE faces SET asset_id=2 WHERE id IN (?,?)", (main[1], bg[1]))
    counts = people.match_all(cat)
    assert counts["background"] == 2
    gs = people.groups(cat)
    assert [sorted(g.face_ids) for g in gs] == [sorted(main)]
    soft_scan = _face(cat, 9.0)                 # old scans are soft but their faces are big: still asked
    with cat.tx():
        cat.db.execute("UPDATE faces SET w=300, h=300, sharp=28 WHERE id=?", (soft_scan,))
    people.match_all(cat)
    assert cat.scalar("SELECT ignored FROM faces WHERE id=?", (soft_scan,)) == 0


def test_not_a_person_is_remembered_and_undoable(tmp_path):
    cat = _cat(tmp_path)
    doll = [_face(cat, 3.0), _face(cat, 3.0)]
    people.not_a_person(cat, doll)
    assert people.groups(cat, min_photos=1) == []
    later = _face(cat, 3.0)                     # the same doll in another photo
    counts = people.match_all(cat)
    assert counts["not_a_person"] == 1
    assert cat.scalar("SELECT ignore_reason FROM faces WHERE id=?", (later,)) == "like_ignored"
    assert len(people.ignored_faces(cat)) == 2
    assert people.undo_last(cat)                # Ctrl+Z
    people.match_all(cat)
    assert cat.scalar("SELECT COUNT(*) FROM faces WHERE ignored=1") == 0


def test_seen_once_is_not_asked(tmp_path):
    cat = _cat(tmp_path)
    _face(cat, 4.0)
    people.match_all(cat)
    assert people.groups(cat) == [] and len(people.groups(cat, min_photos=1)) == 1


def test_which_of_these_are_joseph(tmp_path):
    cat = _cat(tmp_path)
    joe = _face(cat, 1 / np.sqrt(128))          # real face fingerprints have length 1
    people.name_group(cat, [joe], "Joseph")
    rng = np.random.default_rng(1)
    base = np.full(128, 1.0, np.float32) / np.sqrt(128)

    def near(sim):                      # a face whose similarity to Joseph's is about `sim`
        noise = rng.normal(size=128).astype(np.float32)
        noise -= noise.dot(base) * base
        noise /= np.linalg.norm(noise)
        v = sim * base + np.sqrt(1 - sim * sim) * noise
        with cat.tx():
            return cat.db.execute("INSERT INTO faces(asset_id, x, y, w, h, embedding, crop_path) "
                                  "VALUES (1,0,0,9,9,?, 'c.jpg')", (v.astype(np.float32).tobytes(),)).lastrowid
    likely = [near(0.40), near(0.38), near(0.36)]
    far = near(0.05)
    people.match_all(cat)
    pg = people.person_page(cat)
    assert pg.name == "Joseph" and [f["id"] for f in pg.faces] == likely      # most likely first
    in_groups = {f for g in people.groups(cat, min_photos=1) for f in g.face_ids}
    assert far in in_groups and not set(likely) & in_groups      # likely ones come through the grid instead
    tok = people.answer_person_page(cat, pg.person_id, [likely[0]], likely[1:])
    assert cat.scalar("SELECT p.name FROM faces f JOIN people p ON p.id=f.person_id WHERE f.id=?", (likely[0],)) == "Joseph"
    assert people.person_page(cat) is None                                  # the rest aren't offered again
    people.match_all(cat)
    assert people.person_page(cat) is None
    people.undo_person_page(cat, tok)
    assert cat.scalar("SELECT person_id FROM faces WHERE id=?", (likely[0],)) is None


def test_untick_then_name_and_the_rest_follow(tmp_path):
    cat = _cat(tmp_path)
    v = 1 / np.sqrt(128)
    same = [_face(cat, v) for _ in range(20)]               # one person in 20 photos
    people.match_all(cat)
    g = people.groups(cat, min_photos=1)[0]
    assert len(g.samples) == people.GRID_PAGE and len(g.face_ids) == 20
    shown = [f["id"] for f in g.samples]
    people.name_group(cat, shown[1:], "Calvin", rejected=shown[:1])   # first one unticked
    people.match_all(cat)
    named = {r[0] for r in cat.q("SELECT f.id FROM faces f JOIN people p ON p.id=f.person_id WHERE p.name='Calvin'")}
    assert named == set(same) - {shown[0]}                  # the 5 not shown were matched automatically
    assert people.person_page(cat) is None                  # the unticked one isn't offered as Calvin again
