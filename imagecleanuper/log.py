"""Logging (pseudocode section 25)."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_configured = False


def setup_logging(data: Path, console: bool = False) -> logging.Logger:
    global _configured
    logger = logging.getLogger("imagecleanuper")
    if _configured:
        return logger
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        logs / "imagecleanuper.log", when="midnight", backupCount=30, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    logger.setLevel(logging.INFO)
    _configured = True
    return logger


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"imagecleanuper.{name}")


class UserError(Exception):
    """An error with a plain-English message and what to do about it."""

    def __init__(self, message: str, what_to_do: str = "", details: str = ""):
        super().__init__(message)
        self.message = message
        self.what_to_do = what_to_do
        self.details = details

    def __str__(self) -> str:
        return f"{self.message} {self.what_to_do}".strip()
