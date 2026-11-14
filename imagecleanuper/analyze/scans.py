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
    bg = np.median(border, axis=0)
    diff = np.abs(rgb.astype(np.int16) - bg).max(axis=2)
    spread = float(np.percentile(np.abs(border - bg).max(axis=1), 90))
    return diff <= max(18.0, spread + 10.0), bg


def detect_scan(img: Image.Image, info: ImageInfo) -> ScanCheck:
    text = " ".join(x for x in (info.make, info.model, info.software) if x).lower()
    if text and any(w in text for w in SCANNER_WORDS) and not any(w in text for w in CAMERA_WORDS):
        return ScanCheck(True, 0.99, f"made by a scanner ({text[:40]})")
    if info.make and any(w in info.make.lower() for w in CAMERA_WORDS):
        return ScanCheck(False, 0.95, "taken with a camera or phone")
    rgb, _ = _work_size(img, 600)
    mask, _bg = _background_mask(rgb)
    bg_share = float(mask.mean())
    border_bg = float(np.concatenate([mask[:5].ravel(), mask[-5:].ravel(),
                                      mask[:, :5].ravel(), mask[:, -5:].ravel()]).mean())
    # A visible scanner bed (plain margin all round the edge) is required. Missing camera info
    # alone proves nothing: Messenger/WhatsApp copies and screenshots have none either.
    if border_bg <= 0.97:
        return ScanCheck(False, 0.9, "no scanner bed visible")
    no_camera = not info.make and not info.exif_date
    score = 0.3
    if no_camera:
        score += 0.35
    if 0.08 < bg_share < 0.8:
        score += 0.15
    if len(find_photos_on_scan(img, min_area_share=0.03)) >= 2:
        score += 0.3
    score = min(score, 0.98)
    if score >= 0.9:
        return ScanCheck(True, score, "looks like prints on a scanner bed")
    if score >= 0.6:
        return ScanCheck(True, score, "may be a scanned print (please check)")
    return ScanCheck(False, 1 - score, "doesn't look like a scan")


def find_photos_on_scan(img: Image.Image, min_area_share: float = 0.05) -> list[Box]:
    """Find separate prints on one scan. Returns boxes in full-resolution coordinates."""
    rgb, scale = _work_size(img)
    mask, _ = _background_mask(rgb)
    fg = (~mask).astype(np.uint8) * 255
    k = max(3, (min(rgb.shape[:2]) // 150) | 1)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=2)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((k, k), np.uint8), iterations=1)
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
        corners = cv2.boxPoints(rect).astype(np.float32) / scale
        boxes.append(Box(corners, float(min(0.99, rectangularity))))
    if len(boxes) == 1 and cv2.contourArea(boxes[0].corners) * scale * scale > 0.85 * area_total:
        return []  # the whole scan is one print
    # Reading order: top to bottom, then left to right (stable names a, b, c).
    boxes.sort(key=lambda b: (round(b.corners[:, 1].min() / (img.height / 4)), b.corners[:, 0].min()))
    return boxes


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
