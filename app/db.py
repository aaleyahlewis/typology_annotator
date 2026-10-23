"""Companion database (SQLite). Label Studio holds tasks and annotation results;
this database holds participants, import requests, the canonical conversation
record, the conversation -> Label Studio task mapping, and an audit log.

Every query that touches participant data takes participant_id, so a participant
can only ever read or change their own rows.
"""
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .settings import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY,
    study_id TEXT UNIQUE NOT NULL,
    code_hash TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    key TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    first_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_requests (
    id TEXT PRIMARY KEY,
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    source TEXT NOT NULL,                 -- share_link | export_file
    share_id TEXT,
    status TEXT NOT NULL,                 -- processing | ready | failed | imported | cancelled | expired
    error_code TEXT,
    error_message TEXT,
    method TEXT,
    title TEXT,
    extracted_json TEXT,                  -- pending content; cleared when the request ends
    warnings_json TEXT,
    conversation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS import_requests_participant ON import_requests(participant_id, created_at);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    import_id TEXT NOT NULL REFERENCES import_requests(id),
    source TEXT NOT NULL,
    share_id TEXT,
    title TEXT,
    method TEXT,
    status TEXT NOT NULL,                 -- creating | ready | task_failed | deleted
    ls_task_id INTEGER,
    canonical_json TEXT,                  -- extracted messages, approved dialogue and mapping; cleared on delete
    warnings_json TEXT,
    schema_version TEXT NOT NULL,
    label_config_sha TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS conversations_one_per_share
    ON conversations(participant_id, share_id) WHERE share_id IS NOT NULL AND status != 'deleted';
CREATE TABLE IF NOT EXISTS annotations (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
    ls_annotation_id INTEGER,
    version INTEGER NOT NULL DEFAULT 0,
    submitted_at TEXT,
    lock_until TEXT,                      -- short lease held while a save is written to Label Studio
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    participant_id INTEGER,
    action TEXT NOT NULL,
    object_id TEXT,
    detail TEXT                           -- never conversation content
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix):
    return f"{prefix}_{secrets.token_urlsafe(12)}"


@contextmanager
def connect(immediate=False):
    path = settings.db_path
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init():
    path = settings.db_path
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def audit(c, participant_id, action, object_id=None, detail=None):
    c.execute("INSERT INTO audit_log (at, participant_id, action, object_id, detail) VALUES (?, ?, ?, ?, ?)",
              (now(), participant_id, action, object_id, detail))


# ---------- participants & login throttling ----------

def add_participant(study_id, code_hash):
    with connect() as c:
        c.execute("INSERT INTO participants (study_id, code_hash, created_at) VALUES (?, ?, ?)", (study_id, code_hash, now()))
        audit(c, None, "participant_added", study_id)


def set_code(study_id, code_hash):
    with connect() as c:
        n = c.execute("UPDATE participants SET code_hash = ?, disabled = 0 WHERE study_id = ?", (code_hash, study_id)).rowcount
        audit(c, None, "code_reset", study_id)
        return n


def set_disabled(study_id, disabled):
    with connect() as c:
        return c.execute("UPDATE participants SET disabled = ? WHERE study_id = ?", (int(disabled), study_id)).rowcount


def get_participant(study_id):
    with connect() as c:
        return c.execute("SELECT * FROM participants WHERE study_id = ?", (study_id,)).fetchone()


def get_participant_by_id(pid):
    with connect() as c:
        return c.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()


def login_blocked(key, max_failures=8, window_minutes=15):
    with connect() as c:
        row = c.execute("SELECT * FROM login_failures WHERE key = ?", (key,)).fetchone()
        if not row:
            return False
        if datetime.fromisoformat(row["first_at"]) < datetime.now(timezone.utc) - timedelta(minutes=window_minutes):
            c.execute("DELETE FROM login_failures WHERE key = ?", (key,))
            return False
        return row["count"] >= max_failures


def record_login_failure(key):
    with connect(immediate=True) as c:
        c.execute("""INSERT INTO login_failures (key, count, first_at) VALUES (?, 1, ?)
                     ON CONFLICT(key) DO UPDATE SET count = count + 1""", (key, now()))


def clear_login_failures(key):
    with connect() as c:
        c.execute("DELETE FROM login_failures WHERE key = ?", (key,))


# ---------- import requests ----------

def recent_import_count(pid, hours=1):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM import_requests WHERE participant_id = ? AND created_at >= ?", (pid, since)).fetchone()[0]


def active_import_for_share(pid, share_id):
    with connect() as c:
        return c.execute("""SELECT * FROM import_requests WHERE participant_id = ? AND share_id = ?
                            AND status IN ('processing', 'ready') ORDER BY created_at DESC LIMIT 1""", (pid, share_id)).fetchone()


def create_import(pid, source, share_id, status="processing"):
    iid, ts = new_id("imp"), now()
    with connect() as c:
        c.execute("""INSERT INTO import_requests (id, participant_id, source, share_id, status, created_at, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""", (iid, pid, source, share_id, status, ts, ts))
        audit(c, pid, "import_started", iid, source)
    return iid


def finish_import_extraction(iid, title, method, messages, warnings):
    with connect() as c:
        c.execute("""UPDATE import_requests SET status = 'ready', title = ?, method = ?, extracted_json = ?, warnings_json = ?,
                     updated_at = ? WHERE id = ? AND status = 'processing'""",
                  (title, method, json.dumps(messages, ensure_ascii=False), json.dumps(warnings), now(), iid))


def fail_import(iid, code, message):
    with connect() as c:
        c.execute("""UPDATE import_requests SET status = 'failed', error_code = ?, error_message = ?, extracted_json = NULL,
                     updated_at = ? WHERE id = ? AND status = 'processing'""", (code, message, now(), iid))
        audit(c, None, "import_failed", iid, code)


def get_import(iid, pid):
    with connect() as c:
        return c.execute("SELECT * FROM import_requests WHERE id = ? AND participant_id = ?", (iid, pid)).fetchone()


def end_import(iid, pid, status):
    with connect() as c:
        n = c.execute("""UPDATE import_requests SET status = ?, extracted_json = NULL, updated_at = ?
                         WHERE id = ? AND participant_id = ? AND status IN ('processing', 'ready')""", (status, now(), iid, pid)).rowcount
        if n:
            audit(c, pid, f"import_{status}", iid)
        return n


def expire_pending_imports(ttl_hours):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
    with connect() as c:
        return c.execute("""UPDATE import_requests SET status = 'expired', extracted_json = NULL, updated_at = ?
                            WHERE status IN ('processing', 'ready') AND created_at < ?""", (now(), cutoff)).rowcount


# ---------- conversations ----------

def claim_import_for_conversation(iid, pid, canonical, title, share_id, source, method, warnings, schema_version, config_sha):
    """Atomically turn a ready import into a conversation in 'creating' state.

    Returns (conversation_id, created). If the import was already confirmed, returns the
    existing conversation (idempotent). Raises LookupError if the import isn't confirmable.
    """
    with connect(immediate=True) as c:
        imp = c.execute("SELECT * FROM import_requests WHERE id = ? AND participant_id = ?", (iid, pid)).fetchone()
        if imp is None:
            raise LookupError("not_found")
        if imp["status"] == "imported" and imp["conversation_id"]:
            return imp["conversation_id"], False
        if imp["status"] != "ready":
            raise LookupError(imp["status"])
        if share_id:
            existing = c.execute("""SELECT id FROM conversations WHERE participant_id = ? AND share_id = ? AND status != 'deleted'""",
                                 (pid, share_id)).fetchone()
            if existing:
                raise LookupError("duplicate:" + existing["id"])
        cid, ts = new_id("conv"), now()
        c.execute("""INSERT INTO conversations (id, participant_id, import_id, source, share_id, title, method, status,
                     canonical_json, warnings_json, schema_version, label_config_sha, created_at, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, 'creating', ?, ?, ?, ?, ?, ?)""",
                  (cid, pid, iid, source, share_id, title, method, json.dumps(canonical, ensure_ascii=False),
                   json.dumps(warnings), schema_version, config_sha, ts, ts))
        c.execute("""UPDATE import_requests SET status = 'imported', conversation_id = ?, extracted_json = NULL, updated_at = ?
                     WHERE id = ?""", (cid, ts, iid))
        audit(c, pid, "conversation_created", cid, f"{len(canonical['dialogue'])} messages")
        return cid, True


def set_task(cid, ls_task_id):
    with connect() as c:
        c.execute("UPDATE conversations SET ls_task_id = ?, status = 'ready', updated_at = ? WHERE id = ?", (ls_task_id, now(), cid))


def set_task_failed(cid):
    with connect() as c:
        c.execute("UPDATE conversations SET status = 'task_failed', updated_at = ? WHERE id = ? AND status = 'creating'", (now(), cid))


def claim_task_retry(cid, pid):
    with connect(immediate=True) as c:
        return c.execute("""UPDATE conversations SET status = 'creating', updated_at = ?
                            WHERE id = ? AND participant_id = ? AND status = 'task_failed'""", (now(), cid, pid)).rowcount


def get_conversation(cid, pid):
    with connect() as c:
        return c.execute("SELECT * FROM conversations WHERE id = ? AND participant_id = ? AND status != 'deleted'", (cid, pid)).fetchone()


def list_conversations(pid):
    with connect() as c:
        return c.execute("""SELECT c.id, c.title, c.status, c.created_at, a.submitted_at, a.updated_at AS annotated_at
                            FROM conversations c LEFT JOIN annotations a ON a.conversation_id = c.id
                            WHERE c.participant_id = ? AND c.status != 'deleted' ORDER BY c.created_at DESC""", (pid,)).fetchall()


def mark_deleted(cid, pid):
    with connect() as c:
        n = c.execute("""UPDATE conversations SET status = 'deleted', canonical_json = NULL, updated_at = ?
                         WHERE id = ? AND participant_id = ? AND status != 'deleted'""", (now(), cid, pid)).rowcount
        if n:
            c.execute("DELETE FROM annotations WHERE conversation_id = ?", (cid,))
            audit(c, pid, "conversation_deleted", cid)
        return n


# ---------- annotations ----------

def get_annotation(cid):
    with connect() as c:
        return c.execute("SELECT * FROM annotations WHERE conversation_id = ?", (cid,)).fetchone()


def begin_annotation_save(cid, base_version, lease_seconds=60):
    """Take a short lease so only one save at a time is written to Label Studio.

    Raises ValueError('conflict') if base_version is stale (edited in another tab),
    or ValueError('busy') if another save is in progress.
    """
    ts = datetime.now(timezone.utc)
    with connect(immediate=True) as c:
        row = c.execute("SELECT * FROM annotations WHERE conversation_id = ?", (cid,)).fetchone()
        if row is None:
            c.execute("INSERT INTO annotations (conversation_id, version, created_at, updated_at) VALUES (?, 0, ?, ?)", (cid, now(), now()))
            row = c.execute("SELECT * FROM annotations WHERE conversation_id = ?", (cid,)).fetchone()
        if base_version != row["version"]:
            raise ValueError("conflict")
        if row["lock_until"] and datetime.fromisoformat(row["lock_until"]) > ts:
            raise ValueError("busy")
        c.execute("UPDATE annotations SET lock_until = ? WHERE conversation_id = ?",
                  ((ts + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds"), cid))
        return row


def release_annotation_lock(cid):
    with connect() as c:
        c.execute("UPDATE annotations SET lock_until = NULL WHERE conversation_id = ?", (cid,))


def finish_annotation_save(cid, pid, base_version, ls_annotation_id, submitted, regions):
    with connect(immediate=True) as c:
        n = c.execute("""UPDATE annotations SET ls_annotation_id = ?, version = version + 1, updated_at = ?, lock_until = NULL,
                         submitted_at = CASE WHEN ? THEN COALESCE(submitted_at, ?) ELSE submitted_at END
                         WHERE conversation_id = ? AND version = ?""",
                      (ls_annotation_id, now(), int(submitted), now(), cid, base_version)).rowcount
        if not n:
            raise ValueError("conflict")
        audit(c, pid, "annotation_submitted" if submitted else "annotation_saved", cid, f"{regions} highlights")
        return base_version + 1


def export_rows():
    with connect() as c:
        return c.execute("""SELECT p.study_id, c.*, a.ls_annotation_id, a.version, a.submitted_at,
                                   a.created_at AS annotation_created_at, a.updated_at AS annotation_updated_at
                            FROM conversations c JOIN participants p ON p.id = c.participant_id
                            LEFT JOIN annotations a ON a.conversation_id = c.id
                            WHERE c.status != 'deleted' ORDER BY c.created_at""").fetchall()
