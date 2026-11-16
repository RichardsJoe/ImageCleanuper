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
