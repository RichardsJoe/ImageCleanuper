"""Accuracy check: blind questions, wrong guesses become labels, scoring, fewer questions when good."""

import time

import numpy as np

from images import save_jpeg, scene
from imagecleanuper import accuracy, plan
from imagecleanuper.app import Runner
from imagecleanuper.catalog import Catalog
from imagecleanuper.settings import Settings


def _setup(env):
    paths, s, m1, _ = env
    save_jpeg(scene(1), m1 / "xmas.jpg", date="2019:12:25 10:00:00", make="Apple")
    for i in range(6):
        save_jpeg(scene(10 + i), m1 / f"p{i}.jpg", date=f"2018:05:0{i + 1} 10:00:00", make="Apple")
    assert Runner(paths, s).run().state == "done"
    cat = Catalog(paths.catalog)
    ids = [r[0] for r in cat.q("SELECT a.id FROM assets a JOIN files f ON f.id=a.file_id "
                               "WHERE f.rel_path LIKE 'p%' ORDER BY f.rel_path")]
    with cat.tx():
        cat.db.execute("DELETE FROM predictions")
        cat.db.execute("DELETE FROM qa_items")
        cat.db.execute("DELETE FROM qa_rounds")
        for aid in ids[:4]:
            cat.db.execute("INSERT INTO predictions VALUES (?, 'pet', 0.9, 'yes')", (aid,))
    return paths, s, cat, ids, str(paths.data / "derived")


def test_round_asks_blind_and_corrects_wrong_guesses(env):
    paths, s, cat, ids, derived = _setup(env)
    rid = accuracy.build_round(cat, s, seed=1)
    qs = accuracy.questions(cat, derived, rid)
    pets = [q for q in qs if q.check == "pet"]
    holi = [q for q in qs if q.check == "holiday"]
    assert len(pets) == 4 and len(holi) == 1
    assert all(q.options == ["Yes", "No"] for q in pets)
    assert accuracy.answer(cat, pets[0].id, "Yes") is True          # right: nothing changes
    assert accuracy.answer(cat, pets[1].id, "No") is False          # wrong: becomes a label at once
    wrong_asset = cat.scalar("SELECT asset_id FROM qa_items WHERE id=?", (pets[1].id,))
    assert not cat.scalar("SELECT 1 FROM predictions WHERE asset_id=? AND check_key='pet'", (wrong_asset,))
    assert cat.scalar("SELECT value FROM content_answers WHERE asset_id=? AND undone=0", (wrong_asset,)) == 0
    assert accuracy.undo_last(cat) == pets[1].id                    # Ctrl+Z puts it back
    assert cat.scalar("SELECT status FROM predictions WHERE asset_id=? AND check_key='pet'", (wrong_asset,)) == "yes"
    accuracy.answer(cat, pets[1].id, "No")
    accuracy.skip(cat, pets[2].id)                                  # skipped: not counted as wrong
    accuracy.answer(cat, holi[0].id, "No")                          # not Christmas after all
    assert accuracy.waiting(cat) == 1
    # The next run scores the round, and your holiday answer keeps winning.
    assert Runner(paths, s).run().state == "done"
    card = {c.check: c for c in accuracy.scorecard(cat, s)}
    assert card["pet"].accuracy == 0.5 and card["pet"].answered == 2
    assert card["holiday"].accuracy == 0.0
    pv = plan.preview(cat, derived)
    assert not any("Holidays" in k for k in pv.folders)


def test_person_question(env):
    paths, s, cat, ids, derived = _setup(env)
    emb = np.ones(128, np.float32).tobytes()
    with cat.tx():
        for n in ("Calvin", "Mom", "Dad"):
            cat.db.execute("INSERT INTO people(name, created_at) VALUES (?,?)", (n, time.time()))
        cal = cat.scalar("SELECT id FROM people WHERE name='Calvin'")
        fid = cat.db.execute("INSERT INTO faces(asset_id, x, y, w, h, score, embedding, person_id, match_score, source)"
                             " VALUES (?,10,10,50,50,0.9,?,?,0.7,'model')", (ids[5], emb, cal)).lastrowid
    rid = accuracy.build_round(cat, s, seed=2)
    q = [q for q in accuracy.questions(cat, derived, rid) if q.check == f"person:{cal}"][0]
    assert q.question == "Who is the circled person?" and q.face_box == (10, 10, 50, 50)
    assert sorted(q.options[:-1]) == ["Calvin", "Dad", "Mom"] and q.options[-1] == "Someone else"
    assert "Calvin" not in q.question
    accuracy.answer(cat, q.id, "Dad")
    dad = cat.scalar("SELECT id FROM people WHERE name='Dad'")
    assert cat.scalar("SELECT person_id FROM faces WHERE id=?", (fid,)) == dad
    assert cat.scalar("SELECT COUNT(*) FROM face_refs WHERE person_id=?", (dad,)) == 1   # it learns Dad's face
    accuracy.undo_last(cat)
    assert cat.scalar("SELECT person_id FROM faces WHERE id=?", (fid,)) == cal
    assert cat.scalar("SELECT COUNT(*) FROM face_refs WHERE person_id=?", (dad,)) == 0


def test_fewer_questions_when_accurate():
    s = Settings()
    assert accuracy._how_many(None, s, 12) == 12
    assert accuracy._how_many([0.80], s, 0) == accuracy.MORE              # below target
    assert accuracy._how_many([1.0, 0.96], s, 0) == accuracy.MORE         # dropped
    assert accuracy._how_many([0.96, 1.0], s, 0) == s.accuracy_min_per_check
    assert accuracy._how_many([1.0], s, 0) == accuracy.NORMAL


def test_no_question_twice_and_one_open_round(env):
    paths, s, cat, ids, derived = _setup(env)
    rid = accuracy.build_round(cat, s, seed=3)
    assert accuracy.build_round(cat, s) == rid                         # still open: same round
    for q in accuracy.questions(cat, derived, rid):
        accuracy.answer(cat, q.id, "Yes")
    accuracy.close_round(cat)
    rid2 = accuracy.build_round(cat, s, seed=4)
    assert rid2 is None or not ({q.check for q in accuracy.questions(cat, derived, rid2)} & {"pet", "holiday"})


def test_grid_answers_a_page_at_once(env):
    from imagecleanuper import review
    paths, s, cat, ids, derived = _setup(env)
    with cat.tx():
        cat.db.execute("DELETE FROM predictions")
        for i, aid in enumerate(ids):
            cat.db.execute("INSERT INTO predictions VALUES (?, 'pet', ?, 'review')", (aid, 0.40 + i * 0.03))
            cat.db.execute("INSERT INTO predictions VALUES (?, 'junk', 0.5, 'review')", (aid,))
    assert review.count(cat) == 12
    pg = review.grid_page(cat, derived)
    assert pg.check == "pet" and len(pg.items) == 6 and pg.title == "Which of these have a pet in them?"
    first = pg.items[0][0]
    tokens = review.answer_page(cat, "pet", [first], [a for a, _ in pg.items[1:]])
    assert review.count(cat) == 6
    assert cat.scalar("SELECT status FROM predictions WHERE asset_id=? AND check_key='pet'", (first,)) == "yes"
    assert review.grid_page(cat, derived).check == "junk"
    review.undo_page(cat, tokens)
    assert review.count(cat) == 12
    review.skip_check(cat, "pet")                    # "Skip the rest of Pets" until the next run
    assert review.count(cat) == 6 and review.grid_page(cat, derived).check == "junk"
