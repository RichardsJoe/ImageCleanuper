"""Pets, categories and Junk from the photo's CLIP fingerprint (pseudocode sections 8 and 12).

Each check compares the photo with phrases describing it and with phrases describing ordinary
family photos. Confident yes / no is decided automatically; the middle goes to Review. After 10+
answers (yes and no) for a check, a small model trained on YOUR answers is blended in, and it
counts for more as answers grow. Best samples count three times.
"""

from __future__ import annotations

import time

import numpy as np

from .ai import clip, models as models_mod
from .catalog import Catalog
from .settings import Settings

YES, NO = 0.60, 0.35          # above YES = yes, below NO = no, between = ask in Review
MIN_ANSWERS = 10

PET = ["a photo of a dog", "a photo of a cat", "a photo of a pet", "a puppy", "a kitten"]
JUNK = ["a screenshot of a phone screen", "a screenshot of a computer screen", "a meme with text on it",
        "a blurry accidental photo", "an accidental photo of the floor", "a photo taken inside a pocket"]
ORDINARY = ["a family photo of people", "a photo of a person", "a photo of children", "a photo of a baby",
            "a group photo", "a photo of a living room", "a photo of a house", "a photo of food on a table",
            "a photo of a car", "a photo of a birthday party", "a snapshot of everyday life"]


def checks(s: Settings) -> dict[str, list[str]]:
    out = {"pet": PET, "junk": JUNK}
    for key, (_folder, desc) in s.categories.items():
        parts = [p.strip() for p in desc.split(",") if p.strip()]
        out[f"cat:{key}"] = [f"a photo of {p}" for p in parts] or [f"a photo of {desc}"]
    return out


def zero_shot(m, check_phrases: list[str], others: list[str], E: np.ndarray, logit_scale: float = 100.0) -> np.ndarray:
    """P(check) for each photo: softmax over the check's phrases + the 'others'."""
    T = clip.text_embeddings(m, check_phrases + others)
    logits = logit_scale * (E @ T.T)
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    p /= p.sum(axis=1, keepdims=True)
    return p[:, :len(check_phrases)].sum(axis=1)


def train_head(X: np.ndarray, y: np.ndarray, w: np.ndarray, steps: int = 300, lr: float = 0.5) -> tuple[np.ndarray, float]:
    """Tiny logistic regression on CLIP fingerprints (weighted)."""
    coef = np.zeros(X.shape[1], np.float32)
    bias = 0.0
    for _ in range(steps):
        z = X @ coef + bias
        p = 1 / (1 + np.exp(-z))
        g = (p - y) * w
        coef -= lr * (X.T @ g / w.sum() + 1e-3 * coef)
        bias -= lr * float(g.sum() / w.sum())
    return coef, bias


def classify_all(cat: Catalog, s: Settings) -> dict:
    m = models_mod.get()
    if m is None:
        return {"skipped": "AI models not installed"}
    rows = cat.q("""SELECT a.id, h.clip, m.is_scan FROM assets a JOIN active_files f ON f.id = a.file_id
                    JOIN hashes h ON h.asset_id = a.id LEFT JOIN meta m ON m.asset_id = a.id
                    WHERE h.clip IS NOT NULL ORDER BY a.id""")
    if not rows:
        return {}
    ids = [r["id"] for r in rows]
    E = np.stack([clip.from_blob(r["clip"]) for r in rows])
    scan = np.array([bool(r["is_scan"]) for r in rows])
    relink(cat)
    answers = {}
    for a in cat.q("SELECT asset_id, check_key, value, best FROM content_answers "
                   "WHERE undone=0 AND asset_id IS NOT NULL ORDER BY id"):
        answers[(a["asset_id"], a["check_key"])] = (a["value"], a["best"])
    claude = {(r[0], r[1]): r[2] for r in cat.q(
        "SELECT a.id, o.check_key, o.value FROM claude_opinions o JOIN files f ON f.sha256 = substr(o.content_key, 1, 64) "
        "JOIN assets a ON a.file_id = f.id AND o.content_key = f.sha256 || ':' || a.kind || ':' || "
        "IFNULL(a.cutout_index, '')")}
    counts: dict[str, dict] = {}
    all_checks = checks(s)
    with cat.tx():
        cat.db.execute("DELETE FROM predictions")
        for key, phrases in all_checks.items():
            others = ORDINARY + [p for k, ph in all_checks.items() if k != key for p in ph]
            p = zero_shot(m, phrases, others, E)
            # Blend in what the user taught (once there are enough yes AND no answers).
            labelled = [(ids.index(aid), v, b) for (aid, k), (v, b) in answers.items() if k == key and aid in ids]
            ys = [v for _, v, _ in labelled]
            if len(labelled) >= MIN_ANSWERS and 0 < sum(ys) < len(ys):
                idx = [i for i, _, _ in labelled]
                coef, bias = train_head(E[idx], np.array(ys, np.float32),
                                        np.array([3.0 if b else 1.0 for _, _, b in labelled], np.float32))
                head = 1 / (1 + np.exp(-(E @ coef + bias)))
                weight = min(0.8, len(labelled) / 50)
                p = (1 - weight) * p + weight * head
            c = {"yes": 0, "review": 0}
            for i, aid in enumerate(ids):
                if key == "junk" and scan[i]:
                    continue                      # old scans are never junk
                ans = answers.get((aid, key))
                if ans is not None:               # your answer always wins (locked)
                    status = "yes" if ans[0] else "no"
                elif p[i] >= YES:
                    status = "yes"
                elif p[i] >= NO and (aid, key) in claude:   # unsure: Claude's second opinion decides
                    status = "yes" if claude[(aid, key)] else "no"
                elif p[i] >= NO:
                    status = "review"
                else:
                    continue                      # a clear no isn't stored
                c[status] = c.get(status, 0) + 1
                cat.db.execute("INSERT INTO predictions(asset_id, check_key, prob, status) VALUES (?,?,?,?)",
                               (aid, key, float(p[i]), status))
            counts[key] = c
    return counts


def content_key(cat: Catalog, asset_id: int) -> str:
    r = cat.one("SELECT f.sha256, a.kind, a.cutout_index FROM assets a JOIN files f ON f.id = a.file_id "
                "WHERE a.id=?", (asset_id,))
    return f"{r['sha256']}:{r['kind']}:{r['cutout_index'] if r['cutout_index'] is not None else ''}"


def relink(cat: Catalog) -> None:
    """Re-attach answers to photos rebuilt by re-analysis (matched by content, not by record number)."""
    loose = cat.q("SELECT id, content_key FROM content_answers WHERE asset_id IS NULL")
    if not loose:
        return
    with cat.tx():
        for r in loose:
            sha, kind, idx = r["content_key"].split(":")
            aid = cat.scalar("SELECT a.id FROM assets a JOIN files f ON f.id = a.file_id WHERE f.sha256=? AND a.kind=? "
                             "AND IFNULL(a.cutout_index, '') = ? ORDER BY a.id LIMIT 1",
                             (sha, kind, int(idx) if idx else ""))
            if aid:
                cat.db.execute("UPDATE content_answers SET asset_id=? WHERE id=?", (aid, r["id"]))


def answer(cat: Catalog, asset_id: int, key: str, value: bool, best: bool = False) -> int:
    ck = content_key(cat, asset_id)
    with cat.tx():
        cur = cat.db.execute("INSERT INTO content_answers(asset_id, content_key, check_key, value, best, created_at) "
                             "VALUES (?,?,?,?,?,?)", (asset_id, ck, key, int(value), int(best), time.time()))
        cat.db.execute("UPDATE predictions SET status=? WHERE asset_id=? AND check_key=?",
                       ("yes" if value else "no", asset_id, key))
        if value is False:
            cat.db.execute("DELETE FROM predictions WHERE asset_id=? AND check_key=? AND status='no'", (asset_id, key))
    return cur.lastrowid


def undo_last(cat: Catalog) -> bool:
    r = cat.one("SELECT * FROM content_answers WHERE undone=0 ORDER BY id DESC LIMIT 1")
    if not r:
        return False
    with cat.tx():
        cat.db.execute("UPDATE content_answers SET undone=1 WHERE id=?", (r["id"],))
        cat.db.execute("INSERT OR REPLACE INTO predictions(asset_id, check_key, prob, status) VALUES (?,?,0.5,'review')",
                       (r["asset_id"], r["check_key"]))
    return True


def label(key: str, s: Settings) -> str:
    if key == "pet":
        return "Pets"
    if key == "junk":
        return "Junk"
    return s.categories.get(key.split(":", 1)[1], (key, ""))[0]
