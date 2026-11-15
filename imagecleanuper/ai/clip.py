"""CLIP image and text fingerprints (embeddings): 512 numbers per photo or phrase."""

from __future__ import annotations

import numpy as np
from PIL import Image

from .models import Models

MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)


def preprocess(img: Image.Image) -> np.ndarray:
    """Resize the short side to 224 (bicubic), centre-crop 224x224, normalise. CLIP's own recipe."""
    img = img.convert("RGB")
    s = 224 / min(img.size)
    img = img.resize((max(224, round(img.width * s)), max(224, round(img.height * s))), Image.BICUBIC)
    left, top = (img.width - 224) // 2, (img.height - 224) // 2
    img = img.crop((left, top, left + 224, top + 224))
    x = (np.asarray(img, np.float32) / 255.0 - MEAN) / STD
    return x.transpose(2, 0, 1)


def _norm(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-8)


def image_embedding(models: Models, img: Image.Image) -> np.ndarray:
    out = models.clip_image.run(None, {"pixels": preprocess(img)[None]})[0][0]
    return _norm(out.astype(np.float32))


def image_embeddings(models: Models, imgs: list[Image.Image]) -> np.ndarray:
    batch = np.stack([preprocess(i) for i in imgs])
    return _norm(models.clip_image.run(None, {"pixels": batch})[0].astype(np.float32))


_text_cache: dict[str, np.ndarray] = {}


def text_embeddings(models: Models, phrases: list[str]) -> np.ndarray:
    todo = [p for p in phrases if p not in _text_cache]
    if todo:
        tokens = np.array(models.tokenizer.encode_batch(todo), np.int64)
        out = _norm(models.clip_text.run(None, {"tokens": tokens})[0].astype(np.float32))
        for p, e in zip(todo, out):
            _text_cache[p] = e
    return np.stack([_text_cache[p] for p in phrases])


def to_blob(v: np.ndarray) -> bytes:
    return v.astype(np.float16).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, np.float16).astype(np.float32)
