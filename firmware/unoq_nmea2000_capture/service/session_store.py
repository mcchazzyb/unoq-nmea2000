"""Session_Store: SQLite persistence for session metadata and decoded data,
with transactional commits and restart recovery (Requirement 12.2, 12.5;
Property 31).

Recovery contract: on restart, `recover_latest_session()` reads the latest
COMMITTED session and its decoded rows, giving the caller everything needed
to rebuild the Live_State_Cache to the state recorded at the last successful
write (Requirement 12.5) -- an uncommitted/partial write is never recovered,
since SQLite's own transaction boundary is the commit point.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    raw_log_path TEXT,
    status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS decoded_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(session_id),
    pgn INTEGER NOT NULL,
    source_address INTEGER NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_json TEXT NOT NULL,
    decoded_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decoded_session ON decoded_values(session_id);
"""


@dataclass
class WriteFailure:
    timestamp: float
    error: str


class SessionStore:
    """SQLite-backed Session_Store. Every public write method commits (or
    rolls back entirely) within one call -- there is no partially-committed
    state a crash between calls could leave behind.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.last_write_failure: WriteFailure | None = None
        self._active_session_id: int | None = None

    def start_session(self, raw_log_path: str | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO sessions (started_at, raw_log_path, status) VALUES (?, ?, 'open')",
            (time.time(), raw_log_path),
        )
        self._conn.commit()
        self._active_session_id = cur.lastrowid
        return self._active_session_id

    def close_session(self, session_id: int | None = None) -> None:
        sid = session_id if session_id is not None else self._active_session_id
        if sid is None:
            return
        self._conn.execute("UPDATE sessions SET status='closed' WHERE session_id=?", (sid,))
        self._conn.commit()

    def persist_decoded(
        self,
        pgn: int,
        source_address: int,
        entity_kind: str,
        entity: object,
        session_id: int | None = None,
    ) -> bool:
        """Persist one decoded entity transactionally. Returns True on
        success; on failure records a WriteFailure and leaves previously
        persisted rows unchanged (Requirement 12.8) -- never raises."""
        sid = session_id if session_id is not None else self._active_session_id
        if sid is None:
            sid = self.start_session()
        try:
            entity_json = json.dumps(_entity_to_dict(entity))
            self._conn.execute(
                "INSERT INTO decoded_values "
                "(session_id, pgn, source_address, entity_kind, entity_json, decoded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, pgn, source_address, entity_kind, entity_json, time.time()),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass  # connection already closed/broken; nothing to roll back
            self.last_write_failure = WriteFailure(timestamp=time.time(), error=str(exc))
            return False
        return True

    def recover_latest_session(self) -> dict | None:
        """Read the latest session (committed rows only -- SQLite guarantees
        an uncommitted write is never visible to a new connection/read) and
        every decoded row under it, for Live_State_Cache rebuild
        (Requirement 12.5). Returns None if no session exists."""
        row = self._conn.execute(
            "SELECT session_id, started_at, raw_log_path, status FROM sessions "
            "ORDER BY session_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        session_id, started_at, raw_log_path, status = row
        self._active_session_id = session_id

        decoded_rows = self._conn.execute(
            "SELECT pgn, source_address, entity_kind, entity_json, decoded_at "
            "FROM decoded_values WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        decoded = [
            {
                "pgn": pgn,
                "source_address": source_address,
                "entity_kind": entity_kind,
                "entity": json.loads(entity_json),
                "decoded_at": decoded_at,
            }
            for pgn, source_address, entity_kind, entity_json, decoded_at in decoded_rows
        ]
        return {
            "session_id": session_id,
            "started_at": started_at,
            "raw_log_path": raw_log_path,
            "status": status,
            "decoded": decoded,
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _entity_to_dict(entity: object) -> dict:
    """Serialize a dataclass-ish domain entity to a JSON-safe dict, replacing
    the UNSET sentinel with the literal string "UNSET" (JSON has no concept
    of UNSET; replay reconstructs it via `from_dict`). Handles both
    `__dict__`-based objects (dataclasses) and `__slots__`-based objects
    (e.g. protocol.decoders.identity.IsoName)."""
    from protocol.domain import UNSET

    if isinstance(entity, dict):
        raw = dict(entity)
    elif hasattr(entity, "__slots__"):
        raw = {name: getattr(entity, name) for name in entity.__slots__}
    elif hasattr(entity, "__dict__"):
        raw = dict(vars(entity))
    else:
        raw = {"value": entity}

    def _convert(value):
        if value is UNSET:
            return "UNSET"
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        if hasattr(value, "__slots__") or hasattr(value, "__dict__"):
            return _entity_to_dict(value)
        if isinstance(value, tuple):
            return list(value)
        return value

    return {k: _convert(v) for k, v in raw.items()}
