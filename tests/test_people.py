"""People: names, grouping, recognition, strangers, undo (made-up face fingerprints, no real faces)."""

import time

import numpy as np

from imagecleanuper import people
from imagecleanuper.catalog import Catalog


def _cat(tmp_path):
    cat = Catalog(tmp_path / "c.db")
    with cat.tx():
        cat.db.execute("INSERT INTO sources(id, kind, path, path_key, label, created_at) VALUES (1,'folder','/p','/p','001_p',0)")
        for i in range(1, 9):
            cat.db.execute("INSERT INTO files(id, source_id, rel_path, rel_key, orig_path, sha256, size, mtime_ns, kind, "
                           "status, created_at, updated_at) VALUES (?,1,?,?,?,?,1,1,'photo','analyzed',0,0)",
                           (i, f"{i}.jpg", f"{i}.jpg", f"/p/{i}.jpg", f"sha{i}"))
            cat.db.execute("INSERT INTO assets(id, file_id, kind, created_at) VALUES (?,?,'whole',0)", (i, i))
    return cat


def _person(seed):
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


def _face(cat, asset, base, noise=0.25, seed=0):
    v = base + np.random.default_rng(seed).normal(scale=noise / np.sqrt(128), size=128).astype(np.float32)
    v /= np.linalg.norm(v)
    with cat.tx():
        return cat.db.execute("INSERT INTO faces(asset_id, x, y, w, h, score, embedding, source) "
                              "VALUES (?,0,0,50,50,0.9,?,'model')", (asset, v.tobytes())).lastrowid


def test_T13_proper_case_and_did_you_mean(tmp_path):
    cat = _cat(tmp_path)
    assert people.proper_case("calvin") == people.proper_case("CALVIN") == "Calvin"
    assert people.proper_case("mcdonald") == "McDonald"
    a = _face(cat, 1, _person(1))
    assert people.name_group(cat, [a], "  joseph   richards ") == "Joseph Richards"
    assert people.did_you_mean(cat, "Josef Richards") == "Joseph Richards"
    assert people.did_you_mean(cat, "joseph richards") is None   # same name, any case: not a typo
    assert people.suggestions(cat, "jo") == ["Joseph Richards"]


def test_groups_biggest_first_and_naming(tmp_path):
    cat = _cat(tmp_path)
    mom, dad = _person(10), _person(20)
    moms = [_face(cat, a, mom, seed=a) for a in (1, 2, 3, 4)]
    dads = [_face(cat, a, dad, seed=a + 10) for a in (2, 5)]
    gs = people.groups(cat)
    assert [len(g.face_ids) for g in gs] == [4, 2]
    assert sorted(gs[0].face_ids) == sorted(moms) and gs[0].photos == 4
    people.name_group(cat, gs[0].face_ids, "mom")
    people.mark_stranger(cat, dads)
    assert people.groups(cat) == [] and people.pending_count(cat) == 0
    # A new photo of Mom is recognised on the next run; a new face of the stranger stays a stranger.
    new_mom = _face(cat, 6, mom, seed=99)
    new_dad = _face(cat, 7, dad, seed=98)
    counts = people.match_all(cat)
    assert counts["matched"] == 1 and counts["strangers"] == 1
    assert cat.scalar("SELECT p.name FROM faces f JOIN people p ON p.id=f.person_id WHERE f.id=?", (new_mom,)) == "Mom"
    assert cat.scalar("SELECT stranger_id FROM faces WHERE id=?", (new_dad,)) is not None


def test_T14_locked_faces_never_rematched(tmp_path):
    cat = _cat(tmp_path)
    a = _face(cat, 1, _person(1))
    people.name_group(cat, [a], "Calvin")
    _face(cat, 2, _person(1), seed=5)
    people.name_group(cat, [cat.scalar("SELECT MAX(id) FROM faces")], "Dad")   # user says the 2nd is Dad
    people.match_all(cat)
    assert cat.scalar("SELECT p.name FROM faces f JOIN people p ON p.id=f.person_id WHERE f.id=?", (a,)) == "Calvin"


def test_undo_name_and_stranger(tmp_path):
    cat = _cat(tmp_path)
    f1 = _face(cat, 1, _person(1))
    people.name_group(cat, [f1], "Grandma Rose")
    assert people.undo_last(cat)
    assert cat.scalar("SELECT person_id FROM faces WHERE id=?", (f1,)) is None
    assert cat.scalar("SELECT COUNT(*) FROM people") == 0          # a name only used by that answer is removed
    people.mark_stranger(cat, [f1])
    assert people.undo_last(cat) and cat.scalar("SELECT COUNT(*) FROM strangers") == 0
    assert not people.undo_last(cat)


def test_unknown_faces_below_threshold_stay_unknown(tmp_path):
    cat = _cat(tmp_path)
    people.name_group(cat, [_face(cat, 1, _person(1))], "Mom")
    other = _face(cat, 2, _person(2))
    people.match_all(cat)
    assert cat.scalar("SELECT person_id FROM faces WHERE id=?", (other,)) is None
    time.sleep(0)


def test_names_survive_photo_records_being_rebuilt(tmp_path):
    """Re-analysis deletes and rebuilds a photo's faces. The person must still be recognised."""
    cat = _cat(tmp_path)
    mom = _person(10)
    f = _face(cat, 1, mom)
    people.name_group(cat, [f], "Mom")
    with cat.tx():
        cat.db.execute("DELETE FROM faces")                    # what re-analysis does
    again = _face(cat, 1, mom, seed=3)                           # the same face, found again
    people.match_all(cat)
    assert cat.scalar("SELECT p.name FROM faces f JOIN people p ON p.id=f.person_id WHERE f.id=?", (again,)) == "Mom"
