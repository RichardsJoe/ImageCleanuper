"""Find, check and load the AI models bundled with the app.

Search order: $IMAGECLEANUPER_MODELS, the packaged app's models folder, <repo>/models.
models.json (written by tools/export_models.py) lists each file's SHA-256; files are checked
once and the result remembered (by size and modification time) so start-up stays fast.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from functools import cached_property
from pathlib import Path

from .. import log
from ..fileops import sha256_file
from ..paths import app_dir

_log = log.get("ai")
FACE_DET = "face_detection_yunet_2023mar.onnx"
FACE_REC = "face_recognition_sface_2021dec.onnx"
CLIP_IMAGE = "clip_image.onnx"
CLIP_TEXT = "clip_text.onnx"
VOCAB = "bpe_simple_vocab_16e6.txt.gz"


def model_dir() -> Path | None:
    candidates = []
    if os.environ.get("IMAGECLEANUPER_MODELS"):
        candidates.append(Path(os.environ["IMAGECLEANUPER_MODELS"]))
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / "models")
    candidates.append(app_dir() / "models")
    for c in candidates:
        if (c / "models.json").exists():
            return c
    return None


def verify(folder: Path, cache_file: Path | None = None) -> list[str]:
    """Problems with the model files (empty = all good)."""
    manifest = json.loads((folder / "models.json").read_text())
    seen = {}
    if cache_file and cache_file.exists():
        try:
            seen = json.loads(cache_file.read_text())
        except ValueError:
            seen = {}
    problems, ok = [], {}
    for name, sha in manifest["files"].items():
        p = folder / name
        if not p.exists():
            problems.append(f"{name} is missing")
            continue
        key = f"{p.stat().st_size}:{p.stat().st_mtime_ns}"
        if seen.get(name) == [key, sha]:
            ok[name] = [key, sha]
            continue
        if sha256_file(p) != sha:
            problems.append(f"{name} is damaged (fingerprint doesn't match)")
        else:
            ok[name] = [key, sha]
    if cache_file and not problems:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(ok))
    return problems


class Models:
    """Lazily loaded models. Thread-safe: analysis runs on a worker thread."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.manifest = json.loads((folder / "models.json").read_text())
        self._lock = threading.Lock()

    @cached_property
    def providers(self) -> list[str]:
        import onnxruntime as ort
        available = ort.get_available_providers()
        chosen = [p for p in ("DmlExecutionProvider", "CPUExecutionProvider") if p in available]
        _log.info("onnxruntime providers: %s (available %s)", chosen, available)
        return chosen or ["CPUExecutionProvider"]

    def _session(self, name: str):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        try:
            return ort.InferenceSession(str(self.folder / name), opts, providers=self.providers)
        except Exception:  # GPU driver problems: fall back to the CPU, which always works
            _log.exception("GPU session failed for %s; using CPU", name)
            return ort.InferenceSession(str(self.folder / name), opts, providers=["CPUExecutionProvider"])

    @cached_property
    def clip_image(self):
        return self._session(CLIP_IMAGE)

    @cached_property
    def clip_text(self):
        return self._session(CLIP_TEXT)

    @cached_property
    def tokenizer(self):
        from .tokenizer import Tokenizer
        return Tokenizer(self.folder / VOCAB)

    def face_detector(self, w: int, h: int):
        import cv2
        with self._lock:
            if not hasattr(self, "_det"):
                self._det = cv2.FaceDetectorYN.create(str(self.folder / FACE_DET), "", (320, 320), 0.8, 0.3, 5000)
            self._det.setInputSize((w, h))
            return self._det

    @cached_property
    def face_recognizer(self):
        import cv2
        return cv2.FaceRecognizerSF.create(str(self.folder / FACE_REC), "")

    @property
    def using_gpu(self) -> bool:
        return self.providers[0] == "DmlExecutionProvider"


_models: Models | None = None


def get() -> Models | None:
    """The app's models, or None when they aren't installed (features that need them are skipped)."""
    global _models
    if _models is None:
        folder = model_dir()
        if folder is None:
            return None
        _models = Models(folder)
    return _models


def reset() -> None:
    global _models
    _models = None
