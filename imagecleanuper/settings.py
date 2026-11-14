"""settings.ini next to the app (pseudocode section 3)."""

from __future__ import annotations

import configparser
import io
import os
from dataclasses import dataclass, field
from pathlib import Path

from .fileops import atomic_write_bytes
from .paths import is_inside, same_path

DEFAULT_HOLIDAY_WINDOWS = {
    "new_years_day": (1, 1),
    "valentines_day": (0, 0),
    "easter": (1, 1),
    "memorial_day": (1, 1),
    "independence_day": (1, 1),
    "labor_day": (1, 1),
    "halloween": (1, 0),
    "thanksgiving": (1, 2),
    "christmas": (2, 2),
    "new_years_eve": (0, 0),
}


@dataclass
class Settings:
    library: str = ""
    backup: str = r"C:\MasterPhotoBackup" if os.name == "nt" else ""
    masters: list[str] = field(default_factory=list)
    people_per_round: int = 20
    min_face_px: int = 40
    accuracy_start_items: int = 50
    accuracy_target: float = 0.95
    accuracy_min_per_check: int = 3
    accuracy_rounds_to_shrink: int = 2
    save_every_labels: int = 25
    learned_versions_kept: int = 10
    holiday_country: str = "US"
    holiday_windows: dict[str, tuple[int, int]] = field(
        default_factory=lambda: dict(DEFAULT_HOLIDAY_WINDOWS))
    claude_enabled: bool = False
    claude_model: str = "claude-sonnet-5-5"
    claude_monthly_limit_usd: float = 5.0

    @property
    def is_configured(self) -> bool:
        return bool(self.library and self.backup and self.masters)


def _parser() -> configparser.ConfigParser:
    # interpolation=None: a "%" in a folder name must not crash the app.
    return configparser.ConfigParser(interpolation=None)


def _int(cp, sec, key, default, lo, hi, problems):
    try:
        v = cp.getint(sec, key, fallback=default)
    except ValueError:
        problems.append(f"[{sec}] {key} is not a whole number; using {default}.")
        return default
    if not lo <= v <= hi:
        problems.append(f"[{sec}] {key} must be between {lo} and {hi}; using {default}.")
        return default
    return v


def _float(cp, sec, key, default, lo, hi, problems):
    try:
        v = cp.getfloat(sec, key, fallback=default)
    except ValueError:
        problems.append(f"[{sec}] {key} is not a number; using {default}.")
        return default
    if not lo <= v <= hi:
        problems.append(f"[{sec}] {key} must be between {lo} and {hi}; using {default}.")
        return default
    return v


def load(path: Path) -> tuple[Settings, list[str]]:
    """Read settings. Never raises for bad values: returns defaults plus a list of problems."""
    s = Settings()
    problems: list[str] = []
    if not path.exists():
        return s, problems
    cp = _parser()
    try:
        cp.read(path, encoding="utf-8")
    except configparser.Error as e:
        problems.append(f"settings.ini could not be read ({e}); using defaults.")
        return s, problems

    if cp.has_section("Folders"):
        f = cp["Folders"]
        s.library = f.get("library", s.library).strip()
        s.backup = f.get("backup", s.backup).strip()
        keys = sorted((k for k in f if k.startswith("master")),
                      key=lambda k: int(k[6:]) if k[6:].isdigit() else 10**6)
        s.masters = [f[k].strip() for k in keys if f[k].strip()]
    s.people_per_round = _int(cp, "Review", "people_per_round", s.people_per_round, 1, 100, problems)
    s.min_face_px = _int(cp, "Review", "min_face_px", s.min_face_px, 10, 500, problems)
    s.accuracy_start_items = _int(cp, "Accuracy", "start_items", s.accuracy_start_items, 5, 500, problems)
    s.accuracy_target = _float(cp, "Accuracy", "target", s.accuracy_target, 0.5, 1.0, problems)
    s.accuracy_min_per_check = _int(cp, "Accuracy", "min_per_check", s.accuracy_min_per_check, 1, 50, problems)
    s.accuracy_rounds_to_shrink = _int(cp, "Accuracy", "rounds_to_shrink", s.accuracy_rounds_to_shrink, 1, 10, problems)
    s.save_every_labels = _int(cp, "Learning", "save_every_labels", s.save_every_labels, 1, 1000, problems)
    s.learned_versions_kept = _int(cp, "Learning", "learned_versions_kept", s.learned_versions_kept, 1, 100, problems)
    if cp.has_section("Holidays"):
        s.holiday_country = cp["Holidays"].get("country", s.holiday_country).strip() or "US"
        for k, v in cp["Holidays"].items():
            if k.startswith("window."):
                name = k[len("window."):]
                try:
                    before, after = (int(x) for x in v.split(","))
                    if not (0 <= before <= 14 and 0 <= after <= 14):
                        raise ValueError
                    s.holiday_windows[name] = (before, after)
                except ValueError:
                    problems.append(f"[Holidays] {k} should look like '2,2' (days before, after).")
    if cp.has_section("Claude"):
        c = cp["Claude"]
        s.claude_enabled = c.get("enabled", "no").strip().lower() in ("yes", "true", "1", "on")
        s.claude_model = c.get("model", s.claude_model).strip() or s.claude_model
        s.claude_monthly_limit_usd = _float(cp, "Claude", "monthly_limit_usd",
                                            s.claude_monthly_limit_usd, 0.0, 10000.0, problems)
    problems.extend(validate(s))
    return s, problems


def validate(s: Settings) -> list[str]:
    """Folder rules. Returns plain-English problems; an empty list means OK."""
    p: list[str] = []
    for label, value in (("Sorted library", s.library), ("Backup folder", s.backup)):
        if value and not os.path.isabs(value):
            p.append(f"{label} must be a full path like C:\\SortedPhotos.")
    if s.library and s.backup and (is_inside(s.library, s.backup) or is_inside(s.backup, s.library)):
        p.append("The sorted library and the backup folder can't be inside each other.")
    seen: list[str] = []
    for m in s.masters:
        if any(same_path(m, x) for x in seen):
            p.append(f"{m} is listed twice.")
            continue
        seen.append(m)
        if not os.path.isabs(m):
            p.append(f"{m} must be a full path.")
            continue
        if not os.path.isdir(m):
            p.append(f"{m} can't be found right now (unplugged drive?). It will be skipped.")
        for label, value in (("sorted library", s.library), ("backup folder", s.backup)):
            if not value:
                continue
            if is_inside(value, m):
                p.append(f"The {label} ({value}) is inside a photo folder ({m}). "
                         f"Choose a {label} somewhere else, or the app would sort its own files.")
            elif is_inside(m, value):
                p.append(f"The photo folder {m} is inside the {label}. Choose a different folder.")
    for i, a in enumerate(s.masters):
        for b in s.masters[i + 1:]:
            if not same_path(a, b) and (is_inside(a, b) or is_inside(b, a)):
                p.append(f"{a} and {b} overlap. Keep only the top folder.")
    return p


def blocking_problems(s: Settings) -> list[str]:
    """Problems that must be fixed before a run (missing drives are only warnings)."""
    return [x for x in validate(s) if "can't be found right now" not in x]


def save(s: Settings, path: Path) -> None:
    cp = _parser()
    cp["Folders"] = {"library": s.library, "backup": s.backup}
    for i, m in enumerate(s.masters, 1):
        cp["Folders"][f"master{i}"] = m
    cp["Review"] = {"people_per_round": str(s.people_per_round), "min_face_px": str(s.min_face_px)}
    cp["Accuracy"] = {"start_items": str(s.accuracy_start_items), "target": str(s.accuracy_target),
                      "min_per_check": str(s.accuracy_min_per_check),
                      "rounds_to_shrink": str(s.accuracy_rounds_to_shrink)}
    cp["Learning"] = {"save_every_labels": str(s.save_every_labels),
                      "learned_versions_kept": str(s.learned_versions_kept)}
    cp["Holidays"] = {"country": s.holiday_country}
    for k, (b, a) in s.holiday_windows.items():
        cp["Holidays"][f"window.{k}"] = f"{b},{a}"
    cp["Claude"] = {"enabled": "yes" if s.claude_enabled else "no", "model": s.claude_model,
                    "monthly_limit_usd": str(s.claude_monthly_limit_usd)}
    buf = io.StringIO()
    buf.write("; ImageCleanuper settings. Edit in the app (Settings) or here in Notepad.\n")
    buf.write("; The Claude API key is NOT stored here; it is in Windows Credential Manager.\n\n")
    cp.write(buf)
    atomic_write_bytes(path, buf.getvalue().encode("utf-8"), overwrite=True)
