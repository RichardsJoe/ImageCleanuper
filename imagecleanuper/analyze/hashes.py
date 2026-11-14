"""Look-alike fingerprints (perceptual hashes) for near-duplicate detection (sections 8 and 9)."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def dhash(img: Image.Image, size: int = 8) -> int:
    g = np.asarray(img.convert("L").resize((size + 1, size), Image.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).ravel()
    return int("".join("1" if b else "0" for b in bits), 2)


def phash(img: Image.Image) -> int:
    g = np.asarray(img.convert("L").resize((32, 32), Image.LANCZOS), dtype=np.float32)
    d = cv2.dct(g)[:8, :8]
    med = np.median(d.ravel()[1:])  # skip the DC term
    bits = (d > med).ravel()
    return int("".join("1" if b else "0" for b in bits), 2)


def to_hex(v: int) -> str:
    return f"{v:016x}"


def from_hex(s: str) -> int:
    return int(s, 16)


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()
