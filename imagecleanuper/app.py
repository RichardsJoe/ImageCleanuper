"""App start-up and the Run coordinator (pseudocode sections 4 and 5).

Phase 1 runs: discover → backup (gate) → learned-data backup.
Later phases add their steps to STEPS in pseudocode order.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import backup, compare, dates, discover, learned, log, plan, settings as settings_mod
from .analyze import run as analyze_run
from .catalog import Catalog, default_catalog_path
from .log import UserError
from .paths import app_dir, data_dir
from .system import keep_awake

Progress = Callable[[str, int, int], None]
_log = log.get("app")
ACTIVE_ASSETS = "SELECT a.id FROM assets a JOIN active_files f ON f.id = a.file_id"


@dataclass
class AppPaths:
    root: Path
    data: Path
    settings: Path
    catalog: Path
    models: Path

    @classmethod
    def default(cls, root: Path | None = None) -> "AppPaths":
        root = root or app_dir()
        data = data_dir(root)
        return cls(root=root, data=data, settings=root / "settings.ini",
                   catalog=default_catalog_path(data), models=data / "models")


@dataclass
class StartupState:
    settings: settings_mod.Settings
    problems: list[str]
    crashed_run: dict | None
    recovered_ops: int
    catalog_damaged: bool = False
    learned_backup_available: str | None = None


def startup(paths: AppPaths) -> StartupState:
    """Everything that must happen before the first screen (journal recovery first)."""
    s, problems = settings_mod.load(paths.settings)
    damaged = False
    if paths.catalog.exists():
        probe = Catalog(paths.catalog)
        damaged = not probe.integrity_ok()
        probe.close()
    if damaged:
        return StartupState(s, problems, None, 0, catalog_damaged=True,
                            learned_backup_available=learned.newest_learned_backup(s.backup) if s.backup else None)
    cat = Catalog(paths.catalog)
    try:
        recovered = recover_journal(cat)
        crashed = cat.mark_crashed_runs()
        crashed_run = dict(crashed[-1]) if crashed else None
    finally:
        cat.close()
    return StartupState(s, problems, crashed_run, recovered)


def recover_journal(cat: Catalog) -> int:
    """Finish or roll back operations a crash interrupted (rule R4)."""
    n = 0
    for entry_id, kind, payload in cat.open_journal_entries():
        if kind == "backup_part":
            backup.recover_backup_entry(cat, entry_id, payload)
        else:
            # Unknown kinds come from newer versions; leave them for that version.
            _log.warning("journal entry %s of kind %s left for later", entry_id, kind)
            continue
        n += 1
    return n


@dataclass
class RunResult:
    run_id: int
    state: str
    message: str = ""
    counts: dict = field(default_factory=dict)


class Cancelled(Exception):
    pass


class Runner:
    """Runs the pipeline on a worker thread. The UI only reads progress messages."""

    def __init__(self, paths: AppPaths, s: settings_mod.Settings, progress: Progress | None = None):
        self.paths = paths
        self.settings = s
        self.progress = progress or (lambda *_: None)
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def _prog(self, label: str, done: int, total: int, detail: str | None = None) -> None:
        self._check_cancel()
        if detail is None:
            self.progress(label, done, total)
        else:
            self.progress(label, done, total, detail)

    def run(self) -> RunResult:
        problems = settings_mod.blocking_problems(self.settings)
        if not self.settings.is_configured:
            problems.insert(0, "Choose your photo folders, library and backup folder first.")
        if problems:
            raise UserError("Some settings need fixing before a run.", " ".join(problems))
        cat = Catalog(self.paths.catalog)
        run_id = cat.start_run()
        counts: dict = {}
        try:
            with keep_awake():
                cat.set_run(run_id, last_step="discover")
                discover.sync_sources(cat, self.settings.masters)
                excluded = [self.settings.library, self.settings.backup, str(self.paths.root)]
                d = discover.discover_files(cat, run_id, excluded, self._prog)
                counts["discover"] = d.__dict__

                cat.set_run(run_id, last_step="backup")
                b = backup.backup_new_files(cat, self.settings.backup, run_id, self._prog)
                counts["backup"] = b.__dict__
                if not backup.all_backed_up(cat):  # the safety gate (R2)
                    raise UserError("Some photos couldn't be backed up, so nothing else was done.",
                                    "Check the log for the files involved and run again.")

                cat.set_run(run_id, last_step="analyze")
                counts["analyze"] = analyze_run.analyze_pending(cat, str(self.paths.data / "derived"), self._prog)

                cat.set_run(run_id, last_step="compare")
                self._prog("Comparing photos", 0, 0)
                counts["compare"] = compare.compare_all(cat)

                cat.set_run(run_id, last_step="dates")
                self._prog("Working out dates and holidays", 0, 0)
                counts["dates"] = dates.resolve_all(cat, self.settings)

                cat.set_run(run_id, last_step="plan")
                self._prog("Planning", 0, 0)
                counts["plan"] = plan.preview(cat, str(self.paths.data / "derived")).totals

                # Phase 3+ steps go here: people, learning, sync ...

                cat.set_run(run_id, last_step="learned_backup")
                learned.backup_learned_data(self.paths.catalog, self.paths.data, self.settings.backup,
                                            run_id, keep=self.settings.learned_versions_kept)
            cat.set_run(run_id, state="done", ended_at=time.time(), counts=counts, last_step="done")
            return RunResult(run_id, "done", counts=counts)
        except Cancelled:
            cat.set_run(run_id, state="stopped", ended_at=time.time(), counts=counts, message="Paused by you")
            return RunResult(run_id, "stopped", "Paused. Run again to continue where it stopped.", counts)
        except UserError as e:
            cat.set_run(run_id, state="stopped", ended_at=time.time(), counts=counts, message=str(e))
            raise
        except Exception:
            _log.exception("run %s failed", run_id)
            cat.set_run(run_id, state="crashed", ended_at=time.time(), counts=counts)
            raise
        finally:
            cat.close()


def summary(paths: AppPaths) -> dict:
    """Numbers for the Home screen."""
    if not paths.catalog.exists():
        return {"files": 0, "photos": 0, "videos": 0, "backed_up": 0, "parts": 0, "last_run": None}
    cat = Catalog(paths.catalog)
    try:
        last = cat.last_run()
        return {
            "files": cat.scalar("SELECT COUNT(*) FROM active_files"),
            "photos": cat.scalar("SELECT COUNT(*) FROM active_files WHERE kind='photo'"),
            "videos": cat.scalar("SELECT COUNT(*) FROM active_files WHERE kind='video'"),
            "backed_up": cat.scalar(
                "SELECT COUNT(DISTINCT e.sha256) FROM backup_entries e JOIN backup_parts p ON p.id=e.part_id "
                "WHERE p.verified=1 AND e.sha256 IN (SELECT sha256 FROM active_files)"),
            "parts": cat.scalar("SELECT COUNT(*) FROM backup_parts WHERE verified=1"),
            "last_run": dict(last) if last else None,
            "analyzed": cat.scalar("SELECT COUNT(*) FROM active_files WHERE status='analyzed'"),
            "could_not_open": cat.scalar("SELECT COUNT(*) FROM active_files WHERE status='could_not_open'"),
            "date_sources": {r[0]: r[1] for r in cat.q("SELECT source, COUNT(*) FROM dates WHERE asset_id IN (SELECT a.id FROM assets a "
                "JOIN active_files f ON f.id=a.file_id) GROUP BY source")},
            "year_only": cat.scalar("SELECT COUNT(*) FROM dates WHERE year_only=1 AND asset_id IN (SELECT a.id FROM assets a "
                "JOIN active_files f ON f.id=a.file_id)"),
            "holidays": {r[0]: r[1] for r in cat.q(
                f"SELECT holiday, COUNT(*) FROM holidays WHERE asset_id IN ({ACTIVE_ASSETS}) "
                "GROUP BY holiday ORDER BY COUNT(*) DESC")},
            "scans": cat.scalar("SELECT COUNT(DISTINCT a.file_id) FROM meta m JOIN assets a ON a.id=m.asset_id "
                                f"WHERE m.is_scan=1 AND a.id IN ({ACTIVE_ASSETS})"),
            "cutouts": cat.scalar(f"SELECT COUNT(*) FROM assets WHERE kind='cutout' AND id IN ({ACTIVE_ASSETS})"),
            "fixed": cat.scalar(f"SELECT COUNT(*) FROM fixes WHERE (trim_applied=1 OR exif_rotation<>0 OR rotation<>0) "
                                f"AND asset_id IN ({ACTIVE_ASSETS})"),
            "to_check": cat.scalar(f"SELECT COUNT(*) FROM fixes WHERE status='review' AND asset_id IN ({ACTIVE_ASSETS})"),
            "dup_groups": cat.scalar("SELECT COUNT(*) FROM dup_groups"),
            "dup_extras": cat.scalar("SELECT COUNT(*) FROM dup_members WHERE is_kept=0"),
        }
    finally:
        cat.close()
