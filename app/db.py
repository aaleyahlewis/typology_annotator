"""SQLite storage for participants, conversations (tasks) and annotations."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY,
    study_id TEXT UNIQUE NOT NULL,
    code_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    conversation_id TEXT,
    title TEXT,
    source TEXT,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY,
    task_id INTEGER UNIQUE NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    result_json TEXT NOT NULL,
    submitted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path():
    return os.environ.get("TYPOLOGY_DB", os.path.join(os.path.dirname(__file__), "..", "data", "typology.sqlite3"))


@contextmanager
def connect():
    path = db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as c:
        c.executescript(SCHEMA)


def add_participant(study_id, code_hash):
    with connect() as c:
        c.execute("INSERT INTO participants (study_id, code_hash, created_at) VALUES (?, ?, ?)",
                  (study_id, code_hash, now()))


def get_participant(study_id):
    with connect() as c:
        return c.execute("SELECT * FROM participants WHERE study_id = ?", (study_id,)).fetchone()


def create_task(participant_id, data, source):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO tasks (participant_id, conversation_id, title, source, data_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (participant_id, data.get("conversation_id"), data.get("title"), source, json.dumps(data, ensure_ascii=False), now()))
        return cur.lastrowid


def list_tasks(participant_id):
    with connect() as c:
        return c.execute(
            """SELECT t.id, t.title, t.created_at, a.submitted, a.updated_at
               FROM tasks t LEFT JOIN annotations a ON a.task_id = t.id
               WHERE t.participant_id = ? ORDER BY t.id DESC""", (participant_id,)).fetchall()


def get_task(task_id, participant_id):
    with connect() as c:
        return c.execute("SELECT * FROM tasks WHERE id = ? AND participant_id = ?", (task_id, participant_id)).fetchone()


def delete_task(task_id, participant_id):
    with connect() as c:
        return c.execute("DELETE FROM tasks WHERE id = ? AND participant_id = ?", (task_id, participant_id)).rowcount


def get_annotation(task_id):
    with connect() as c:
        return c.execute("SELECT * FROM annotations WHERE task_id = ?", (task_id,)).fetchone()


def save_annotation(task_id, result, submitted):
    ts = now()
    with connect() as c:
        c.execute(
            """INSERT INTO annotations (task_id, result_json, submitted, created_at, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET result_json = excluded.result_json,
                   submitted = MAX(annotations.submitted, excluded.submitted), updated_at = excluded.updated_at""",
            (task_id, json.dumps(result, ensure_ascii=False), int(submitted), ts, ts))


def export_all():
    with connect() as c:
        return c.execute(
            """SELECT p.study_id, t.id AS task_id, t.source, t.data_json, t.created_at AS task_created_at,
                      a.result_json, a.submitted, a.created_at AS annotation_created_at, a.updated_at AS annotation_updated_at
               FROM tasks t JOIN participants p ON p.id = t.participant_id
               LEFT JOIN annotations a ON a.task_id = t.id ORDER BY t.id""").fetchall()
