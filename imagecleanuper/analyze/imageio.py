"""Open images, read camera info, dates in file names and video dates (section 8)."""

from __future__ import annotations

import datetime as dt
import os
import re
import struct
from dataclasses import dataclass

from PIL import Image, ImageOps

from ..paths import long_path

try:  # iPhone photos (.heic). Registered once at import (bug trap: forgetting this
    from pillow_heif import register_heif_opener  # sends every HEIC to Could Not Open).
    register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover
    HEIF_OK = False

Image.MAX_IMAGE_PIXELS = 400_000_000  # big scans are fine; stop only absurd "decompression bombs"

EXIF_IFD = 0x8769
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_DATETIME = 0x0132
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_SOFTWARE = 0x0131
TAG_ORIENTATION = 0x0112
ORIENTATION_DEGREES = {3: 180, 6: 270, 8: 90}  # PIL rotate() degrees (counter-clockwise)


class DecodeError(Exception):
    """The file can't be opened as an image. The message is shown to the user."""


@dataclass
class ImageInfo:
    exif_date: str | None = None       # YYYY-MM-DD
    exif_date_raw: str | None = None
    make: str | None = None
    model: str | None = None
    software: str | None = None
    orientation: int = 1
    width: int = 0
    height: int = 0
    exif_bytes: bytes | None = None


def _clean(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    v = str(v).replace("\x00", "").strip()
    return v or None


def parse_exif_date(raw: str | None) -> str | None:
    """EXIF dates look like '2019:07:04 15:30:12'. No time zone: kept as local, never converted."""
    if not raw:
        return None
    m = re.match(r"\s*(\d{4})[:\-/](\d{2})[:\-/](\d{2})", raw)
    if not m:
        return None
    try:
        d = dt.date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    if d.year < 1800 or d > dt.date.today() + dt.timedelta(days=2):
        return None  # 0000:00:00 and similar placeholders
    return d.isoformat()


def open_image(path: str) -> tuple[Image.Image, ImageInfo]:
    """Decode the image fully and read its metadata BEFORE any transform (bug trap)."""
    try:
        img = Image.open(long_path(path))
        img.load()
    except Exception as e:
        raise DecodeError(_decode_reason(path, e)) from e
    info = ImageInfo(width=img.width, height=img.height)
    try:
        exif = img.getexif()
    except Exception:
        exif = None
    if exif:
        sub = exif.get_ifd(EXIF_IFD) if EXIF_IFD in exif else {}
        raw = _clean(sub.get(TAG_DATETIME_ORIGINAL)) or _clean(sub.get(TAG_DATETIME_DIGITIZED)) \
            or _clean(exif.get(TAG_DATETIME))
        info.exif_date_raw = raw
        info.exif_date = parse_exif_date(raw)
        info.make = _clean(exif.get(TAG_MAKE))
        info.model = _clean(exif.get(TAG_MODEL))
        info.software = _clean(exif.get(TAG_SOFTWARE))
        try:
            info.orientation = int(exif.get(TAG_ORIENTATION, 1) or 1)
        except (TypeError, ValueError):
            info.orientation = 1
        try:
            info.exif_bytes = exif.tobytes()
        except Exception:
            info.exif_bytes = None
    return img, info


def _decode_reason(path: str, e: Exception) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".heic", ".heif") and not HEIF_OK:
        return "iPhone photo format (HEIC) support is missing."
    try:
        if os.path.getsize(long_path(path)) == 0:
            return "The file is empty (0 bytes)."
    except OSError:
        pass
    if "truncated" in str(e).lower():
        return "The file is cut short (probably an incomplete copy)."
    if "cannot identify" in str(e).lower():
        return "This isn't a photo format the app can read, or the file is damaged."
    return f"The file couldn't be opened ({type(e).__name__})."


def upright(img: Image.Image, orientation: int) -> tuple[Image.Image, int]:
    """Apply the camera's orientation physically. Returns (image, degrees applied)."""
    if orientation in (1, 0):
        return img, 0
    fixed = ImageOps.exif_transpose(img)
    return fixed, ORIENTATION_DEGREES.get(orientation, 0)


def to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGB",):
        return img
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


# ---- dates in file names -------------------------------------------------------
_NAME_PATTERNS = [
    # IMG_20190704_153012, PXL_20190704_..., VID_20190704, IMG-20190704-WA0001, 20190704_153012
    re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?:[_\-T ]?\d{6})?(?!\d)"),
    # 2019-07-04, 2019_07_04, 2019.07.04 (Screenshot_2019-07-04-15-30-12)
    re.compile(r"(?<!\d)((?:19|20)\d{2})[\-_.](\d{2})[\-_.](\d{2})(?!\d)"),
]


def parse_date_from_filename(name: str) -> str | None:
    stem = os.path.splitext(os.path.basename(name))[0]
    for pat in _NAME_PATTERNS:
        for m in pat.finditer(stem):
            try:
                d = dt.date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:
                continue
            if dt.date(1900, 1, 1) <= d <= dt.date.today() + dt.timedelta(days=2):
                return d.isoformat()
    return None


# ---- video dates (MP4 / MOV / M4V / 3GP 'mvhd' box) ----------------------------
_MAC_EPOCH = dt.datetime(1904, 1, 1)
_CONTAINERS = {b"moov", b"trak", b"mdia", b"udta"}


def read_video_date(path: str) -> str | None:
    """Creation date from the movie header. Returns None for formats without one."""
    try:
        with open(long_path(path), "rb") as f:
            size = os.fstat(f.fileno()).st_size
            return _scan_boxes(f, 0, size, depth=0)
    except (OSError, struct.error):
        return None


def _scan_boxes(f, start: int, end: int, depth: int) -> str | None:
    pos = start
    while pos + 8 <= end and depth < 6:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return None
        box_size, box_type = struct.unpack(">I4s", hdr)
        header = 8
        if box_size == 1:
            box_size = struct.unpack(">Q", f.read(8))[0]
            header = 16
        elif box_size == 0:
            box_size = end - pos
        if box_size < header:
            return None
        if box_type == b"mvhd":
            version = f.read(1)[0]
            f.read(3)
            secs = struct.unpack(">Q", f.read(8))[0] if version == 1 else struct.unpack(">I", f.read(4))[0]
            if secs == 0:
                return None
            d = (_MAC_EPOCH + dt.timedelta(seconds=secs)).date()
            if d.year < 1990 or d > dt.date.today() + dt.timedelta(days=2):
                return None
            return d.isoformat()
        if box_type in _CONTAINERS:
            found = _scan_boxes(f, pos + header, pos + box_size, depth + 1)
            if found:
                return found
        pos += box_size
    return None
