"""Windows helpers: single instance, keep the PC awake, free space."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .paths import IS_WINDOWS

ERROR_ALREADY_EXISTS = 183
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class SingleInstance:
    """Only one copy of the app may run, or two runs could corrupt the catalog."""

    def __init__(self, name: str = "ImageCleanuper", lock_dir: Path | None = None):
        self.name = name
        self.lock_dir = lock_dir
        self._handle = None
        self._file = None

    def acquire(self) -> bool:
        if IS_WINDOWS:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            self._handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
            if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(self._handle)
                self._handle = None
                return False
            return True
        import fcntl
        d = self.lock_dir or Path.home()
        d.mkdir(parents=True, exist_ok=True)
        self._file = open(d / f".{self.name}.lock", "w")
        try:
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._file.close()
            self._file = None
            return False

    def release(self) -> None:
        if self._handle is not None:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._file is not None:
            self._file.close()
            self._file = None


@contextmanager
def keep_awake() -> Iterator[None]:
    """Stop Windows from sleeping while a run is working."""
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        if IS_WINDOWS:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def open_folder(path: str) -> None:
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
