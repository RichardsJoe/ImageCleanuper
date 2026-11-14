"""Generate test photos (no real photos in the repo)."""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

EXIF_IFD = 0x8769


def scene(seed: int, size=(800, 600), shift: int = 0) -> Image.Image:
    """A colourful, detailed 'photo' that hashes distinctly per seed."""
    rnd = random.Random(seed)
    img = Image.new("RGB", size, tuple(rnd.randint(40, 200) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        r = rnd.randint(10, 120)
        col = tuple(rnd.randint(0, 255) for _ in range(3))
        x += shift
        if rnd.random() < 0.5:
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        else:
            d.rectangle([x - r, y - r // 2, x + r, y + r // 2], fill=col)
    noise = np.random.default_rng(seed).integers(0, 25, (size[1], size[0], 3), dtype=np.uint8)
    arr = np.clip(np.asarray(img, dtype=np.int16) + noise - 12, 0, 255).astype(np.uint8)
    return Image.fromarray(arr).filter(ImageFilter.GaussianBlur(0.6))


def save_jpeg(img: Image.Image, path: Path, date: str | None = None, make: str | None = None,
              model: str | None = None, software: str | None = None, orientation: int | None = None,
              quality: int = 92) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if make:
        exif[0x010F] = make
    if model:
        exif[0x0110] = model
    if software:
        exif[0x0131] = software
    if orientation:
        exif[0x0112] = orientation
    if date:
        exif.get_ifd(EXIF_IFD)[0x9003] = date
    img.save(path, "JPEG", quality=quality, exif=exif.tobytes())
    return path


def scanner_bed(prints: list[Image.Image], angles: list[float], size=(2400, 1800)) -> Image.Image:
    """Several prints lying on a white scanner bed, slightly crooked."""
    bed = Image.new("RGB", size, (250, 250, 248))
    spots = [(150, 150), (1300, 200), (400, 1000)]
    for p, a, (x, y) in zip(prints, angles, spots):
        framed = Image.new("RGB", (p.width + 24, p.height + 24), (235, 232, 225))  # print border
        framed.paste(p, (12, 12))
        rot = framed.rotate(a, expand=True, fillcolor=(250, 250, 248))
        bed.paste(rot, (x, y))
    return bed
