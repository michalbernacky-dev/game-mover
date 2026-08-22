"""SQLite-backed notes shared by Game Mover clients."""

import datetime
import json
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
MAX_CHECKS_PER_NOTE = 24
MAX_CHECK_LABEL_LENGTH = 160
CHECK_TYPES = frozenset({
    "path_exists", "path_absent", "file_contains",
    "json_value", "steam_app",
})
CHECK_CATEGORIES = frozenset({"installation", "configuration"})


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
        checks = normalize_checks(raw_note.get("checks", []))
        notes.append({"title": title, "platform": platform, "body": body, "checks": checks})
    return notes


def normalize_checks(raw_checks):
    """Validate the small declarative language understood by local clients."""
    if not isinstance(raw_checks, list) or len(raw_checks) > MAX_CHECKS_PER_NOTE:
        raise NotesError("Neplatný seznam místních kontrol")
    checks = []
    for raw in raw_checks:
        if not isinstance(raw, dict):
            raise NotesError("Neplatná místní kontrola")
        check_type = str(raw.get("type", ""))
        category = str(raw.get("category", "configuration"))
        label = str(raw.get("label", "")).strip()
        if check_type not in CHECK_TYPES or category not in CHECK_CATEGORIES:
            raise NotesError("Nepodporovaný typ místní kontroly")
        if not label or len(label) > MAX_CHECK_LABEL_LENGTH:
            raise NotesError("Místní kontrola nemá platný popis")
        check = {"type": check_type, "category": category, "label": label}
        if check_type == "steam_app":
            app_id = str(raw.get("app_id", ""))
            if not app_id.isdigit() or len(app_id) > 12:
                raise NotesError("Neplatné Steam AppID")
            check["app_id"] = app_id
        elif check_type == "path_exists":
            paths = raw.get("paths", [raw.get("path", "")])
            if not isinstance(paths, list) or not paths or len(paths) > 12:
                raise NotesError("Neplatné cesty místní kontroly")
            check["paths"] = [_validate_check_path(path) for path in paths]
        elif check_type in {"path_absent", "file_contains", "json_value"}:
            check["path"] = _validate_check_path(raw.get("path", ""))
        if check_type == "file_contains":
            needles = raw.get("all", [])
            if not isinstance(needles, list) or not needles or len(needles) > 12:
                raise NotesError("Neplatný obsah místní kontroly")
            check["all"] = [_validate_short_value(value) for value in needles]
        elif check_type == "json_value":
            keys = raw.get("keys", [])
            if not isinstance(keys, list) or len(keys) > 16:
                raise NotesError("Neplatná JSON cesta místní kontroly")
            check["keys"] = [_validate_short_value(key) for key in keys]
            expected = raw.get("equals")
            if not isinstance(expected, (str, int, float, bool, type(None))):
                raise NotesError("Neplatná očekávaná JSON hodnota")
            check["equals"] = expected
        checks.append(check)
    return checks


def _validate_check_path(value):
    value = str(value).strip()
    if not value or len(value) > 512 or "\x00" in value:
        raise NotesError("Neplatná cesta místní kontroly")
    return value


def _validate_short_value(value):
    value = str(value)
    if not value or len(value) > 512 or "\x00" in value:
        raise NotesError("Neplatná hodnota místní kontroly")
    return value


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
            checks_json TEXT NOT NULL DEFAULT '[]',
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
    if "checks_json" not in columns:
        connection.execute(
            "ALTER TABLE notes ADD COLUMN checks_json TEXT NOT NULL DEFAULT '[]'"
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
            "SELECT id, title, platform, body, checks_json, created_at, updated_at FROM notes "
            "WHERE target_type = ? AND target_id = ? ORDER BY sort_order, id",
            (target_type, target_id),
        ).fetchall()
    return [_row_to_note(row) for row in rows]


def list_all_notes(path):
    with _database(path) as connection:
        rows = connection.execute(
            "SELECT id, target_type, target_id, title, platform, body, checks_json, "
            "created_at, updated_at FROM notes "
            "ORDER BY target_type, target_id, sort_order, id"
        ).fetchall()
    return [_row_to_note(row) for row in rows]


def _row_to_note(row):
    note = dict(row)
    try:
        note["checks"] = json.loads(note.pop("checks_json", "[]"))
    except (TypeError, json.JSONDecodeError):
        note.pop("checks_json", None)
        note["checks"] = []
    return note


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
            "(target_type, target_id, sort_order, title, platform, body, checks_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    target_type, target_id, index, note["title"], note["platform"],
                    note["body"], json.dumps(note["checks"], ensure_ascii=False), now, now,
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
