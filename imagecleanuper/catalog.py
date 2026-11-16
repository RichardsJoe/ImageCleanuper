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
    # 2: phase 2 analysis
    """
    CREATE TABLE assets (
        id INTEGER PRIMARY KEY,
        file_id INTEGER NOT NULL REFERENCES files(id),
        kind TEXT NOT NULL,                           -- whole | cutout
        cutout_index INTEGER,
        derived_path TEXT,                            -- fixed/cut image in data\\derived, else NULL
        width INTEGER, height INTEGER,
        created_at REAL NOT NULL
    );
    CREATE INDEX assets_file ON assets(file_id);
    CREATE TABLE fixes (
        asset_id INTEGER PRIMARY KEY REFERENCES assets(id),
        exif_rotation INTEGER NOT NULL DEFAULT 0,     -- applied from camera info
        rotation INTEGER NOT NULL DEFAULT 0,          -- extra rotation (sideways check)
        rotation_conf REAL,
        trim_box TEXT,                                -- "left,top,right,bottom" or NULL
        trim_conf REAL,
        cut_conf REAL,                                -- cut-outs only
        status TEXT NOT NULL DEFAULT 'none'           -- none | auto | review | accepted | undone
    );
    CREATE TABLE meta (
        asset_id INTEGER PRIMARY KEY REFERENCES assets(id),
        exif_date TEXT, exif_date_raw TEXT,
        camera_make TEXT, camera_model TEXT, software TEXT,
        google_date TEXT, filename_date TEXT,
        is_scan INTEGER NOT NULL DEFAULT 0,
        scan_conf REAL, scan_reason TEXT
    );
    CREATE TABLE hashes (
        asset_id INTEGER PRIMARY KEY REFERENCES assets(id),
        phash TEXT, dhash TEXT,
        pixels INTEGER
    );
    CREATE TABLE dates (
        asset_id INTEGER PRIMARY KEY REFERENCES assets(id),
        date TEXT,                                    -- YYYY-MM-DD or NULL
        year_only INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL,                         -- exif|google|filename|similar|folder|unknown
        borrowed_from INTEGER,
        is_guess INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE holidays (
        asset_id INTEGER PRIMARY KEY REFERENCES assets(id),
        holiday TEXT,                                 -- key, e.g. christmas, or NULL
        source TEXT                                   -- date | folder
    );
    CREATE TABLE dup_groups (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL                            -- exact | near
    );
    CREATE TABLE dup_members (
        group_id INTEGER NOT NULL REFERENCES dup_groups(id),
        asset_id INTEGER NOT NULL REFERENCES assets(id),
        is_kept INTEGER NOT NULL DEFAULT 0,
        user_choice TEXT,                             -- keep_both | keep_this (from the review screen)
        PRIMARY KEY (group_id, asset_id)
    );
    CREATE INDEX dup_members_asset ON dup_members(asset_id);
    """,
    # 3: only folders currently in Settings count. Removing a folder keeps its history
    # (for Restore) but takes its photos out of every count, comparison and plan.
    """
    CREATE VIEW active_files AS
        SELECT f.* FROM files f JOIN sources s ON s.id = f.source_id
        WHERE s.enabled = 1 AND f.missing = 0;
    """,
    # 4: files analyzed by older rules are redone automatically (see analyze.run.ANALYSIS_VERSION)
    """
    ALTER TABLE files ADD COLUMN analysis_version INTEGER NOT NULL DEFAULT 0;
    DROP VIEW active_files;
    CREATE VIEW active_files AS
        SELECT f.* FROM files f JOIN sources s ON s.id = f.source_id
        WHERE s.enabled = 1 AND f.missing = 0;
    """,
    # 5: review answers; proposed trims are kept (trim_applied says whether they were used)
    """
    ALTER TABLE fixes ADD COLUMN trim_applied INTEGER NOT NULL DEFAULT 0;
    UPDATE fixes SET trim_applied = 1 WHERE trim_box IS NOT NULL;
    CREATE TABLE answers (
        id INTEGER PRIMARY KEY,
        asset_id INTEGER NOT NULL REFERENCES assets(id),
        question TEXT NOT NULL,        -- trim | cutout | rotation
        value TEXT NOT NULL,           -- looks_good | undo | rotate
        prev TEXT NOT NULL,            -- JSON of the fix before this answer (for Ctrl+Z)
        created_at REAL NOT NULL,
        undone INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX answers_asset ON answers(asset_id);
    """,
    # 6: a suggested rotation the app wasn't sure enough to apply (asked about in Review)
    """
    ALTER TABLE fixes ADD COLUMN rotation_suggested INTEGER NOT NULL DEFAULT 0;
    """,
    # 7: phase 3 - faces, people, strangers, CLIP fingerprints
    """
    ALTER TABLE assets ADD COLUMN ai_version INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE hashes ADD COLUMN clip BLOB;
    CREATE TABLE people (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        created_at REAL NOT NULL,
        merged_into INTEGER REFERENCES people(id)
    );
    CREATE TABLE strangers (
        id INTEGER PRIMARY KEY,
        label TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE faces (
        id INTEGER PRIMARY KEY,
        asset_id INTEGER NOT NULL REFERENCES assets(id),
        x INTEGER, y INTEGER, w INTEGER, h INTEGER,
        score REAL,
        embedding BLOB NOT NULL,
        crop_path TEXT,
        person_id INTEGER REFERENCES people(id),
        stranger_id INTEGER REFERENCES strangers(id),
        match_score REAL,
        source TEXT,                      -- model | user
        locked INTEGER NOT NULL DEFAULT 0,
        best INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX faces_asset ON faces(asset_id);
    CREATE INDEX faces_person ON faces(person_id);
    CREATE TABLE face_answers (
        id INTEGER PRIMARY KEY,
        action TEXT NOT NULL,             -- name | stranger
        face_ids TEXT NOT NULL,           -- JSON list
        prev TEXT NOT NULL,               -- JSON {face_id: [person_id, stranger_id, locked, source]}
        created_person INTEGER,
        created_stranger INTEGER,
        created_at REAL NOT NULL,
        undone INTEGER NOT NULL DEFAULT 0
    );
    """,
    # 8: pets, categories and junk (CLIP), and the answers that teach them
    """
    CREATE TABLE predictions (
        asset_id INTEGER NOT NULL REFERENCES assets(id),
        check_key TEXT NOT NULL,          -- pet | junk | cat:<key>
        prob REAL NOT NULL,               -- 0..1
        status TEXT NOT NULL,             -- yes | no | review
        PRIMARY KEY (asset_id, check_key)
    );
    CREATE TABLE content_answers (
        id INTEGER PRIMARY KEY,
        asset_id INTEGER REFERENCES assets(id),      -- NULL while the photo is being re-analyzed
        content_key TEXT NOT NULL,                   -- file fingerprint + cut-out position (survives re-analysis)
        check_key TEXT NOT NULL,
        value INTEGER NOT NULL,           -- 1 yes, 0 no
        best INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        undone INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX content_answers_asset ON content_answers(asset_id, check_key);
    """,
    # 9: what the user taught survives re-analysis (photo records are rebuilt then)
    """
    CREATE TABLE face_refs (
        id INTEGER PRIMARY KEY,
        person_id INTEGER REFERENCES people(id),
        stranger_id INTEGER REFERENCES strangers(id),
        embedding BLOB NOT NULL,
        best INTEGER NOT NULL DEFAULT 0,
        answer_id INTEGER,                -- face_answers row that created it (for Ctrl+Z)
        created_at REAL NOT NULL
    );
    """,
    # 10: burst shots + keep/remove choices for copies (survive re-analysis via content_key)
    """
    ALTER TABLE hashes ADD COLUMN sharpness REAL;
    CREATE TABLE copy_decisions (
        content_key TEXT PRIMARY KEY,     -- file fingerprint + cut-out position
        decision TEXT NOT NULL,           -- keep | remove
        batch INTEGER NOT NULL,           -- one click = one batch (Ctrl+Z undoes a whole batch)
        prev TEXT,                        -- previous decision, if any
        created_at REAL NOT NULL
    );
    """,
    # 11: Approve and sort: the library files this app made (rule R5) and each sort's summary
    """
    CREATE TABLE owned_files (
        rel_path TEXT PRIMARY KEY,        -- inside the library, "/" separators
        content_key TEXT NOT NULL,        -- which photo it is (see content.content_key) or readme:<folder>
        src_sha TEXT NOT NULL,            -- fingerprint of what was copied
        src_stat TEXT,                    -- "size:mtime_ns" of the source when copied (skip re-hashing)
        sha256 TEXT NOT NULL,             -- fingerprint of the library file as written
        size INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE syncs (
        id INTEGER PRIMARY KEY,
        started_at REAL NOT NULL,
        ended_at REAL,
        state TEXT NOT NULL,              -- running | done | stopped | crashed
        counts TEXT                       -- JSON
    );
    """,
    # 12: Finish. A finished file's original now lives only in the library (and the backup), so it
    # stays part of the plan even though it's gone from the photo folder or its folder was removed.
    """
    ALTER TABLE files ADD COLUMN finished_at REAL;
    DROP VIEW active_files;
    CREATE VIEW active_files AS
        SELECT f.* FROM files f JOIN sources s ON s.id = f.source_id
        WHERE (s.enabled = 1 AND f.missing = 0) OR f.finished_at IS NOT NULL;
    """,
    # 13: your corrections (Report M/Del, accuracy check) and the accuracy check itself
    """
    CREATE TABLE photo_labels (
        id INTEGER PRIMARY KEY,
        content_key TEXT NOT NULL,        -- which photo (survives re-analysis)
        kind TEXT NOT NULL,               -- holiday | year | not_person | person
        value TEXT NOT NULL,              -- holiday key ('' = none) | YYYY | person name
        source TEXT NOT NULL,             -- report | accuracy | library
        created_at REAL NOT NULL,
        undone INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX photo_labels_key ON photo_labels(content_key);
    CREATE TABLE qa_rounds (
        id INTEGER PRIMARY KEY,
        created_at REAL NOT NULL,
        closed_at REAL
    );
    CREATE TABLE qa_items (
        id INTEGER PRIMARY KEY,
        round_id INTEGER NOT NULL REFERENCES qa_rounds(id),
        position INTEGER NOT NULL,
        check_key TEXT NOT NULL,          -- person:<id> | pet | junk | cat:<key> | holiday | rotation | trim
        kind TEXT NOT NULL,               -- person | yesno
        content_key TEXT NOT NULL,
        asset_id INTEGER,                 -- NULL if the photo was re-analyzed since
        face_id INTEGER,
        question TEXT NOT NULL,
        options TEXT NOT NULL,            -- JSON list shown 1..n
        hidden TEXT NOT NULL,             -- the app's guess when sampled (never shown)
        answer TEXT,                      -- chosen option; NULL = not answered
        skipped INTEGER NOT NULL DEFAULT 0,
        answered_at REAL,
        undo TEXT                         -- JSON: what to restore if you press Ctrl+Z
    );
    CREATE INDEX qa_items_round ON qa_items(round_id, position);
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

