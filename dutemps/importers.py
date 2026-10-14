"""Bring existing time into DuTemps from a CSV, times in the timezone setting
(see README.md for the columns). The import is idempotent: rows carry
(source, source_ref) and the unique index on that pair makes a re-run add 0.
"""
import csv
import os

from . import core, db
from .timeutil import local_to_utc, parse_utc

CLIENT_DEFAULT_COLOR = "#03A9F4"


# --- lookups that create on demand ----------------------------------------

class Catalog:
    """Name -> id caches for clients/projects/tasks/tags, creating missing ones
    inside the caller's transaction."""

    def __init__(self, con):
        self.con = con
        self.created = {"clients": [], "projects": [], "tasks": [], "tags": []}

    def _one(self, sql, args):
        r = self.con.execute(sql, args).fetchone()
        return r[0] if r else None

    def client(self, name):
        name = (name or "").strip()
        if not name:
            return None
        cid = self._one("SELECT id FROM clients WHERE name = ? COLLATE NOCASE", (name,))
        if cid is None:
            cid = self.con.execute("INSERT INTO clients(name) VALUES (?)", (name,)).lastrowid
            db.audit(self.con, "create", "clients", cid, None, db.row(self.con, "clients", cid))
            self.created["clients"].append(name)
        return cid

    def project(self, name, client=None, color=None, billable=1):
        name = (name or "").strip()
        if not name:
            return None
        cid = self.client(client)
        pid = self._one("SELECT id FROM projects WHERE name = ? COLLATE NOCASE "
                        "AND IFNULL(client_id, 0) = IFNULL(?, 0)", (name, cid))
        if pid is None and cid is not None:     # same project name filed under no client
            pid = self._one("SELECT id FROM projects WHERE name = ? COLLATE NOCASE "
                            "AND client_id IS NULL", (name,))
            if pid is not None:                 # ...now belongs to this client
                before = db.row(self.con, "projects", pid)
                self.con.execute("UPDATE projects SET client_id = ? WHERE id = ?", (cid, pid))
                db.audit(self.con, "update", "projects", pid, before,
                         db.row(self.con, "projects", pid))
        if pid is None:
            pid = self.con.execute(
                "INSERT INTO projects(name, client_id, color, billable) VALUES (?, ?, ?, ?)",
                (name, cid, color or CLIENT_DEFAULT_COLOR, billable)).lastrowid
            db.audit(self.con, "create", "projects", pid, None, db.row(self.con, "projects", pid))
            self.created["projects"].append(name)
        return pid

    def task(self, project_id, name):
        name = (name or "").strip()
        if not name or not project_id:
            return None
        tid = self._one("SELECT id FROM tasks WHERE project_id = ? AND name = ? "
                        "COLLATE NOCASE", (project_id, name))
        if tid is None:
            tid = self.con.execute("INSERT INTO tasks(project_id, name) VALUES (?, ?)",
                                   (project_id, name)).lastrowid
            db.audit(self.con, "create", "tasks", tid, None, db.row(self.con, "tasks", tid))
            self.created["tasks"].append(name)
        return tid

    def tag(self, name):
        name = (name or "").strip()
        tid = self._one("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,))
        if tid is None:
            tid = self.con.execute("INSERT INTO tags(name) VALUES (?)", (name,)).lastrowid
            self.created["tags"].append(name)
        return tid


def _insert(con, cat, rec, source, ref):
    """rec: description, project, client, task, start_utc, end_utc, billable,
    hourly_rate, tags. Returns True if added, False if already present."""
    if con.execute("SELECT 1 FROM entries WHERE source = ? AND source_ref = ?",
                   (source, ref)).fetchone():
        return False
    pid = cat.project(rec["project"], rec.get("client"))
    tid = cat.task(pid, rec.get("task"))
    core.create_entry(con, {
        "description": rec.get("description", ""), "project_id": pid,
        "task_id": tid, "start_utc": rec["start_utc"], "end_utc": rec["end_utc"],
        "billable": rec.get("billable", 1), "hourly_rate": rec.get("hourly_rate"),
        "tag_ids": [cat.tag(t) for t in rec.get("tags") or []],
    }, source=source, source_ref=ref, _in_tx=True)
    return True


def _summary(recs, added, cat, label):
    hours = sum((parse_utc(r["end_utc"]) - parse_utc(r["start_utc"])).total_seconds()
                for r in recs) / 3600
    return {"source": label, "rows": len(recs), "added": added,
            "already_present": len(recs) - added, "hours_in_file": round(hours, 4),
            "created": {k: v for k, v in cat.created.items() if v}}


# --- CSV -------------------------------------------------------------------

def read_csv(path, tz):
    recs = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            s = local_to_utc(r["start_date"], r["start_time"], tz)
            e = local_to_utc(r["end_date"], r["end_time"], tz)
            recs.append({
                "description": r["description"], "project": r["project"],
                "client": r["client"], "task": r["task"], "start_utc": s,
                "end_utc": e, "billable": r["billable"].lower() == "true",
                # D3: the invoiced rate travels with the entry
                "hourly_rate": float(r["billable_rate"]) if r["billable_rate"] else None,
                "ref": f"{s}|{e}|{r['project']}|{r['task']}",
            })
    return recs


def import_csv(con, path, dry_run=False):
    tz = db.settings(con)["timezone"]
    recs = read_csv(path, tz)
    refs = [r["ref"] for r in recs]
    if len(set(refs)) != len(refs):
        raise core.Invalid(f"{path}: duplicate (start, end, project, task) rows")
    cat = Catalog(con)
    added = 0
    con.execute("BEGIN IMMEDIATE")
    try:
        for r in recs:
            added += _insert(con, cat, r, "csv", r["ref"])
        con.execute("ROLLBACK" if dry_run else "COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    out = _summary(recs, added, cat, os.path.basename(path))
    out["dry_run"] = dry_run
    return out


__all__ = ["import_csv"]
