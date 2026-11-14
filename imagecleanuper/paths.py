"""Path helpers (pseudocode section 0).

Windows limits ordinary paths to 260 characters. Deeply nested photo folders
often go past that, so every file-system call goes through ``long_path``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
_LONG_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\?\\UNC\\"


def app_dir() -> Path:
    """Folder holding the app (next to the .exe when frozen, else the repo root)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir(base: Path | None = None) -> Path:
    d = (base or app_dir()) / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def strip_long(p: str | os.PathLike) -> str:
    """Remove the Windows long-path prefix for display and storage."""
    s = os.fspath(p)
    if s.startswith(_UNC_PREFIX):
        return "\\\\" + s[len(_UNC_PREFIX):]
    if s.startswith(_LONG_PREFIX):
        return s[len(_LONG_PREFIX):]
    return s


def long_path(p: str | os.PathLike) -> str:
    """Absolute path that works past 260 characters on Windows (no-op elsewhere)."""
    s = os.fspath(p)
    if not IS_WINDOWS:
        return os.path.abspath(s)
    if s.startswith(_LONG_PREFIX):
        return s
    s = os.path.abspath(s)
    if s.startswith("\\\\"):
        return _UNC_PREFIX + s[2:]
    return _LONG_PREFIX + s


def norm(p: str | os.PathLike) -> str:
    """Normalized absolute path for comparisons (case-insensitive on Windows)."""
    s = os.path.normcase(os.path.abspath(strip_long(p)))
    if len(s) > 1:
        s = s.rstrip("\\/")
    if IS_WINDOWS and len(s) == 2 and s[1] == ":":
        s += "\\"
    return s


def is_inside(child: str | os.PathLike, parent: str | os.PathLike) -> bool:
    """True if ``child`` is ``parent`` or anywhere below it."""
    c, p = norm(child), norm(parent)
    if c == p:
        return True
    prefix = p if p.endswith(os.sep) else p + os.sep
    return c.startswith(prefix)


def same_path(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    return norm(a) == norm(b)
