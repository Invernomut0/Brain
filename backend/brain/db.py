"""SQLite persistence layer (single connection guarded by a lock)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, type TEXT, agent TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS goals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, title TEXT, description TEXT,
  status TEXT DEFAULT 'pending', priority REAL DEFAULT 0.5, expected_success REAL,
  result TEXT, created REAL, updated REAL, attempts INTEGER DEFAULT 0, role TEXT DEFAULT 'executor');
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, text TEXT, tags TEXT,
  importance REAL DEFAULT 0.5, embedding TEXT);
CREATE TABLE IF NOT EXISTS journal(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS predictions(
  goal_id INTEGER PRIMARY KEY, p REAL, outcome REAL, ts REAL);
CREATE TABLE IF NOT EXISTS prompt_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, version INTEGER, sha TEXT, reason TEXT,
  created REAL, runs INTEGER DEFAULT 0, successes INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS tools(
  name TEXT PRIMARY KEY, description TEXT, params TEXT, status TEXT, created REAL,
  calls INTEGER DEFAULT 0, failures INTEGER DEFAULT 0, test_output TEXT);
CREATE TABLE IF NOT EXISTS agents(
  id TEXT PRIMARY KEY, role TEXT, parent TEXT, goal_id INTEGER, status TEXT,
  started REAL, ended REAL, steps INTEGER DEFAULT 0, result TEXT);
CREATE TABLE IF NOT EXISTS evolutions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, target TEXT, sha TEXT,
  status TEXT, reason TEXT, detail TEXT);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur.lastrowid or 0

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # --- key/value helpers -------------------------------------------------
    def wipe(self) -> None:
        """Delete every row of every table (schema is kept) and compact the file."""
        with self._lock:
            tables = [r[0] for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for t in tables:
                self._conn.execute(f'DELETE FROM "{t}"')
            try:
                self._conn.execute("DELETE FROM sqlite_sequence")
            except sqlite3.OperationalError:
                pass
            self._conn.commit()
            self._conn.execute("VACUUM")

    # --- key/value helpers -------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def now(self) -> float:
        return time.time()
