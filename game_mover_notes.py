"""SQLite-backed notes shared by Game Mover clients."""

import datetime
import os
import re
import sqlite3
from contextlib import contextmanager


TARGET_TYPES = frozenset({"server", "game", "launcher"})
TARGET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_NOTES_PER_TARGET = 32
MAX_TITLE_LENGTH = 120
MAX_PLATFORM_LENGTH = 120
MAX_BODY_LENGTH = 8000


class NotesError(ValueError):
    """Raised for invalid note targets or content."""


def validate_target(target_type, target_id):
    target_type = str(target_type).strip().lower()
    target_id = str(target_id).strip()
    if target_type not in TARGET_TYPES or not TARGET_ID_RE.fullmatch(target_id):
        raise NotesError("Neplatný cíl poznámek")
    return target_type, target_id


def normalize_notes(raw_notes):
    if not isinstance(raw_notes, list) or len(raw_notes) > MAX_NOTES_PER_TARGET:
        raise NotesError("Neplatný seznam poznámek")
    notes = []
    for raw_note in raw_notes:
        if not isinstance(raw_note, dict):
            raise NotesError("Neplatná poznámka")
        title = str(raw_note.get("title", "")).strip()
        platform = str(raw_note.get("platform", "Obecné")).strip()
        body = str(raw_note.get("body", "")).strip()
        if (
            not title or not body
            or len(title) > MAX_TITLE_LENGTH
            or not platform or len(platform) > MAX_PLATFORM_LENGTH
            or len(body) > MAX_BODY_LENGTH
        ):
            raise NotesError("Poznámka nemá platný nadpis nebo text")
        notes.append({"title": title, "platform": platform, "body": body})
    return notes


def _connect(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            title TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'Obecné',
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (target_type, target_id, sort_order)
        )
        """
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(notes)").fetchall()
    }
    if "platform" not in columns:
        connection.execute(
            "ALTER TABLE notes ADD COLUMN platform TEXT NOT NULL DEFAULT 'Obecné'"
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS notes_target_idx "
        "ON notes (target_type, target_id, sort_order)"
    )
    return connection


@contextmanager
def _database(path):
    connection = _connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_notes_database(path):
    """Create or migrate the notes database without changing stored notes."""
    with _database(path):
        pass
    return path


def list_notes(path, target_type, target_id):
    target_type, target_id = validate_target(target_type, target_id)
    with _database(path) as connection:
        rows = connection.execute(
            "SELECT id, title, platform, body, created_at, updated_at FROM notes "
            "WHERE target_type = ? AND target_id = ? ORDER BY sort_order, id",
            (target_type, target_id),
        ).fetchall()
    return [dict(row) for row in rows]


def list_all_notes(path):
    with _database(path) as connection:
        rows = connection.execute(
            "SELECT id, target_type, target_id, title, platform, body, "
            "created_at, updated_at FROM notes "
            "ORDER BY target_type, target_id, sort_order, id"
        ).fetchall()
    return [dict(row) for row in rows]


def replace_notes(path, target_type, target_id, raw_notes):
    target_type, target_id = validate_target(target_type, target_id)
    notes = normalize_notes(raw_notes)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with _database(path) as connection:
        connection.execute(
            "DELETE FROM notes WHERE target_type = ? AND target_id = ?",
            (target_type, target_id),
        )
        connection.executemany(
            "INSERT INTO notes "
            "(target_type, target_id, sort_order, title, platform, body, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    target_type, target_id, index, note["title"], note["platform"],
                    note["body"], now, now,
                )
                for index, note in enumerate(notes)
            ],
        )
    return list_notes(path, target_type, target_id)


def delete_target_notes(path, target_type, target_id):
    target_type, target_id = validate_target(target_type, target_id)
    with _database(path) as connection:
        connection.execute(
            "DELETE FROM notes WHERE target_type = ? AND target_id = ?",
            (target_type, target_id),
        )
