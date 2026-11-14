"""Safe file operations (pseudocode section 0, rules R3 and R10)."""

from __future__ import annotations

import hashlib
import os
from typing import BinaryIO, Callable

from .paths import long_path

CHUNK = 4 * 1024 * 1024


class CopyVerifyError(Exception):
    """A copied file did not match its expected fingerprint."""


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(long_path(path), "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def sha256_stream(f: BinaryIO) -> str:
    h = hashlib.sha256()
    for block in iter(lambda: f.read(CHUNK), b""):
        h.update(block)
    return h.hexdigest()


def remove_quietly(path: str | os.PathLike) -> None:
    try:
        os.remove(long_path(path))
    except FileNotFoundError:
        pass


def atomic_write(dest: str | os.PathLike, write_fn: Callable[[BinaryIO], None],
                 overwrite: bool = False) -> None:
    """Write via ``dest.partial`` + fsync + rename, so ``dest`` is never half-written."""
    dest = os.fspath(dest)
    if not overwrite and os.path.exists(long_path(dest)):
        raise FileExistsError(dest)
    os.makedirs(long_path(os.path.dirname(os.path.abspath(dest))), exist_ok=True)
    tmp = dest + ".partial"
    try:
        with open(long_path(tmp), "wb") as f:
            write_fn(f)
            f.flush()
            os.fsync(f.fileno())
        if not overwrite and os.path.exists(long_path(dest)):
            raise FileExistsError(dest)
        os.replace(long_path(tmp), long_path(dest))
    except BaseException:
        remove_quietly(tmp)
        raise


def atomic_write_bytes(dest: str | os.PathLike, data: bytes, overwrite: bool = False) -> None:
    atomic_write(dest, lambda f: f.write(data), overwrite=overwrite)


def atomic_copy(src: str | os.PathLike, dest: str | os.PathLike,
                expected_sha: str | None = None, overwrite: bool = False) -> str:
    """Copy ``src`` to ``dest`` atomically and verify the result. Returns the fingerprint."""
    h = hashlib.sha256()

    def write(f: BinaryIO) -> None:
        with open(long_path(src), "rb") as s:
            for block in iter(lambda: s.read(CHUNK), b""):
                h.update(block)
                f.write(block)

    atomic_write(dest, write, overwrite=overwrite)
    written = sha256_file(dest)
    if written != h.hexdigest() or (expected_sha and written != expected_sha):
        remove_quietly(dest)
        raise CopyVerifyError(f"Copy of {src} did not match its fingerprint")
    return written
