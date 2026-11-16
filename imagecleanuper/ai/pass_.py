"""The AI step (after analysis): faces + CLIP fingerprint for every photo, saved per photo (rule R8).

Kept separate from analysis so improving the AI never forces scans to be cut again.
Skipped entirely (with a note) when the models aren't installed.
"""

from __future__ import annotations

import os
from typing import Callable

from PIL import Image

from .. import log
from ..analyze import imageio
from ..catalog import Catalog
from ..fileops import atomic_write, remove_quietly
from ..paths import long_path
from . import clip, faces, models as models_mod

AI_VERSION = 2   # 2 = sharpness for burst shots. Bump when face/CLIP processing changes so it's redone on the next run
_log = log.get("ai")
Progress = Callable[..., None]


def _upright_image(row, derived_dir: str) -> Image.Image:
    """The photo as the user will see it: the fixed version if there is one, else the original."""
    if row["derived_path"]:
        img = Image.open(long_path(os.path.join(derived_dir, row["derived_path"])))
        img.load()
        return img
    img, info = imageio.open_image(row["orig_path"])
    up, _ = imageio.upright(img, info.orientation)
    return up


def pending(cat: Catalog) -> list:
    return cat.q(
        """
        SELECT a.id, a.derived_path, f.orig_path FROM assets a JOIN active_files f ON f.id = a.file_id
        WHERE f.status = 'analyzed' AND f.kind = 'photo' AND a.ai_version < ? AND f.finished_at IS NULL
        ORDER BY a.id
        """, (AI_VERSION,))


def run(cat: Catalog, data_dir: str, min_face_px: int = 40, progress: Progress | None = None) -> dict:
    m = models_mod.get()
    if m is None:
        return {"skipped": "AI models not installed"}
    derived_dir = os.path.join(data_dir, "derived")
    crops_dir = os.path.join(data_dir, "faces")
    os.makedirs(long_path(crops_dir), exist_ok=True)
    rows = pending(cat)
    counts = {"photos": 0, "faces": 0, "errors": 0}
    for i, r in enumerate(rows, 1):
        if progress:
            progress("Finding faces", i, len(rows), r["orig_path"])
        try:
            img = _upright_image(r, derived_dir)
            found = faces.detect(m, img, min_face_px)
            emb = clip.image_embedding(m, img)
            sharp = sharpness(img)
            with cat.tx():
                for old in cat.q("SELECT crop_path FROM faces WHERE asset_id=? AND locked=0", (r["id"],)):
                    if old["crop_path"]:
                        remove_quietly(os.path.join(crops_dir, old["crop_path"]))
                # Locked faces (named by the user) are kept; everything else is redone.
                cat.db.execute("DELETE FROM faces WHERE asset_id=? AND locked=0", (r["id"],))
                kept = cat.q("SELECT x, y, w, h FROM faces WHERE asset_id=?", (r["id"],))
                for n, f in enumerate(found):
                    if any(_overlap(f.box, (k["x"], k["y"], k["w"], k["h"])) > 0.5 for k in kept):
                        continue   # already named by the user
                    name = f"{r['id']}_{n}.jpg"
                    atomic_write(os.path.join(crops_dir, name), lambda fh, c=f.crop: c.save(fh, "JPEG", quality=90),
                                 overwrite=True)
                    cat.db.execute(
                        "INSERT INTO faces(asset_id, x, y, w, h, score, embedding, crop_path, source) "
                        "VALUES (?,?,?,?,?,?,?,?, 'model')",
                        (r["id"], *f.box, f.score, f.embedding.astype("float32").tobytes(), name))
                    counts["faces"] += 1
                cat.db.execute("UPDATE hashes SET clip=?, sharpness=? WHERE asset_id=?",
                               (clip.to_blob(emb), sharp, r["id"]))
                cat.db.execute("UPDATE assets SET ai_version=? WHERE id=?", (AI_VERSION, r["id"]))
            counts["photos"] += 1
        except Exception:   # one odd photo never stops the run
            _log.exception("AI step failed for asset %s", r["id"])
            counts["errors"] += 1
            with cat.tx():
                cat.db.execute("UPDATE assets SET ai_version=? WHERE id=?", (AI_VERSION, r["id"]))
    return counts


def sharpness(img: Image.Image) -> float:
    """How sharp a photo is (variance of the Laplacian on a 1000px copy). Blurry burst frames score low."""
    import cv2
    import numpy as np
    g = np.asarray(img.convert("L"))
    scale = 1000 / max(g.shape)
    if scale < 1:
        g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def _overlap(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0

