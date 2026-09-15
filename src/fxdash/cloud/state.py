"""Conditional private-state interface and a disk-backed, local-only simulator.

SQLite is for offline fault tests, not a shared GitHub runner state store. A real
adapter must provide authoritative reads and atomic compare-and-swap semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Protocol
import uuid

MAX_BYTES = 128 * 1024 * 1024


class StateError(RuntimeError):
    """Fixed diagnostic codes only; no provider response or private payload."""


class Conflict(StateError):
    pass


@dataclass(frozen=True)
class Record:
    version: str
    body: bytes


class Store(Protocol):
    def read(self, key: str) -> Record | None: ...
    def compare_and_swap(self, key: str, expected: str | None, body: bytes) -> Record: ...


def valid_key(key):
    if (not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9/_.-]{0,199}", key)
            or any(p in {"", ".", ".."} for p in key.split("/"))):
        raise StateError("invalid_state_key")


class SQLiteStore:
    def __init__(self, path, *, create=False):
        self.path = Path(path).absolute()
        if (self.path.suffix != ".sqlite3" or self.path.is_symlink()
                or self.path.resolve() != self.path):
            raise StateError("invalid_simulator_path")
        if create:
            # Exclusive creation: a missing/restored store is never reset on retry.
            try:
                with self.path.open("xb"):
                    pass
                with closing(self._connect()) as db, db:
                    db.execute("CREATE TABLE objects (name TEXT PRIMARY KEY, version TEXT NOT NULL, body BLOB NOT NULL)")
            except (OSError, sqlite3.Error):
                raise StateError("simulator_initialization_failed") from None
        try:
            with closing(self._connect()) as db, db:
                db.execute("SELECT name, version, body FROM objects LIMIT 0")
        except sqlite3.Error:
            raise StateError("simulator_unavailable") from None

    def _connect(self):
        # mode=rw refuses to silently create an empty replacement after data loss.
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def read(self, key):
        valid_key(key)
        try:
            with closing(self._connect()) as db, db:
                row = db.execute("SELECT version, body FROM objects WHERE name=?", (key,)).fetchone()
            return Record(row[0], bytes(row[1])) if row else None
        except sqlite3.Error:
            raise StateError("state_read_failed") from None

    def compare_and_swap(self, key, expected, body):
        valid_key(key)
        if (not isinstance(body, bytes) or len(body) > MAX_BYTES
                or (expected is not None and (not isinstance(expected, str)
                    or not re.fullmatch(r"[0-9a-f]{32}", expected)))):
            raise StateError("invalid_state_write")
        result = Record(uuid.uuid4().hex, body)
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute("SELECT version FROM objects WHERE name=?", (key,)).fetchone()
                if (old[0] if old else None) != expected:
                    raise Conflict("state_conflict")
                db.execute("INSERT INTO objects VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET version=excluded.version, body=excluded.body",
                           (key, result.version, result.body))
            return result
        except sqlite3.Error:
            raise StateError("state_write_failed") from None


def save_artifact(store: Store, body: bytes):
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise StateError("invalid_artifact")
    digest = hashlib.sha256(body).hexdigest()
    key = "artifacts/" + digest
    existing = store.read(key)
    if existing is None:
        try:
            store.compare_and_swap(key, None, body)
        except Conflict:
            existing = store.read(key)
            if existing is None:
                raise StateError("artifact_missing") from None
    if existing is not None and existing.body != body:
        raise StateError("artifact_corrupt")
    return digest


def read_artifact(store: Store, digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise StateError("invalid_artifact_hash")
    row = store.read("artifacts/" + digest)
    if row is None or hashlib.sha256(row.body).hexdigest() != digest:
        raise StateError("artifact_missing_or_corrupt")
    return row.body
