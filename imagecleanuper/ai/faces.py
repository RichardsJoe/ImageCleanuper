"""Find faces and make a face fingerprint (128 numbers) for each, using YuNet + SFace (OpenCV)."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from .models import Models

MAX_SIDE = 1280   # faces are found on a downsized copy; boxes are scaled back


@dataclass
class Face:
    box: tuple[int, int, int, int]        # x, y, w, h in the photo's (upright) pixels
    score: float
    embedding: np.ndarray                 # 128 floats, length 1
    crop: Image.Image                     # 112x112 aligned face, for "Who is this?"


def detect(models: Models, img: Image.Image, min_face_px: int = 40) -> list[Face]:
    rgb = np.asarray(img.convert("RGB"))
    scale = min(1.0, MAX_SIDE / max(rgb.shape[:2]))
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
    det = models.face_detector(bgr.shape[1], bgr.shape[0])
    _, found = det.detect(bgr)
    faces: list[Face] = []
    if found is None:
        return faces
    rec = models.face_recognizer
    for row in found:
        x, y, w, h = row[:4]
        if max(w, h) / scale < min_face_px:
            continue   # background faces are too small to recognise reliably
        aligned = rec.alignCrop(bgr, row)
        feat = rec.feature(aligned).flatten().astype(np.float32)
        feat /= max(np.linalg.norm(feat), 1e-8)
        box = tuple(int(round(v / scale)) for v in (x, y, w, h))
        faces.append(Face(box, float(row[-1]), feat, Image.fromarray(cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB))))
    return faces


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity. SFace's published threshold for 'same person' is about 0.363."""
    return float(np.dot(a, b))
