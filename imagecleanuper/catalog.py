"""The catalog: one SQLite database (pseudocode section 2).

Phase 1 creates the tables for sources, files, backups, runs and the journal.
Later phases add tables through new entries in MIGRATIONS (never by editing old ones).
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

MIGRATIONS: list[str] = [
    # 1: phase 1 foundation
    """
    CREATE TABLE sources (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT 'folder',          -- folder | google_import | add_new
        path TEXT NOT NULL,
        path_key TEXT NOT NULL UNIQUE,                -- normalized for comparisons
        label TEXT NOT NULL UNIQUE,                   -- stable prefix inside backup zips
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL
    );
    CREATE TABLE files (
        id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES sources(id),
        rel_path TEXT NOT NULL,
        rel_key TEXT NOT NULL,
        orig_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        kind TEXT NOT NULL,                           -- photo | video
        status TEXT NOT NULL DEFAULT 'new',           -- new | analyzed | could_not_open
        open_error TEXT,
        busy INTEGER NOT NULL DEFAULT 0,
        missing INTEGER NOT NULL DEFAULT 0,
        first_seen_run INTEGER,
        last_seen_run INTEGER,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        UNIQUE (source_id, rel_key)
    );
    CREATE INDEX files_sha ON files(sha256);
    CREATE TABLE backup_parts (
        id INTEGER PRIMARY KEY,
        zip_name TEXT NOT NULL UNIQUE,                -- relative to the backup folder
        kind TEXT NOT NULL DEFAULT 'photos',          -- photos | google_takeout
        created_run INTEGER,
        file_count INTEGER NOT NULL,
        bytes INTEGER NOT NULL,
        verified INTEGER NOT NULL DEFAULT 0,
        verified_at REAL
    );
    CREATE TABLE backup_entries (
        part_id INTEGER NOT NULL REFERENCES backup_parts(id),
        sha256 TEXT NOT NULL,
        arcname TEXT NOT NULL,
        size INTEGER NOT NULL,
        PRIMARY KEY (part_id, arcname)
    );
    CREATE INDEX backup_entries_sha ON backup_entries(sha256);
    CREATE TABLE runs (
        id INTEGER PRIMARY KEY,
        started_at REAL NOT NULL,
        ended_at REAL,
        state TEXT NOT NULL,                          -- running | done | crashed | stopped
        last_step TEXT,
        last_item_id INTEGER,
        message TEXT,
        counts TEXT
    );
    CREATE TABLE journal (
        id INTEGER PRIMARY KEY,
        op_kind TEXT NOT NULL,
        payload TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'intent',         -- intent | done | rolled_back
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
]


class Catalog:
    """Thin wrapper around a SQLite connection. Use one Catalog per thread."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self._migrate()

    # -- schema ---------------------------------------------------------
    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for i in range(version, len(MIGRATIONS)):
            # One script per migration: the tables and the version bump commit together,
            # so a crash mid-migration leaves the old version intact.
            self.db.executescript(f"BEGIN;\n{MIGRATIONS[i]}\nPRAGMA user_version={i + 1};\nCOMMIT;")

    @property
    def schema_version(self) -> int:
        return self.db.execute("PRAGMA user_version").fetchone()[0]

    # -- helpers --------------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.db
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def q(self, sql: str, args: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | dict = ()) -> sqlite3.Row | None:
        return self.db.execute(sql, args).fetchone()

    def scalar(self, sql: str, args: tuple | dict = ()) -> Any:
        row = self.db.execute(sql, args).fetchone()
        return None if row is None else row[0]

    def close(self) -> None:
        self.db.close()

    def integrity_ok(self) -> bool:
        try:
            return self.scalar("PRAGMA integrity_check") == "ok"
        except sqlite3.DatabaseError:
            return False

    # -- journal (rule R4) -------------------------------------------------
    def journal(self, op_kind: str, payload: dict) -> int:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO journal(op_kind, payload, state, created_at, updated_at) VALUES (?,?,?,?,?)",
            (op_kind, json.dumps(payload), "intent", now, now))
        self.db.commit()
        return cur.lastrowid

    def journal_set(self, entry_id: int, state: str) -> None:
        self.db.execute("UPDATE journal SET state=?, updated_at=? WHERE id=?", (state, time.time(), entry_id))
        self.db.commit()

    def open_journal_entries(self) -> list[tuple[int, str, dict]]:
        rows = self.q("SELECT id, op_kind, payload FROM journal WHERE state='intent' ORDER BY id")
        return [(r["id"], r["op_kind"], json.loads(r["payload"])) for r in rows]

    # -- runs ----------------------------------------------------------------
    def start_run(self) -> int:
        cur = self.db.execute("INSERT INTO runs(started_at, state) VALUES (?, 'running')", (time.time(),))
        self.db.commit()
        return cur.lastrowid

    def set_run(self, run_id: int, **fields: Any) -> None:
        if "counts" in fields and not isinstance(fields["counts"], str):
            fields["counts"] = json.dumps(fields["counts"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
        self.db.commit()

    def mark_crashed_runs(self) -> list[sqlite3.Row]:
        rows = self.q("SELECT * FROM runs WHERE state='running'")
        if rows:
            self.db.execute("UPDATE runs SET state='crashed', ended_at=? WHERE state='running'", (time.time(),))
            self.db.commit()
        return rows

    def last_run(self) -> sqlite3.Row | None:
        return self.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")


def default_catalog_path(data: Path) -> Path:
    return data / "catalog.db"

