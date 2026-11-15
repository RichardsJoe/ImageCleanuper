"""Border trim and rotation (section 8). Cautious: only act when clearly right."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

AUTO_TRIM = 0.9


@dataclass
class Trim:
    box: tuple[int, int, int, int] | None   # left, top, right, bottom (crop box), None = no border
    confidence: float


def find_border(img: Image.Image, min_border_share: float = 0.02) -> Trim:
    """Find uniform white or black margins. A dark photo edge is NOT a border unless it's
    near-uniform and clearly different from what's inside it (bug trap: night photos)."""
    small = img.convert("L")
    scale = min(1.0, 800 / max(small.size))
    if scale < 1.0:
        small = small.resize((max(1, int(small.width * scale)), max(1, int(small.height * scale))))
    a = np.asarray(small, dtype=np.int16)
    h, w = a.shape

    def edge_run(lines) -> tuple[int, float]:
        """How many outer lines are uniform border; returns (count, border level)."""
        first = lines[0]
        level = float(np.median(first))
        if not (level >= 225 or level <= 30):
            return 0, level
        n = 0
        for line in lines:
            if np.percentile(np.abs(line - level), 95) <= 12:
                n += 1
            else:
                break
        return n, level

    top, lt = edge_run([a[i] for i in range(h // 3)])
    bottom, lb = edge_run([a[h - 1 - i] for i in range(h // 3)])
    left, ll = edge_run([a[:, i] for i in range(w // 3)])
    right, lr = edge_run([a[:, w - 1 - i] for i in range(w // 3)])
    sides = [(top, lt, h), (bottom, lb, h), (left, ll, w), (right, lr, w)]
    real = [(n, lev) for n, lev, size in sides if n >= max(2, size * min_border_share)]
    if not real:
        return Trim(None, 0.0)
    levels = [lev for _, lev in real]
    if max(levels) - min(levels) > 40:
        return Trim(None, 0.0)  # mixed white and black edges: probably part of the photo
    inner = a[top:h - bottom or h, left:w - right or w]
    if inner.size == 0 or inner.shape[0] < h * 0.3 or inner.shape[1] < w * 0.3:
        return Trim(None, 0.0)  # would remove most of the picture
    # Safety: a real border leaves a photo behind. If much of what's left is still plain
    # border-coloured background, this is a print on an empty page, not a border to trim.
    level = float(np.mean(levels))
    if float((np.abs(inner - level) <= 12).mean()) > 0.3:
        return Trim(None, 0.0)
    contrast = abs(float(np.median(inner)) - level)
    confidence = min(0.99, 0.5 + contrast / 100 + 0.1 * len(real))
    inv = 1 / scale
    box = (int(left * inv), int(top * inv), int(img.width - right * inv), int(img.height - bottom * inv))
    return Trim(box, confidence)


def apply_trim(img: Image.Image, trim: Trim) -> Image.Image:
    return img.crop(trim.box) if trim.box else img


AUTO_ROTATE = 0.95      # confidence returned for a clear answer (rotate automatically)
SUGGEST_ROTATE = 0.7    # a possible answer (ask in Review: "Is this the right way up?")
_CASCADES = None


def _cascades():
    global _CASCADES
    if _CASCADES is None:
        import cv2
        base = cv2.data.haarcascades
        _CASCADES = [(cv2.CascadeClassifier(base + name), weight) for name, weight in
                     (("haarcascade_frontalface_default.xml", 1.0), ("haarcascade_frontalface_alt2.xml", 1.0),
                      ("haarcascade_profileface.xml", 0.6))]
    return _CASCADES


def face_score(img: Image.Image) -> float:
    """How strongly upright faces are found (0 = none). Face detectors only find upright faces,
    so trying all four turns shows which way is up."""
    import cv2
    g = np.asarray(img.convert("L"))
    scale = 640 / max(g.shape)
    g = cv2.equalizeHist(cv2.resize(g, None, fx=scale, fy=scale))
    total = 0.0
    for cascade, weight in _cascades():
        boxes, _levels, weights = cascade.detectMultiScale3(g, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20),
                                                            outputRejectLevels=True)
        if len(boxes):
            total += weight * float(np.clip(weights, 0, 10).sum())
    return total


def predict_sideways(img: Image.Image) -> tuple[int, float]:
    """Which way is up, judged by faces (for scans and photos without camera rotation info).
    Returns (counter-clockwise degrees to rotate, confidence). Thresholds measured on real scans:
    clear wins rotate automatically, close calls are asked about, no faces = leave it alone."""
    scores = {a: face_score(img.rotate(a, expand=True)) for a in (0, 90, 180, 270)}
    best = max(scores, key=scores.get)
    second = max(v for a, v in scores.items() if a != best)
    if best == 0 or scores[best] < 5:
        return 0, 0.0
    if scores[best] >= 8 and scores[best] >= 2.5 * max(second, 0.5):
        return best, AUTO_ROTATE
    return best, SUGGEST_ROTATE
