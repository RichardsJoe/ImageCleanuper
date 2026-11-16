"""People: recognising named people, grouping unknown faces for "Who is this?", names, strangers
(pseudocode section 11, wireframe 08). Every answer is saved at once and can be undone (Ctrl+Z).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import numpy as np

from .catalog import Catalog

MATCH = 0.45        # face matches a named person (SFace's own 'same person' line is ~0.36; stricter here)
MARGIN = 0.05       # and must beat the next person by this much
GROUP = 0.50        # unknown faces this similar are grouped together for "Who is this?"


def _emb(rows) -> np.ndarray:
    if not rows:
        return np.zeros((0, 128), np.float32)
    return np.stack([np.frombuffer(r["embedding"], np.float32) for r in rows])


# ---- names ------------------------------------------------------------------------------
def proper_case(text: str) -> str:
    """'joseph richards' -> 'Joseph Richards'; handles McDonald, O'Brien and hyphens."""
    words = []
    for w in " ".join(text.split()).split(" "):
        parts = []
        for part in w.split("-"):
            p = part[:1].upper() + part[1:].lower()
            if p.lower().startswith("o'") and len(p) > 2:
                p = "O'" + p[2].upper() + p[3:]
            if p.lower().startswith("mc") and len(p) > 2:
                p = "Mc" + p[2].upper() + p[3:]
            parts.append(p)
        words.append("-".join(parts))
    return " ".join(words).strip()


def _distance(a: str, b: str) -> int:
    a, b = a.lower(), b.lower()
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def names(cat: Catalog) -> list[str]:
    return [r["name"] for r in cat.q("SELECT name FROM people WHERE merged_into IS NULL ORDER BY name")]


def suggestions(cat: Catalog, typed: str, limit: int = 5) -> list[str]:
    t = typed.strip().lower()
    if not t:
        return []
    all_names = names(cat)
    starts = [n for n in all_names if n.lower().startswith(t)]
    contains = [n for n in all_names if t in n.lower() and n not in starts]
    return (starts + contains)[:limit]


def did_you_mean(cat: Catalog, typed: str) -> str | None:
    """A close existing name (likely a typo), or None. Exact matches (any case) are not typos."""
    name = proper_case(typed)
    best = None
    for n in names(cat):
        if n.lower() == name.lower():
            return None
        d = _distance(n, name)
        if d <= 2 and (best is None or d < best[0]):
            best = (d, n)
    return best[1] if best else None


# ---- recognising named people -------------------------------------------------------------
def match_all(cat: Catalog) -> dict:
    """Give every unlocked face the best-matching named person (or stranger), or none."""
    # References: everything the user named. face_refs keeps them even when photos are re-analyzed.
    refs = cat.q("SELECT person_id, stranger_id, embedding, best FROM face_refs")
    people = {}
    for r in refs:
        key = ("p", r["person_id"]) if r["person_id"] else ("s", r["stranger_id"])
        people.setdefault(key, []).append((np.frombuffer(r["embedding"], np.float32), 3.0 if r["best"] else 1.0))
    faces = cat.q("SELECT id, embedding FROM faces WHERE locked=0")
    counts = {"matched": 0, "strangers": 0, "unknown": 0}
    if not faces:
        return counts
    E = _emb(faces)
    scores = {}
    for key, items in people.items():
        R = np.stack([e for e, _ in items])
        w = np.array([wt for _, wt in items])
        sims = E @ R.T
        # best match, nudged up when it's a "best sample"
        scores[key] = (sims * np.minimum(1.0, 0.9 + 0.1 * w)).max(axis=1)
    with cat.tx():
        for i, f in enumerate(faces):
            ranked = sorted(((s[i], k) for k, s in scores.items()), reverse=True)
            pid = sid = None
            score = None
            if ranked and ranked[0][0] >= MATCH and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= MARGIN):
                score, (kind, ident) = ranked[0]
                if kind == "p":
                    pid = ident
                    counts["matched"] += 1
                else:
                    sid = ident
                    counts["strangers"] += 1
            else:
                counts["unknown"] += 1
            cat.db.execute("UPDATE faces SET person_id=?, stranger_id=?, match_score=?, source='model' WHERE id=?",
                           (pid, sid, float(score) if score is not None else None, f["id"]))
    return counts


# ---- "Who is this?" groups ---------------------------------------------------------------------
@dataclass
class Group:
    face_ids: list[int]
    samples: list[dict] = field(default_factory=list)     # face rows to show (crop_path, asset, box)
    photos: int = 0                                         # how many photos this person is in


def groups(cat: Catalog, limit: int = 20, min_size: int = 1) -> list[Group]:
    """Unknown faces grouped by likely person, biggest groups first (family tends to come first)."""
    rows = cat.q("""SELECT fc.id, fc.asset_id, fc.embedding, fc.crop_path, fc.score, fc.x, fc.y, fc.w, fc.h
                    FROM faces fc JOIN assets a ON a.id = fc.asset_id JOIN active_files f ON f.id = a.file_id
                    WHERE fc.locked=0 AND fc.person_id IS NULL AND fc.stranger_id IS NULL ORDER BY fc.id""")
    if not rows:
        return []
    E = _emb(rows)
    n = len(rows)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    sims = E @ E.T
    for i in range(n):
        for j in np.nonzero(sims[i, i + 1:] >= GROUP)[0]:
            a, b = find(i), find(i + 1 + j)
            if a != b:
                parent[max(a, b)] = min(a, b)
    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    out = []
    for idx in sorted(clusters.values(), key=lambda c: (-len(c), c[0])):
        if len(idx) < min_size:
            continue
        members = [rows[i] for i in idx]
        centre = E[idx].mean(axis=0)
        order = sorted(members, key=lambda r: -float(np.frombuffer(r["embedding"], np.float32) @ centre))
        out.append(Group([r["id"] for r in members], [dict(r) for r in order[:6]],
                         len({r["asset_id"] for r in members})))
        if len(out) >= limit:
            break
    return out


def pending_count(cat: Catalog) -> int:
    return cat.scalar("""SELECT COUNT(*) FROM faces fc JOIN assets a ON a.id = fc.asset_id
                         JOIN active_files f ON f.id = a.file_id
                         WHERE fc.locked=0 AND fc.person_id IS NULL AND fc.stranger_id IS NULL""") or 0


# ---- answers --------------------------------------------------------------------------------
def _prev(cat: Catalog, face_ids: list[int]) -> dict:
    marks = ",".join("?" * len(face_ids))
    return {str(r["id"]): [r["person_id"], r["stranger_id"], r["locked"], r["source"]]
            for r in cat.q(f"SELECT id, person_id, stranger_id, locked, source FROM faces WHERE id IN ({marks})",
                           tuple(face_ids))}


def name_group(cat: Catalog, face_ids: list[int], typed: str) -> str:
    """Name a group of faces. Returns the saved (proper-case) name."""
    name = proper_case(typed)
    if not name:
        raise ValueError("Type a name, or press Stranger.")
    prev = _prev(cat, face_ids)
    with cat.tx():
        row = cat.one("SELECT id FROM people WHERE name=? COLLATE NOCASE AND merged_into IS NULL", (name,))
        created = None
        if row:
            pid = row["id"]
        else:
            pid = created = cat.db.execute("INSERT INTO people(name, created_at) VALUES (?,?)",
                                           (name, time.time())).lastrowid
        marks = ",".join("?" * len(face_ids))
        cat.db.execute(f"UPDATE faces SET person_id=?, stranger_id=NULL, locked=1, source='user' WHERE id IN ({marks})",
                       (pid, *face_ids))
        aid = cat.db.execute("INSERT INTO face_answers(action, face_ids, prev, created_person, created_at) "
                             "VALUES (?,?,?,?,?)", ("name", json.dumps(face_ids), json.dumps(prev), created,
                                                     time.time())).lastrowid
        _add_refs(cat, face_ids, aid, person_id=pid)
    return name


def mark_stranger(cat: Catalog, face_ids: list[int]) -> str:
    prev = _prev(cat, face_ids)
    with cat.tx():
        n = (cat.scalar("SELECT COUNT(*) FROM strangers") or 0) + 1
        label = f"Stranger {n:03d}"
        sid = cat.db.execute("INSERT INTO strangers(label, created_at) VALUES (?,?)", (label, time.time())).lastrowid
        marks = ",".join("?" * len(face_ids))
        cat.db.execute(f"UPDATE faces SET stranger_id=?, person_id=NULL, locked=1, source='user' WHERE id IN ({marks})",
                       (sid, *face_ids))
        aid = cat.db.execute("INSERT INTO face_answers(action, face_ids, prev, created_stranger, created_at) "
                             "VALUES (?,?,?,?,?)", ("stranger", json.dumps(face_ids), json.dumps(prev), sid,
                                                     time.time())).lastrowid
        _add_refs(cat, face_ids, aid, stranger_id=sid)
    return label


def _add_refs(cat: Catalog, face_ids: list[int], answer_id: int, person_id=None, stranger_id=None) -> None:
    marks = ",".join("?" * len(face_ids))
    for r in cat.q(f"SELECT embedding, best FROM faces WHERE id IN ({marks})", tuple(face_ids)):
        cat.db.execute("INSERT INTO face_refs(person_id, stranger_id, embedding, best, answer_id, created_at) "
                       "VALUES (?,?,?,?,?,?)", (person_id, stranger_id, r["embedding"], r["best"], answer_id,
                                                time.time()))


def undo_last(cat: Catalog) -> bool:
    r = cat.one("SELECT * FROM face_answers WHERE undone=0 ORDER BY id DESC LIMIT 1")
    if not r:
        return False
    prev = json.loads(r["prev"])
    with cat.tx():
        for fid, (pid, sid, locked, source) in prev.items():
            cat.db.execute("UPDATE faces SET person_id=?, stranger_id=?, locked=?, source=? WHERE id=?",
                           (pid, sid, locked, source, int(fid)))
        cat.db.execute("DELETE FROM face_refs WHERE answer_id=?", (r["id"],))
        if r["created_person"] and not cat.scalar("SELECT 1 FROM faces WHERE person_id=?", (r["created_person"],)):
            cat.db.execute("DELETE FROM people WHERE id=?", (r["created_person"],))
        if r["created_stranger"] and not cat.scalar("SELECT 1 FROM faces WHERE stranger_id=?", (r["created_stranger"],)):
            cat.db.execute("DELETE FROM strangers WHERE id=?", (r["created_stranger"],))
        cat.db.execute("UPDATE face_answers SET undone=1 WHERE id=?", (r["id"],))
    return True


def photo_people(cat: Catalog, asset_id: int) -> list[str]:
    return [r["name"] for r in cat.q("""SELECT DISTINCT p.name FROM faces fc JOIN people p ON p.id = fc.person_id
                                        WHERE fc.asset_id=? ORDER BY p.name""", (asset_id,))]


def suggest_for_group(cat: Catalog, face_ids: list[int], k: int = 3) -> list[tuple[str, float | None]]:
    """The k people this group most looks like: (name, similarity 0..1). If fewer people are known
    than k, the most recently used names fill the rest (with no score)."""
    if not face_ids:
        return []
    marks = ",".join("?" * len(face_ids))
    G = _emb(cat.q(f"SELECT embedding FROM faces WHERE id IN ({marks})", tuple(face_ids)))
    refs = cat.q("""SELECT r.person_id, r.embedding, p.name FROM face_refs r JOIN people p ON p.id = r.person_id
                    WHERE p.merged_into IS NULL""")
    best: dict[str, float] = {}
    for r in refs:
        sim = float((G @ np.frombuffer(r["embedding"], np.float32)).max())
        best[r["name"]] = max(best.get(r["name"], -1.0), sim)
    ranked = sorted(best.items(), key=lambda x: -x[1])[:k]
    out: list[tuple[str, float | None]] = [(n, max(0.0, s)) for n, s in ranked]
    if len(out) < k:
        recent = cat.q("""SELECT p.name, MAX(a.created_at) t FROM face_answers a JOIN people p ON p.id = a.created_person
                          WHERE a.undone=0 GROUP BY p.name ORDER BY t DESC""")
        for r in recent:
            if r["name"] not in dict(out):
                out.append((r["name"], None))
            if len(out) >= k:
                break
    return out


# ---- People & strangers management (Settings › People) -----------------------------------------
def people_summary(cat: Catalog) -> list[dict]:
    """Every named person with how many photos they're in and some faces (best samples first)."""
    out = []
    for p in cat.q("SELECT id, name FROM people WHERE merged_into IS NULL ORDER BY name"):
        faces = [dict(r) for r in cat.q(
            "SELECT id, crop_path, best FROM faces WHERE person_id=? AND crop_path IS NOT NULL "
            "ORDER BY best DESC, locked DESC, IFNULL(match_score, 1) DESC LIMIT 8", (p["id"],))]
        photos = cat.scalar("SELECT COUNT(DISTINCT asset_id) FROM faces WHERE person_id=?", (p["id"],))
        out.append({"id": p["id"], "name": p["name"], "photos": photos, "faces": faces})
    return out


def strangers_summary(cat: Catalog) -> list[dict]:
    out = []
    for s in cat.q("SELECT id, label FROM strangers ORDER BY id"):
        faces = [dict(r) for r in cat.q("SELECT id, crop_path FROM faces WHERE stranger_id=? AND crop_path IS NOT NULL "
                                        "LIMIT 6", (s["id"],))]
        photos = cat.scalar("SELECT COUNT(DISTINCT asset_id) FROM faces WHERE stranger_id=?", (s["id"],))
        if photos or cat.scalar("SELECT 1 FROM face_refs WHERE stranger_id=?", (s["id"],)):
            out.append({"id": s["id"], "label": s["label"], "photos": photos, "faces": faces})
    return out


def _person_id(cat: Catalog, name: str) -> int:
    row = cat.one("SELECT id FROM people WHERE name=? COLLATE NOCASE AND merged_into IS NULL", (name,))
    if row:
        return row["id"]
    return cat.db.execute("INSERT INTO people(name, created_at) VALUES (?,?)", (name, time.time())).lastrowid


def rename_person(cat: Catalog, person_id: int, typed: str) -> str:
    """Rename someone. Renaming to a name that already exists merges the two people."""
    name = proper_case(typed)
    if not name:
        raise ValueError("Type a name.")
    with cat.tx():
        other = cat.one("SELECT id FROM people WHERE name=? COLLATE NOCASE AND merged_into IS NULL AND id<>?",
                        (name, person_id))
        if other:
            cat.db.execute("UPDATE faces SET person_id=? WHERE person_id=?", (other["id"], person_id))
            cat.db.execute("UPDATE face_refs SET person_id=? WHERE person_id=?", (other["id"], person_id))
            cat.db.execute("UPDATE people SET merged_into=?, name=name || ' (merged ' || id || ')' WHERE id=?",
                           (other["id"], person_id))
        else:
            cat.db.execute("UPDATE people SET name=? WHERE id=?", (name, person_id))
    return name


def name_stranger(cat: Catalog, stranger_id: int, typed: str) -> str:
    """A stranger turns out to be someone you know: they become (or join) that person."""
    name = proper_case(typed)
    if not name:
        raise ValueError("Type a name.")
    with cat.tx():
        pid = _person_id(cat, name)
        cat.db.execute("UPDATE faces SET person_id=?, stranger_id=NULL WHERE stranger_id=?", (pid, stranger_id))
        cat.db.execute("UPDATE face_refs SET person_id=?, stranger_id=NULL WHERE stranger_id=?", (pid, stranger_id))
        cat.db.execute("DELETE FROM strangers WHERE id=?", (stranger_id,))
    return name


def toggle_best(cat: Catalog, face_id: int) -> bool:
    """Mark or unmark a face as a best sample (counts extra when recognising that person)."""
    f = cat.one("SELECT best, embedding FROM faces WHERE id=?", (face_id,))
    new = 0 if f["best"] else 1
    with cat.tx():
        cat.db.execute("UPDATE faces SET best=?, locked=1 WHERE id=?", (new, face_id))
        cat.db.execute("UPDATE face_refs SET best=? WHERE embedding=?", (new, f["embedding"]))
        if new and not cat.scalar("SELECT 1 FROM face_refs WHERE embedding=?", (f["embedding"],)):
            r = cat.one("SELECT person_id, stranger_id FROM faces WHERE id=?", (face_id,))
            cat.db.execute("INSERT INTO face_refs(person_id, stranger_id, embedding, best, created_at) "
                           "VALUES (?,?,?,1,?)", (r["person_id"], r["stranger_id"], f["embedding"], time.time()))
    return bool(new)
