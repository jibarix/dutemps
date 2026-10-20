"""SQLite storage. Times are UTC strings 'YYYY-MM-DDTHH:MM:SSZ' so they sort
lexically; local-day grouping happens in timeutil."""
import json
import os
import sqlite3
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(HERE, "data", "dutemps.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    hourly_rate REAL,
    archived    INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS clients_name ON clients(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS projects (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    client_id      INTEGER REFERENCES clients(id),
    color          TEXT NOT NULL DEFAULT '#03A9F4',
    billable       INTEGER NOT NULL DEFAULT 1,
    hourly_rate    REAL,
    estimate_hours REAL,
    archived       INTEGER NOT NULL DEFAULT 0,
    note           TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS projects_name
    ON projects(name COLLATE NOCASE, IFNULL(client_id, 0));

CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY,
    project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    hourly_rate    REAL,
    estimate_hours REAL,
    done           INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS tasks_name ON tasks(project_id, name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS tags (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS tags_name ON tags(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    project_id  INTEGER REFERENCES projects(id),
    task_id     INTEGER REFERENCES tasks(id),
    start_utc   TEXT NOT NULL,
    end_utc     TEXT,                       -- NULL while the timer runs
    billable    INTEGER NOT NULL DEFAULT 1,
    hourly_rate REAL,                       -- override; NULL = rate chain
    source      TEXT NOT NULL DEFAULT 'manual',
    source_ref  TEXT,
    created_utc TEXT NOT NULL,
    updated_utc TEXT NOT NULL,
    CHECK (end_utc IS NULL OR end_utc >= start_utc),
    CHECK (task_id IS NULL OR project_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS entries_start ON entries(start_utc);
CREATE UNIQUE INDEX IF NOT EXISTS entries_source
    ON entries(source, source_ref) WHERE source_ref IS NOT NULL;
-- at most one running timer
CREATE UNIQUE INDEX IF NOT EXISTS entries_one_running
    ON entries((end_utc IS NULL)) WHERE end_utc IS NULL;

CREATE TABLE IF NOT EXISTS entry_tags (
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (entry_id, tag_id)
);

CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY,
    at_utc    TEXT NOT NULL,
    action    TEXT NOT NULL,                -- create | update | delete | restore
    entity    TEXT NOT NULL,
    entity_id INTEGER,
    before    TEXT,
    after     TEXT
);

-- a task must belong to the entry's project
CREATE TRIGGER IF NOT EXISTS entries_task_project_ins
BEFORE INSERT ON entries WHEN NEW.task_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'task does not belong to project')
    WHERE (SELECT project_id FROM tasks WHERE id = NEW.task_id)
          IS NOT NEW.project_id;
END;
CREATE TRIGGER IF NOT EXISTS entries_task_project_upd
BEFORE UPDATE OF task_id, project_id ON entries WHEN NEW.task_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'task does not belong to project')
    WHERE (SELECT project_id FROM tasks WHERE id = NEW.task_id)
          IS NOT NEW.project_id;
END;

-- A draft is recomputed from the entries every time it is shown. Issuing
-- freezes it: the computed invoice, the profile text it used and the exact
-- PDF are stored, and the triggers below keep them from ever changing.
-- The only change an issued invoice accepts is issued -> void; void is final.
CREATE TABLE IF NOT EXISTS invoices (
    id            INTEGER PRIMARY KEY,
    number        TEXT NOT NULL,
    issued        TEXT NOT NULL,                -- invoice date, YYYY-MM-DD
    period_from   TEXT NOT NULL,
    period_to     TEXT NOT NULL,
    retainer      REAL NOT NULL DEFAULT 0,
    retainer_date TEXT,
    credit_pcts   TEXT NOT NULL,                -- JSON list, profile order
    note          TEXT NOT NULL DEFAULT '',     -- internal, never printed
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'issued', 'void')),
    snapshot      TEXT,                         -- JSON, set when issued
    profile       TEXT,                         -- JSON, set when issued
    pdf           BLOB,                         -- set when issued
    created_utc   TEXT NOT NULL,
    updated_utc   TEXT NOT NULL,
    issued_utc    TEXT,
    voided_utc    TEXT,
    CHECK (period_from <= period_to),
    CHECK (status = 'draft' OR (snapshot IS NOT NULL AND pdf IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS invoices_number ON invoices(number COLLATE NOCASE);

CREATE TRIGGER IF NOT EXISTS invoices_frozen
BEFORE UPDATE ON invoices WHEN OLD.status <> 'draft'
BEGIN
    SELECT RAISE(ABORT, 'an issued invoice cannot be changed; void it instead')
    WHERE NOT (OLD.status = 'issued' AND NEW.status = 'void')
       OR NEW.number IS NOT OLD.number OR NEW.issued IS NOT OLD.issued
       OR NEW.period_from IS NOT OLD.period_from OR NEW.period_to IS NOT OLD.period_to
       OR NEW.retainer IS NOT OLD.retainer OR NEW.retainer_date IS NOT OLD.retainer_date
       OR NEW.credit_pcts IS NOT OLD.credit_pcts OR NEW.note IS NOT OLD.note
       OR NEW.snapshot IS NOT OLD.snapshot OR NEW.profile IS NOT OLD.profile
       OR NEW.pdf IS NOT OLD.pdf OR NEW.issued_utc IS NOT OLD.issued_utc;
END;
CREATE TRIGGER IF NOT EXISTS invoices_kept
BEFORE DELETE ON invoices WHEN OLD.status <> 'draft'
BEGIN
    SELECT RAISE(ABORT, 'an issued invoice cannot be deleted; void it instead');
END;
"""

DEFAULT_SETTINGS = {
    "timezone": "UTC",
    "currency": "USD",
    "default_rate": "0",
    "week_start": "1",              # 1 = Monday ... 7 = Sunday
    "user_name": "",
    "user_email": "",
    "time_format": "12",            # 12 | 24
    "duration_format": "hms",       # hms (1:30:00) | decimal (1.50)
}


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path=None):
    path = path or os.environ.get("DUTEMPS_DB") or DEFAULT_PATH
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    return con


class tx:
    """`with tx(con):` -- BEGIN IMMEDIATE / COMMIT, ROLLBACK on error."""

    def __init__(self, con):
        self.con = con

    def __enter__(self):
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, et, ev, tb):
        self.con.execute("ROLLBACK" if et else "COMMIT")
        return False


def settings(con):
    return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}


def row(con, table, id_):
    r = con.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()
    return dict(r) if r else None


def audit(con, action, entity, entity_id, before=None, after=None):
    return con.execute(
        "INSERT INTO audit(at_utc, action, entity, entity_id, before, after) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now_utc(), action, entity, entity_id,
         json.dumps(before) if before is not None else None,
         json.dumps(after) if after is not None else None)).lastrowid


def db_dir(con):
    """Folder of the open database; backups go in its backups/ subfolder."""
    f = con.execute("PRAGMA database_list").fetchone()["file"]
    return os.path.dirname(f) if f else os.path.join(HERE, "data")


def backup(con, dest):
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    out = sqlite3.connect(dest)
    try:            # closed even on failure, so the caller can remove the file
        with out:
            con.backup(out)
    finally:
        out.close()
    return dest
