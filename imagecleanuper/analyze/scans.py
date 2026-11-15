"""Old-scan detection and multi-photo scan splitting (section 8)."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from .imageio import ImageInfo

SCANNER_WORDS = ("scan", "epson", "perfection", "canoscan", "canon lide", "lide", "fujitsu", "scansnap",
                 "brother", "hp scan", "hpscan", "officejet", "deskjet", "laserjet", "photosmart",
                 "plustek", "visioneer", "xerox", "kodak alaris", "vuescan", "naps2", "windows fax and scan",
                 "image capture", "photomyne")
CAMERA_WORDS = ("apple", "samsung", "google", "pixel", "nikon", "sony", "olympus", "panasonic", "fujifilm",
                "huawei", "xiaomi", "oneplus", "motorola", "lg electronics", "gopro", "dji", "leica", "ricoh")


@dataclass
class ScanCheck:
    is_scan: bool
    confidence: float
    reason: str


@dataclass
class Box:
    corners: np.ndarray   # 4x2 float32, in full-image pixel coordinates
    confidence: float
    angle: float = 0.0    # tilt in degrees, normalised to -45..45
    rect: float = 1.0     # how rectangular the outline is (1.0 = perfect)


def _work_size(img: Image.Image, longest: int = 1200) -> tuple[np.ndarray, float]:
    scale = min(1.0, longest / max(img.size))
    small = img.convert("RGB")
    if scale < 1.0:
        small = small.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
    return np.asarray(small), scale


def _background_mask(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pixels that look like the scanner bed (estimated from the outer border colour)."""
    h, w, _ = rgb.shape
    b = max(2, min(h, w) // 50)
    border = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3),
                             rgb[:, :b].reshape(-1, 3), rgb[:, -b:].reshape(-1, 3)]).astype(np.int16)
    # The lid is the largest plain, light area ANYWHERE on the scan. (Using only the outer edge
    # fails when prints are pushed into the corners: the "edge colour" is then mostly photo.)
    step = max(1, min(h, w) // 300)
    small = rgb[::step, ::step]
    gray = cv2.cvtColor(np.ascontiguousarray(small), cv2.COLOR_RGB2GRAY).astype(np.float32)
    mean = cv2.blur(gray, (7, 7))
    std = np.sqrt(np.maximum(cv2.blur(gray * gray, (7, 7)) - mean * mean, 0))
    flat_light = (std < 6) & (gray >= 200)
    if flat_light.mean() >= 0.04:
        samples = small[flat_light].astype(np.int16)
    else:
        samples = border  # no plain light area: fall back to the edge colour
    bg = np.median(samples, axis=0)
    diff = np.abs(rgb.astype(np.int16) - bg).max(axis=2)
    spread = float(np.percentile(np.abs(samples - bg).max(axis=1), 90))
    return diff <= max(18.0, spread + 10.0), bg


def _lid_mask(rgb: np.ndarray) -> np.ndarray:
    """The scanner lid, separated from light areas INSIDE photos (white walls, sky, Polaroid frames).

    Colour alone isn't enough: a white wall in a print can be lid-coloured. So the image is first
    cut along every edge (prints always have one where they meet the lid, even a faint one). Then
    the largest even area is the lid, and other areas join it only if they match its colour AND its
    evenness. Measured on real scans: lid texture ~0.3, light photo areas ~0.9 and 15-30 levels darker.
    """
    color_mask, bg = _background_mask(rgb)
    gray = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2GRAY)
    g = gray.astype(np.float32)
    mean = cv2.blur(g, (5, 5))
    std = np.sqrt(np.maximum(cv2.blur(g * g, (5, 5)) - mean * mean, 0))
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 8, 24)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    # The lid is also even pixel by pixel; grainy light areas (sky, walls) never count as lid.
    cand = (color_mask & (edges == 0) & (std < 2.0)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=4)
    if n <= 1:
        return color_mask
    order = sorted(range(1, n), key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    biggest = order[0]
    ref = labels == biggest
    ref_mean = float(g[ref].mean())
    ref_tex = float(np.median(std[ref]))
    lid = ref.copy()
    for i in order[1:]:
        area = stats[i, cv2.CC_STAT_AREA]
        if area < 0.002 * cand.size:
            break
        px = labels == i
        if abs(float(g[px].mean()) - ref_mean) <= 8 and float(np.median(std[px])) <= max(0.6, ref_tex * 2):
            lid |= px
    # Give back the thin edge band next to the lid so prints aren't padded with lid colour.
    grown = cv2.dilate(lid.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2).astype(bool)
    return grown & color_mask


def detect_scan(img: Image.Image, info: ImageInfo) -> ScanCheck:
    text = " ".join(x for x in (info.make, info.model, info.software) if x).lower()
    if text and any(w in text for w in SCANNER_WORDS) and not any(w in text for w in CAMERA_WORDS):
        return ScanCheck(True, 0.99, f"made by a scanner ({text[:40]})")
    if info.make and any(w in info.make.lower() for w in CAMERA_WORDS):
        return ScanCheck(False, 0.95, "taken with a camera or phone")
    no_camera = not info.make and not info.exif_date
    if not no_camera:
        # Camera info present (Messenger/WhatsApp strip it, scanners rarely write it): a photo.
        return ScanCheck(False, 0.9, "has camera info")
    rgb, _ = _work_size(img, 900)
    mask = _lid_mask(rgb)
    bed_share = float(mask.mean())
    if not 0.08 < bed_share < 0.98:
        return ScanCheck(False, 0.9, "no scanner lid visible")
    # Several cleanly rectangular prints on a plain light background: prints on a scanner,
    # even when they're pushed right into the corners.
    prints = [b for b in find_photos_on_scan(img, min_area_share=0.03) if b.rect >= 0.9]
    if len(prints) >= 2:
        return ScanCheck(True, 0.95, f"{len(prints)} prints on a scanner lid")
    # One print on a mostly empty page (often pushed into a corner).
    if len(prints) == 1 and bed_share >= 0.25:
        return ScanCheck(True, 0.9, "one print on a scanner lid")
    # One print with a plain margin all round: probably a single scanned print.
    border_bg = float(np.concatenate([mask[:5].ravel(), mask[-5:].ravel(),
                                      mask[:, :5].ravel(), mask[:, -5:].ravel()]).mean())
    if border_bg > 0.97:
        return ScanCheck(True, 0.8, "may be a scanned print (please check)")
    return ScanCheck(False, 0.8, "doesn't look like a scan")


def find_photos_on_scan(img: Image.Image, min_area_share: float = 0.05) -> list[Box]:
    """Find separate prints on one scan. Returns boxes in full-resolution coordinates."""
    rgb, scale = _work_size(img, 1800)
    mask = _lid_mask(rgb) | _blank_sheets(rgb)
    fg = (~mask).astype(np.uint8) * 255
    # Light clean-up only: heavy smoothing bridges the narrow gap between prints laid close together.
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)   # remove dust specks
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8), iterations=1)  # mend tiny nicks
    fg = _cut_background_strips(fg, mask)
    contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area_total = rgb.shape[0] * rgb.shape[1]
    boxes: list[Box] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area_share * area_total:
            continue
        rect = cv2.minAreaRect(c)
        (rw, rh) = rect[1]
        if rw < 10 or rh < 10:
            continue
        rectangularity = area / (rw * rh)
        if rectangularity < 0.80:
            continue  # not print-shaped (e.g. two touching prints or a torn edge)
        if _is_blank(rgb, rect):
            continue  # an empty card or the back of a print: nothing to keep
        corners = cv2.boxPoints(rect).astype(np.float32) / scale
        tilt = ((rect[2] + 45) % 90) - 45
        boxes.append(Box(corners, float(min(0.99, rectangularity)), float(tilt), float(rectangularity)))
    if len(boxes) == 1 and cv2.contourArea(boxes[0].corners) * scale * scale > 0.85 * area_total:
        return []  # the whole scan is one print
    _flag_suspicious_pairs(boxes)
    # Reading order: top to bottom, then left to right (stable names a, b, c).
    boxes.sort(key=lambda b: (round(b.corners[:, 1].min() / (img.height / 4)), b.corners[:, 0].min()))
    return boxes


AMBIGUOUS_CUT = 0.7


def _flag_suspicious_pairs(boxes: list[Box]) -> None:
    """Two 'prints' that line up perfectly with a narrow strip between them might really be ONE
    print with a washed-out band across it. The picture alone can't tell, so lower the
    confidence: these cuts go to review ("Was this scan cut out correctly?") instead of being trusted."""
    def bbox(b: Box):
        xs, ys = b.corners[:, 0], b.corners[:, 1]
        return xs.min(), ys.min(), xs.max(), ys.max()
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, c = bbox(boxes[i]), bbox(boxes[j])
            w = max(a[2] - a[0], c[2] - c[0])
            h = max(a[3] - a[1], c[3] - c[1])
            same_cols = abs(a[0] - c[0]) < 0.01 * w and abs(a[2] - c[2]) < 0.01 * w
            same_rows = abs(a[1] - c[1]) < 0.01 * h and abs(a[3] - c[3]) < 0.01 * h
            v_gap = max(a[1], c[1]) - min(a[3], c[3])
            h_gap = max(a[0], c[0]) - min(a[2], c[2])
            same_tilt = abs(boxes[i].angle - boxes[j].angle) < 0.5
            # Only very narrow gaps are suspicious: prints laid out in a tidy grid line up too,
            # with visible gaps between them (measured 15-40% on real scans).
            if same_tilt and ((same_cols and 0 <= v_gap < 0.1 * h) or (same_rows and 0 <= h_gap < 0.1 * w)):
                boxes[i].confidence = min(boxes[i].confidence, AMBIGUOUS_CUT)
                boxes[j].confidence = min(boxes[j].confidence, AMBIGUOUS_CUT)


def _cut_background_strips(fg: np.ndarray, bg_mask: np.ndarray, min_share: float = 0.97) -> np.ndarray:
    """Where a straight strip of scanner bed runs right through a blob (two prints almost
    touching), clear it so the blob splits in two."""
    out = fg.copy()
    n, labels, stats, _ = cv2.connectedComponentsWithStats((fg > 0).astype(np.uint8))
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 0.02 * fg.size or w < 20 or h < 20:
            continue
        region = bg_mask[y:y + h, x:x + w]
        cols = region.mean(axis=0) >= min_share   # columns that are (almost) all background
        rows = region.mean(axis=1) >= min_share
        margin_w, margin_h = int(w * 0.1), int(h * 0.1)   # ignore the blob's own ragged edges
        for j in np.nonzero(cols)[0]:
            if margin_w <= j < w - margin_w:
                out[y:y + h, x + j] = 0
        for j in np.nonzero(rows)[0]:
            if margin_h <= j < h - margin_h:
                out[y + j, x:x + w] = 0
    return out


def _blank_sheets(rgb: np.ndarray) -> np.ndarray:
    """Solid blank paper (an empty card, the back of a print) treated like the lid, so a photo
    lying on top of it still comes out on its own. A Polaroid's white frame is NOT blank paper:
    it's a ring with a picture inside, so it isn't 'solid'."""
    gray = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2GRAY)
    g = gray.astype(np.float32)
    mean = cv2.blur(g, (5, 5))
    std = np.sqrt(np.maximum(cv2.blur(g * g, (5, 5)) - mean * mean, 0))
    edges = cv2.dilate(cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 8, 24), np.ones((3, 3), np.uint8))
    flat_light = ((std < 2.0) & (gray > 215) & (edges == 0)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(flat_light, connectivity=4)
    out = np.zeros(gray.shape, bool)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= 0.015 * gray.size and area / float(w * h) >= 0.7:   # big and solid, not a ring
            out |= labels == i
    if out.any():
        out = cv2.dilate(out.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2).astype(bool)
    return out


def _is_blank(rgb: np.ndarray, rect) -> bool:
    """True for a plain light piece with no picture in it (blank card, back of a print)."""
    box = cv2.boxPoints(rect).astype(np.int32)
    mask = np.zeros(rgb.shape[:2], np.uint8)
    cv2.fillPoly(mask, [box], 1)
    mask = cv2.erode(mask, np.ones((9, 9), np.uint8))  # ignore the piece's own edge
    if mask.sum() < 100:
        return False
    gray = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2GRAY)[mask.astype(bool)].astype(np.float32)
    return float(gray.mean()) > 200 and float(np.percentile(gray, 99) - np.percentile(gray, 1)) < 30


def _order_corners(pts: np.ndarray) -> np.ndarray:
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32)


def straighten_and_crop(img: Image.Image, box: Box, inset: float = 0.006) -> Image.Image:
    """Cut one print out of the scan and rotate it straight."""
    src = _order_corners(box.corners)
    w = int(round(max(np.linalg.norm(src[0] - src[1]), np.linalg.norm(src[2] - src[3]))))
    h = int(round(max(np.linalg.norm(src[0] - src[3]), np.linalg.norm(src[1] - src[2]))))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(np.asarray(img.convert("RGB")), m, (w, h), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)
    dx, dy = int(w * inset), int(h * inset)  # trim the slightly soft edge of the print
    if w - 2 * dx > 10 and h - 2 * dy > 10:
        out = out[dy:h - dy, dx:w - dx]
    return Image.fromarray(out)
