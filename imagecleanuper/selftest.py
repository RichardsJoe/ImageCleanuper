"""--selftest: proves the packaged app can use its image libraries (run by the Windows build)."""

from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> int:
    # Never let an exception escape: a windowed .exe would show an error box and hang the build.
    try:
        return _run()
    except Exception:
        import traceback
        traceback.print_exc()
        return 1


def _run() -> int:
    from PIL import Image

    from .analyze import fix, hashes, imageio, scans
    with tempfile.TemporaryDirectory() as d:
        img = Image.new("RGB", (640, 480), (255, 255, 255))
        img.paste(Image.new("RGB", (400, 300), (30, 120, 200)), (120, 90))
        for name, fmt in (("t.jpg", "JPEG"), ("t.png", "PNG"), ("t.heic", "HEIF")):
            p = Path(d) / name
            img.save(p, fmt)
            opened, info = imageio.open_image(str(p))
            assert opened.size == (640, 480), name
        assert fix.find_border(img).box is not None
        scans.detect_scan(img, imageio.ImageInfo())
        hashes.phash(img), hashes.dhash(img)
    # AI models: present in the packaged app, fingerprints OK, and they actually run.
    from .ai import clip, faces, models
    m = models.get()
    if m is None:
        raise SystemExit("AI models missing from the package")
    problems = models.verify(m.folder)
    if problems:
        raise SystemExit("AI model problem: " + "; ".join(problems))
    faces.detect(m, img)
    e = clip.image_embedding(m, img)
    t = clip.text_embeddings(m, ["a photo of a dog"])
    assert e.shape == (512,) and t.shape == (1, 512)
    print("models ok, providers:", m.providers)
    return 0
