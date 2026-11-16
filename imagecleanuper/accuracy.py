"""Accuracy check (pseudocode section 15): blind samples of confident guesses, scored per check.

A round is a handful of questions per check (each person, Pets, Junk, each category, Holidays,
Rotation, Border trim). The app's guess is saved when the question is made and never shown, so your
answer is a fair test. A wrong guess is corrected straight away (it becomes a label the models learn
from). A round is scored when the next run starts; the scorecard shows the latest score per check.
Checks at or above the target for a few rounds get fewer questions; weak or falling ones get more.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

from . import labels as labels_mod
from .catalog import Catalog
from .dates import HOLIDAY_NAMES
from .settings import Settings

MORE, NORMAL = 8, 5
SOMEONE_ELSE = "Someone else"
YES, NO = "Yes", "No"


def check_label(key: str, cat: Catalog, s: Settings) -> str:
    if key.startswith("person:"):
        return cat.scalar("SELECT name FROM people WHERE id=?", (int(key[7:]),)) or "Someone"
    if key.startswith("cat:"):
        return (s.categories.get(key[4:]) or (key[4:].title(),))[0]
    return {"pet": "Pets", "junk": "Junk", "holiday": "Holidays", "rotation": "Rotation",
            "trim": "Border trim"}.get(key, key)


def _image(row, derived_dir: str) -> str:
    import os
    return os.path.join(derived_dir, row["derived_path"]) if row["derived_path"] else row["orig_path"]


# ---- candidates -------------------------------------------------------------------------------
_ASSETS = """SELECT a.id, a.kind, a.cutout_index, a.derived_path, f.orig_path, f.sha256
             FROM assets a JOIN active_files f ON f.id = a.file_id WHERE f.status = 'analyzed'"""


def _candidates(cat: Catalog, s: Settings) -> dict[str, list[dict]]:
    """Confident guesses that could be asked about, per check (never locked or in review)."""
    out: dict[str, list[dict]] = {}
    asked = {(r[0], r[1]) for r in cat.q("SELECT check_key, content_key FROM qa_items")}
    assets = {r["id"]: dict(r) for r in cat.q(_ASSETS)}
    answered = {(r[0], r[1]) for r in cat.q("SELECT content_key, check_key FROM content_answers WHERE undone=0")}

    def add(check, a, **kw):
        ck = labels_mod.asset_key(a)
        if (check, ck) not in asked:
            out.setdefault(check, []).append({"asset": a, "ck": ck, **kw})

    people = cat.q("SELECT id, name FROM people WHERE merged_into IS NULL ORDER BY name")
    for f in cat.q("SELECT id, asset_id, person_id, x, y, w, h FROM faces "
                   "WHERE person_id IS NOT NULL AND locked=0 AND source='model'"):
        if f["asset_id"] in assets and people:
            add(f"person:{f['person_id']}", assets[f["asset_id"]], face=dict(f))
    for p in cat.q("SELECT asset_id, check_key, status FROM predictions WHERE status IN ('yes', 'no')"):
        a = assets.get(p["asset_id"])
        if a is None or (labels_mod.asset_key(a), p["check_key"]) in answered:
            continue
        if p["check_key"].startswith("cat:") and p["check_key"][4:] not in s.categories:
            continue
        add(p["check_key"], a, hidden=YES if p["status"] == "yes" else NO)
    for h in cat.q("SELECT asset_id, holiday FROM holidays WHERE source = 'date'"):
        if h["asset_id"] in assets:
            add("holiday", assets[h["asset_id"]], holiday=h["holiday"])
    for x in cat.q("SELECT asset_id, rotation, trim_applied FROM fixes WHERE status = 'auto'"):
        a = assets.get(x["asset_id"])
        if a is None or not a["derived_path"]:
            continue
        if x["rotation"]:
            add("rotation", a)
        if x["trim_applied"]:
            add("trim", a)
    return out


def _history(cat: Catalog) -> dict[str, list[float]]:
    """check → accuracy in each closed round, oldest first (skipped items don't count)."""
    out: dict[str, list[float]] = {}
    for r in cat.q("""SELECT q.round_id, q.check_key, SUM(q.answer = q.hidden) AS ok, COUNT(*) AS n
                      FROM qa_items q JOIN qa_rounds r ON r.id = q.round_id
                      WHERE r.closed_at IS NOT NULL AND q.answer IS NOT NULL
                      GROUP BY q.round_id, q.check_key ORDER BY q.round_id"""):
        out.setdefault(r["check_key"], []).append(r["ok"] / r["n"])
    return out


def _how_many(hist: list[float] | None, s: Settings, first_share: int) -> int:
    if not hist:
        return first_share
    if hist[-1] < s.accuracy_target or (len(hist) >= 2 and hist[-1] < hist[-2]):
        return MORE
    k = s.accuracy_rounds_to_shrink
    if len(hist) >= k and all(h >= s.accuracy_target for h in hist[-k:]):
        return s.accuracy_min_per_check
    return NORMAL


def _question(check: str, c: dict, cat: Catalog, s: Settings, rnd: random.Random) -> tuple[str, str, list, str]:
    """(kind, question, options, hidden)"""
    if check.startswith("person:"):
        hidden = check_label(check, cat, s)
        others = [r[0] for r in cat.q("SELECT name FROM people WHERE merged_into IS NULL AND name<>? ORDER BY name",
                                      (hidden,))]
        opts = rnd.sample(others, min(3, len(others))) + [hidden]
        rnd.shuffle(opts)
        return "person", "Who is the circled person?", opts + [SOMEONE_ELSE], hidden
    if check == "holiday":
        name = HOLIDAY_NAMES.get(c["holiday"], c["holiday"].split(":", 1)[-1])
        return "yesno", f"Was this taken around {name}?", [YES, NO], YES
    if check == "rotation":
        return "yesno", "Is this photo the right way up?", [YES, NO], YES
    if check == "trim":
        return "yesno", "Is the border trimmed well (nothing important cut off)?", [YES, NO], YES
    if check == "pet":
        q = "Is there a pet in this photo?"
    elif check == "junk":
        q = "Is this junk (screenshot, receipt, accidental photo)?"
    else:
        q = f"Does this belong in {check_label(check, cat, s)}?"
    return "yesno", q, [YES, NO], c["hidden"]


def open_round(cat: Catalog) -> int | None:
    return cat.scalar("SELECT id FROM qa_rounds WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1")


def build_round(cat: Catalog, s: Settings, seed: int | None = None) -> int | None:
    """Make a new round unless one is still open. Returns its id (None if there's nothing to ask)."""
    rid = open_round(cat)
    if rid:
        return rid
    cands = _candidates(cat, s)
    if not cands:
        return None
    hist = _history(cat)
    first_share = max(s.accuracy_min_per_check, s.accuracy_start_items // max(1, len(cands)))
    with cat.tx():
        rid = cat.db.execute("INSERT INTO qa_rounds(created_at) VALUES (?)", (time.time(),)).lastrowid
    rnd = random.Random(seed if seed is not None else rid)
    picked = []
    for check in sorted(cands):
        n = _how_many(hist.get(check), s, first_share if not hist else NORMAL)
        pool = sorted(cands[check], key=lambda c: (c["ck"], (c.get("face") or {}).get("id", 0)))
        for c in rnd.sample(pool, min(n, len(pool))):
            picked.append((check, c))
    rnd.shuffle(picked)               # mixed order, so one check doesn't come in a block
    with cat.tx():
        for pos, (check, c) in enumerate(picked):
            kind, q, opts, hidden = _question(check, c, cat, s, rnd)
            cat.db.execute(
                "INSERT INTO qa_items(round_id, position, check_key, kind, content_key, asset_id, face_id, question,"
                " options, hidden) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, pos, check, kind, c["ck"], c["asset"]["id"], (c.get("face") or {}).get("id"), q,
                 json.dumps(opts), hidden))
    return rid


@dataclass
class Question:
    id: int
    check: str
    kind: str
    question: str
    options: list[str]
    image: str | None
    face_box: tuple | None
    answer: str | None
    skipped: bool


def questions(cat: Catalog, derived_dir: str, round_id: int | None = None) -> list[Question]:
    rid = round_id or open_round(cat)
    if not rid:
        return []
    out = []
    for q in cat.q("""SELECT q.*, a.derived_path, f.orig_path, fc.x, fc.y, fc.w, fc.h FROM qa_items q
                      LEFT JOIN assets a ON a.id = q.asset_id LEFT JOIN files f ON f.id = a.file_id
                      LEFT JOIN faces fc ON fc.id = q.face_id
                      WHERE q.round_id=? ORDER BY q.position""", (rid,)):
        if q["asset_id"] is None or q["orig_path"] is None:
            continue
        box = (q["x"], q["y"], q["w"], q["h"]) if q["x"] is not None else None
        out.append(Question(q["id"], q["check_key"], q["kind"], q["question"], json.loads(q["options"]),
                            _image(q, derived_dir), box, q["answer"], bool(q["skipped"])))
    return out


def waiting(cat: Catalog) -> int:
    rid = open_round(cat)
    return cat.scalar("SELECT COUNT(*) FROM qa_items WHERE round_id=? AND answer IS NULL AND skipped=0",
                      (rid,)) if rid else 0


def answer(cat: Catalog, item_id: int, choice: str) -> bool:
    """Record your answer. A wrong guess is corrected right away. Returns True if the app was right."""
    q = cat.one("SELECT * FROM qa_items WHERE id=?", (item_id,))
    right = choice == q["hidden"]
    undo: dict = {}
    with cat.tx():
        if not right:
            undo = _correct(cat, q, choice)
        cat.db.execute("UPDATE qa_items SET answer=?, skipped=0, answered_at=?, undo=? WHERE id=?",
                       (choice, time.time(), json.dumps(undo), item_id))
    return right


def _correct(cat: Catalog, q, choice: str) -> dict:
    """Fix a wrong guess so the next run learns from it. Returns how to undo it. Runs inside a tx."""
    aid, check = q["asset_id"], q["check_key"]
    if q["kind"] == "person":
        f = cat.one("SELECT person_id, stranger_id, locked, source, embedding, best FROM faces WHERE id=?",
                    (q["face_id"],))
        undo = {"face": [q["face_id"], f["person_id"], f["stranger_id"], f["locked"], f["source"]]}
        if choice == SOMEONE_ELSE:
            # Not who the app said: no folder for them here, and it isn't asked again.
            cat.db.execute("UPDATE faces SET person_id=NULL, locked=1, source='user' WHERE id=?", (q["face_id"],))
        else:
            pid = cat.scalar("SELECT id FROM people WHERE name=? AND merged_into IS NULL", (choice,))
            cat.db.execute("UPDATE faces SET person_id=?, stranger_id=NULL, locked=1, source='user' WHERE id=?",
                           (pid, q["face_id"]))
            undo["ref"] = cat.db.execute(
                "INSERT INTO face_refs(person_id, embedding, best, created_at) VALUES (?,?,?,?)",
                (pid, f["embedding"], f["best"], time.time())).lastrowid
        return undo
    if check in ("rotation", "trim"):
        prev = cat.scalar("SELECT status FROM fixes WHERE asset_id=?", (aid,))
        # Back to Needs review, where you can turn or un-trim it.
        cat.db.execute("UPDATE fixes SET status='review' WHERE asset_id=?", (aid,))
        return {"fix": [aid, prev]}
    if check == "holiday":
        lid = cat.db.execute("INSERT INTO photo_labels(content_key, kind, value, source, created_at) "
                             "VALUES (?,?,?,?,?)", (q["content_key"], "holiday", "", "accuracy", time.time())).lastrowid
        cat.db.execute("DELETE FROM holidays WHERE asset_id=?", (aid,))
        return {"label": lid}
    # pet / junk / categories: a yes/no answer the image model learns from
    value = choice == YES
    pred = cat.one("SELECT prob, status FROM predictions WHERE asset_id=? AND check_key=?", (aid, check))
    caid = cat.db.execute("INSERT INTO content_answers(asset_id, content_key, check_key, value, best, created_at) "
                          "VALUES (?,?,?,?,0,?)", (aid, q["content_key"], check, int(value), time.time())).lastrowid
    if value:
        cat.db.execute("INSERT OR REPLACE INTO predictions(asset_id, check_key, prob, status) VALUES (?,?,1.0,'yes')",
                       (aid, check))
    else:
        cat.db.execute("DELETE FROM predictions WHERE asset_id=? AND check_key=?", (aid, check))
    return {"content": caid, "pred": [aid, check, pred["prob"], pred["status"]] if pred else None}


def skip(cat: Catalog, item_id: int) -> None:
    with cat.tx():
        cat.db.execute("UPDATE qa_items SET skipped=1 WHERE id=? AND answer IS NULL", (item_id,))


def undo_last(cat: Catalog) -> int | None:
    """Undo the most recent answer in the open round. Returns that item's id."""
    rid = open_round(cat)
    q = rid and cat.one("SELECT * FROM qa_items WHERE round_id=? AND answer IS NOT NULL "
                        "ORDER BY answered_at DESC, id DESC LIMIT 1", (rid,))
    if not q:
        return None
    u = json.loads(q["undo"] or "{}")
    with cat.tx():
        if "face" in u:
            fid, pid, sid, locked, source = u["face"]
            cat.db.execute("UPDATE faces SET person_id=?, stranger_id=?, locked=?, source=? WHERE id=?",
                           (pid, sid, locked, source, fid))
        if "ref" in u:
            cat.db.execute("DELETE FROM face_refs WHERE id=?", (u["ref"],))
        if "fix" in u:
            cat.db.execute("UPDATE fixes SET status=? WHERE asset_id=?", (u["fix"][1], u["fix"][0]))
        if "label" in u:
            cat.db.execute("UPDATE photo_labels SET undone=1 WHERE id=?", (u["label"],))
        if "content" in u:
            cat.db.execute("UPDATE content_answers SET undone=1 WHERE id=?", (u["content"],))
            if u.get("pred"):
                aid, check, prob, status = u["pred"]
                cat.db.execute("INSERT OR REPLACE INTO predictions(asset_id, check_key, prob, status) "
                               "VALUES (?,?,?,?)", (aid, check, prob, status))
        cat.db.execute("UPDATE qa_items SET answer=NULL, answered_at=NULL, undo=NULL WHERE id=?", (q["id"],))
    return q["id"]


def close_round(cat: Catalog) -> bool:
    """Score the open round (at the start of a run) if you answered anything in it."""
    rid = open_round(cat)
    if not rid or not cat.scalar("SELECT 1 FROM qa_items WHERE round_id=? AND answer IS NOT NULL", (rid,)):
        return False
    with cat.tx():
        cat.db.execute("UPDATE qa_rounds SET closed_at=? WHERE id=?", (time.time(), rid))
    return True


@dataclass
class Score:
    check: str
    label: str
    accuracy: float
    answered: int
    trend: int          # +1 better than last round, -1 worse, 0 same or first


def scorecard(cat: Catalog, s: Settings) -> list[Score]:
    out = []
    for check, hist in sorted(_history(cat).items()):
        n = cat.scalar("""SELECT COUNT(*) FROM qa_items q JOIN qa_rounds r ON r.id=q.round_id
                          WHERE q.check_key=? AND q.answer IS NOT NULL AND r.id = (
                            SELECT MAX(r2.id) FROM qa_rounds r2 JOIN qa_items q2 ON q2.round_id=r2.id
                            WHERE r2.closed_at IS NOT NULL AND q2.check_key=? AND q2.answer IS NOT NULL)""",
                       (check, check))
        trend = 0 if len(hist) < 2 else (1 if hist[-1] > hist[-2] + 1e-9 else (-1 if hist[-1] < hist[-2] - 1e-9 else 0))
        out.append(Score(check, check_label(check, cat, s), hist[-1], n, trend))
    return sorted(out, key=lambda x: (x.accuracy, x.label))
