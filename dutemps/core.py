"""Domain operations. Every write goes through here so the audit log and the
invariants (one running timer, task-in-project, end >= start) hold for the
web UI, the importers and the CLI alike."""
import re
from datetime import datetime, timezone

from . import db
from .db import audit, now_utc, row, tx
from .timeutil import day_bounds_utc, parse_utc, seconds, zone

UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class Invalid(ValueError):
    """Bad input -> HTTP 400 with this message."""


# --- helpers ---------------------------------------------------------------

def _num(v, field, allow_none=True):
    if v in (None, ""):
        if allow_none:
            return None
        raise Invalid(f"{field} is required")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise Invalid(f"{field} must be a number")
    if x < 0:
        raise Invalid(f"{field} cannot be negative")
    return x


def _name(v, field="name"):
    v = (v or "").strip()
    if not v:
        raise Invalid(f"{field} is required")
    return v


def _utc(v, field):
    if not isinstance(v, str) or not UTC_RE.match(v):
        raise Invalid(f"{field} must be UTC 'YYYY-MM-DDTHH:MM:SSZ'")
    parse_utc(v)
    return v


def _id_or_none(v):
    if v in (None, "", 0, "0"):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise Invalid("ids must be integers")


def _bool(v):
    if isinstance(v, str):
        return 1 if v.lower() in ("1", "true", "yes", "on") else 0
    return 1 if v else 0


def _color(v):
    if not COLOR_RE.match(v or ""):
        raise Invalid("color must be #RRGGBB")
    return v


def _required_id(v):
    v = _id_or_none(v)
    if v is None:
        raise Invalid("project is required")
    return v


# --- clients / projects / tasks / tags -------------------------------------

SIMPLE = {
    "clients":  {"name": _name, "hourly_rate": lambda v: _num(v, "rate"),
                 "archived": _bool, "note": lambda v: (v or "")},
    "projects": {"name": _name, "client_id": _id_or_none, "color": _color,
                 "billable": _bool, "hourly_rate": lambda v: _num(v, "rate"),
                 "estimate_hours": lambda v: _num(v, "estimate"),
                 "archived": _bool, "note": lambda v: (v or "")},
    "tasks":    {"name": _name, "project_id": _required_id,
                 "hourly_rate": lambda v: _num(v, "rate"),
                 "estimate_hours": lambda v: _num(v, "estimate"), "done": _bool},
    "tags":     {"name": _name, "archived": _bool},
}


def _clean(table, data, partial):
    spec = SIMPLE[table]
    out = {}
    for k, fn in spec.items():
        if k in data:
            out[k] = fn(data[k])
        elif not partial and k == "name":
            out[k] = _name(None)
    if table == "tasks" and not partial and "project_id" not in out:
        raise Invalid("project is required")
    return out


def create(con, table, data):
    vals = _clean(table, data, partial=False)
    cols = ", ".join(vals)
    try:
        with tx(con):
            cur = con.execute(f"INSERT INTO {table}({cols}) VALUES "
                              f"({', '.join('?' * len(vals))})", list(vals.values()))
            new = row(con, table, cur.lastrowid)
            audit(con, "create", table, new["id"], None, new)
    except db.sqlite3.IntegrityError as e:
        raise Invalid(_integrity(e, table))
    return new


def update(con, table, id_, data):
    vals = _clean(table, data, partial=True)
    if not vals:
        return row(con, table, id_)
    try:
        with tx(con):
            before = row(con, table, id_)
            if not before:
                raise Invalid(f"{table[:-1]} {id_} not found")
            con.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in vals)} "
                        f"WHERE id = ?", [*vals.values(), id_])
            after = row(con, table, id_)
            audit(con, "update", table, id_, before, after)
    except db.sqlite3.IntegrityError as e:
        raise Invalid(_integrity(e, table))
    return after


def delete(con, table, id_):
    """Clients/projects/tags/tasks in use are archived instead of deleted, so
    history keeps its labels."""
    with tx(con):
        before = row(con, table, id_)
        if not before:
            raise Invalid(f"{table[:-1]} {id_} not found")
        used = {
            "clients":  "SELECT 1 FROM projects WHERE client_id = ?",
            "projects": "SELECT 1 FROM entries WHERE project_id = ?",
            "tasks":    "SELECT 1 FROM entries WHERE task_id = ?",
            "tags":     "SELECT 1 FROM entry_tags WHERE tag_id = ?",
        }[table]
        if con.execute(used, (id_,)).fetchone():
            flag = "done" if table == "tasks" else "archived"
            con.execute(f"UPDATE {table} SET {flag} = 1 WHERE id = ?", (id_,))
            audit(con, "update", table, id_, before, row(con, table, id_))
            return {"archived": True}
        con.execute(f"DELETE FROM {table} WHERE id = ?", (id_,))
        audit(con, "delete", table, id_, before, None)
        return {"deleted": True}


def _integrity(e, table):
    msg = str(e)
    if "UNIQUE" in msg:
        return f"a {table[:-1]} with that name already exists"
    if "FOREIGN KEY" in msg:
        return "referenced item does not exist"
    return msg


def meta(con):
    q = lambda sql: [dict(r) for r in con.execute(sql)]
    return {
        "settings": db.settings(con),
        "clients": q("SELECT * FROM clients ORDER BY name COLLATE NOCASE"),
        "projects": q("SELECT p.*, c.name AS client FROM projects p "
                      "LEFT JOIN clients c ON c.id = p.client_id "
                      "ORDER BY p.name COLLATE NOCASE"),
        "tasks": q("SELECT * FROM tasks ORDER BY project_id, name COLLATE NOCASE"),
        "tags": q("SELECT * FROM tags ORDER BY name COLLATE NOCASE"),
    }


def set_settings(con, data):
    allowed = set(db.DEFAULT_SETTINGS)
    with tx(con):
        before = db.settings(con)
        for k, v in data.items():
            if k not in allowed:
                raise Invalid(f"unknown setting {k}")
            v = str(v).strip()
            if k == "default_rate":
                _num(v, "default rate", allow_none=False)
            if k == "time_format" and v not in ("12", "24"):
                raise Invalid("time_format must be 12 or 24")
            if k == "duration_format" and v not in ("hms", "decimal"):
                raise Invalid("duration_format must be hms or decimal")
            if k == "week_start" and (len(v) != 1 or v not in "1234567"):
                raise Invalid("week_start must be 1..7")
            if k == "timezone":
                try:
                    zone(v)
                except Exception as e:
                    raise Invalid(f"unknown timezone {v}" + (
                        " (on Windows, pip install tzdata)" if "tzdata" in str(e) else ""))
            con.execute("UPDATE settings SET value = ? WHERE key = ?", (v, k))
        audit(con, "update", "settings", None, before, db.settings(con))
    return db.settings(con)


# --- entries ---------------------------------------------------------------

ENTRY_SQL = """
SELECT e.*, p.name AS project, p.color AS project_color, p.client_id,
       c.name AS client, t.name AS task,
       COALESCE(e.hourly_rate, t.hourly_rate, p.hourly_rate, c.hourly_rate,
                CAST((SELECT value FROM settings WHERE key = 'default_rate') AS REAL))
           AS rate
FROM entries e
LEFT JOIN projects p ON p.id = e.project_id
LEFT JOIN clients  c ON c.id = p.client_id
LEFT JOIN tasks    t ON t.id = e.task_id
"""


def _hydrate(con, rows):
    rows = [dict(r) for r in rows]
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    tags = {}
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        for t in con.execute(
                "SELECT et.entry_id, g.id, g.name FROM entry_tags et "
                "JOIN tags g ON g.id = et.tag_id WHERE et.entry_id IN "
                f"({','.join('?' * len(chunk))}) ORDER BY g.name", chunk):
            tags.setdefault(t[0], []).append({"id": t[1], "name": t[2]})
    now = datetime.now(timezone.utc)
    for r in rows:
        r["tags"] = tags.get(r["id"], [])
        end = parse_utc(r["end_utc"]) if r["end_utc"] else now
        r["seconds"] = int((end - parse_utc(r["start_utc"])).total_seconds())
        r["running"] = r["end_utc"] is None
        r["amount"] = (round(r["seconds"] / 3600 * r["rate"], 2)
                       if r["billable"] and r["rate"] is not None else 0.0)
    return rows


def get_entry(con, id_):
    r = _hydrate(con, con.execute(ENTRY_SQL + " WHERE e.id = ?", (id_,)).fetchall())
    return r[0] if r else None


def entry_where(con, f):
    """Filters -> (sql, params). Dates are local (settings.timezone)."""
    tz = db.settings(con)["timezone"]
    w, p = [], []
    if f.get("from") or f.get("to"):
        a, b = day_bounds_utc(f.get("from") or "1970-01-01",
                              f.get("to") or "2999-12-31", tz)
        w += ["e.start_utc >= ?", "e.start_utc < ?"]
        p += [a, b]

    def ids(key):
        v = f.get(key)
        if v in (None, "", []):
            return []
        v = v if isinstance(v, list) else str(v).split(",")
        return [int(x) for x in v if str(x).strip()]

    for key, col in (("project", "e.project_id"), ("task", "e.task_id"),
                     ("client", "p.client_id")):
        vs = ids(key)
        if vs:
            w.append(f"{col} IN ({','.join('?' * len(vs))})")
            p += vs
    tg = ids("tag")
    if tg:
        w.append("e.id IN (SELECT entry_id FROM entry_tags WHERE tag_id IN "
                 f"({','.join('?' * len(tg))}))")
        p += tg
    if f.get("billable") in ("true", "false", True, False):
        w.append("e.billable = ?")
        p.append(1 if f["billable"] in ("true", True) else 0)
    if f.get("no_project") in ("true", True):
        w.append("e.project_id IS NULL")
    if f.get("q"):
        w.append("e.description LIKE ?")
        p.append(f"%{f['q']}%")
    if f.get("source"):
        w.append("e.source = ?")
        p.append(f["source"])
    if f.get("finished") in ("true", True):
        w.append("e.end_utc IS NOT NULL")
    return (" WHERE " + " AND ".join(w)) if w else "", p


def list_entries(con, f=None, limit=None, offset=0, order="DESC"):
    f = f or {}
    where, p = entry_where(con, f)
    sql = ENTRY_SQL + where + f" ORDER BY e.start_utc {order}, e.id {order}"
    if limit:
        sql += " LIMIT ? OFFSET ?"
        p += [int(limit), int(offset)]
    return _hydrate(con, con.execute(sql, p).fetchall())


def count_entries(con, f=None):
    where, p = entry_where(con, f or {})
    sql = ("SELECT COUNT(*) FROM entries e LEFT JOIN projects p "
           "ON p.id = e.project_id" + where)
    return con.execute(sql, p).fetchone()[0]


def _entry_vals(con, data, partial, current=None):
    v = {}
    if "description" in data:
        v["description"] = (data["description"] or "").strip()
    if "project_id" in data:
        v["project_id"] = _id_or_none(data["project_id"])
    if "task_id" in data:
        v["task_id"] = _id_or_none(data["task_id"])
    if "start_utc" in data:
        v["start_utc"] = _utc(data["start_utc"], "start")
    if "end_utc" in data:
        v["end_utc"] = None if data["end_utc"] is None else _utc(data["end_utc"], "end")
    if "billable" in data:
        v["billable"] = _bool(data["billable"])
    if "hourly_rate" in data:
        v["hourly_rate"] = _num(data["hourly_rate"], "rate")
    if not partial and "start_utc" not in v:
        raise Invalid("start is required")
    merged = {**(current or {}), **v}
    if "project_id" in v and "task_id" not in v and current and current.get("task_id"):
        # moving to another project drops a task that belonged to the old one
        t = row(con, "tasks", current["task_id"])
        if t and t["project_id"] != v["project_id"]:
            v["task_id"] = merged["task_id"] = None
    if merged.get("end_utc") and merged.get("start_utc") and \
            merged["end_utc"] < merged["start_utc"]:
        raise Invalid("end is before start")
    if merged.get("task_id"):
        t = row(con, "tasks", merged["task_id"])
        if not t:
            raise Invalid("task not found")
        if merged.get("project_id") is None:
            v["project_id"] = merged["project_id"] = t["project_id"]
        elif t["project_id"] != merged["project_id"]:
            raise Invalid("task does not belong to project")
    if "billable" not in v and not partial and merged.get("project_id"):
        pr = row(con, "projects", merged["project_id"])
        v["billable"] = pr["billable"] if pr else 1
    return v


def _set_tags(con, entry_id, tag_ids):
    con.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry_id,))
    for t in tag_ids or []:
        con.execute("INSERT OR IGNORE INTO entry_tags(entry_id, tag_id) VALUES (?, ?)",
                    (entry_id, int(t)))


def _snap(con, id_):
    r = row(con, "entries", id_)
    if r:
        r["tag_ids"] = [x[0] for x in con.execute(
            "SELECT tag_id FROM entry_tags WHERE entry_id = ?", (id_,))]
    return r


def create_entry(con, data, source="manual", source_ref=None, _in_tx=False):
    v = _entry_vals(con, data, partial=False)
    if "end_utc" not in v:
        raise Invalid("end is required (use the timer to start an open entry)")
    ts = now_utc()
    v.update(source=source, source_ref=source_ref, created_utc=ts, updated_utc=ts)

    def go():
        cur = con.execute(f"INSERT INTO entries({', '.join(v)}) VALUES "
                          f"({', '.join('?' * len(v))})", list(v.values()))
        _set_tags(con, cur.lastrowid, data.get("tag_ids"))
        audit(con, "create", "entries", cur.lastrowid, None, _snap(con, cur.lastrowid))
        return cur.lastrowid

    try:
        if _in_tx:
            new_id = go()
        else:
            with tx(con):
                new_id = go()
    except db.sqlite3.IntegrityError as e:
        raise Invalid(str(e))
    return get_entry(con, new_id)


def update_entry(con, id_, data):
    return update_entry_with(con, id_, lambda current: data)


def update_entry_with(con, id_, derive):
    """update_entry, with the change worked out by derive(current) from the
    entry as it is inside the transaction (its row plus tag_ids), so a
    concurrent edit can't be overwritten with values from a stale copy."""
    with tx(con):
        before = _snap(con, id_)
        if not before:
            raise Invalid(f"entry {id_} not found")
        data = derive({**before, "tag_ids": list(before["tag_ids"])})
        v = _entry_vals(con, data, partial=True, current=before)
        if "end_utc" in v and v["end_utc"] is None and before["end_utc"] is not None:
            raise Invalid("cannot reopen a finished entry; use Continue instead")
        if v:
            v["updated_utc"] = now_utc()
            try:
                con.execute(f"UPDATE entries SET {', '.join(f'{k} = ?' for k in v)} "
                            "WHERE id = ?", [*v.values(), id_])
            except db.sqlite3.IntegrityError as e:
                raise Invalid(str(e))
        if "tag_ids" in data:
            _set_tags(con, id_, data["tag_ids"])
        audit(con, "update", "entries", id_, before, _snap(con, id_))
    return get_entry(con, id_)


def delete_entries(con, ids):
    out, audits = [], []
    with tx(con):
        for id_ in ids:
            before = _snap(con, int(id_))
            if not before:
                continue
            con.execute("DELETE FROM entries WHERE id = ?", (int(id_),))
            audits.append(audit(con, "delete", "entries", int(id_), before, None))
            out.append(int(id_))
    return {"deleted": out, "audit_ids": audits}


def restore_entry(con, audit_id):
    """Undo a delete: re-insert the snapshot recorded in the audit log."""
    with tx(con):
        a = con.execute("SELECT * FROM audit WHERE id = ? AND action = 'delete' "
                        "AND entity = 'entries'", (audit_id,)).fetchone()
        if not a:
            raise Invalid("no such deleted entry")
        import json
        snap = json.loads(a["before"])
        if _undone(con, a):
            raise Invalid("this entry was already restored")
        if row(con, "entries", snap["id"]):     # a newer entry has its id
            del snap["id"]
        tag_ids = snap.pop("tag_ids", [])
        if snap.get("source_ref") and con.execute(
                "SELECT 1 FROM entries WHERE source = ? AND source_ref = ?",
                (snap["source"], snap["source_ref"])).fetchone():
            raise Invalid("this entry was imported again since it was deleted")
        if snap["end_utc"] is None and con.execute(
                "SELECT 1 FROM entries WHERE end_utc IS NULL").fetchone():
            raise Invalid("a timer is running; stop it before restoring this one")
        new = con.execute(f"INSERT INTO entries({', '.join(snap)}) VALUES "
                          f"({', '.join('?' * len(snap))})", list(snap.values())).lastrowid
        _set_tags(con, new, [t for t in tag_ids if row(con, "tags", t)])
        audit(con, "restore", "entries", new, {"from_audit": a["id"]}, _snap(con, new))
    return get_entry(con, new)


def _undone(con, a):
    """Whether the delete recorded in audit row a was restored. Restores name
    the delete they undo; older ones put the entry back under its own id."""
    import json
    for r in con.execute("SELECT before FROM audit WHERE action = 'restore' AND "
                         "entity = 'entries' AND id > ? AND (before IS NOT NULL "
                         "OR entity_id = ?)", (a["id"], a["entity_id"])):
        if r["before"] is None or json.loads(r["before"]).get("from_audit") == a["id"]:
            return True
    return False


def recent_deletes(con, limit=20):
    import json
    out = []
    for a in con.execute("SELECT * FROM audit WHERE action = 'delete' AND "
                         "entity = 'entries' ORDER BY id DESC LIMIT ?", (limit,)):
        if _undone(con, a):
            continue
        s = json.loads(a["before"])
        out.append({"audit_id": a["id"], "at_utc": a["at_utc"],
                    "description": s["description"], "start_utc": s["start_utc"],
                    "end_utc": s["end_utc"]})
    return out


# --- timer -----------------------------------------------------------------

def running(con):
    r = con.execute("SELECT id FROM entries WHERE end_utc IS NULL").fetchone()
    return get_entry(con, r[0]) if r else None


def start_timer(con, data):
    """Start a timer. A running one is stopped at the same instant first."""
    return switch_timer(con, data)["started"]


def switch_timer(con, data, stop_at=None):
    """start_timer, returning {"started": entry, "stopped": entry or None}.
    With stop_at (UTC), a running timer ends then instead of now, in the same
    transaction: a timer started back-dated to stop_at doesn't overlap it.
    stop_at must be after the running timer's start."""
    ts = now_utc()
    with tx(con):
        cur = con.execute("SELECT id, start_utc FROM entries "
                          "WHERE end_utc IS NULL").fetchone()
        if cur:
            end = ts if stop_at is None else _utc(stop_at, "stop")
            if stop_at is not None and end <= cur["start_utc"]:
                raise Invalid("the running timer started at or after that time; "
                              "stop it first, or pick a later start")
            before = _snap(con, cur[0])
            con.execute("UPDATE entries SET end_utc = ?, updated_utc = ? WHERE id = ?",
                        (end, ts, cur[0]))
            audit(con, "update", "entries", cur[0], before, _snap(con, cur[0]))
        v = _entry_vals(con, {**data, "start_utc": data.get("start_utc") or ts},
                        partial=False)
        v.pop("end_utc", None)
        v.update(source="timer", created_utc=ts, updated_utc=ts)
        c = con.execute(f"INSERT INTO entries({', '.join(v)}) VALUES "
                        f"({', '.join('?' * len(v))})", list(v.values()))
        _set_tags(con, c.lastrowid, data.get("tag_ids"))
        audit(con, "create", "entries", c.lastrowid, None, _snap(con, c.lastrowid))
    return {"started": get_entry(con, c.lastrowid),
            "stopped": get_entry(con, cur[0]) if cur else None}


def stop_timer(con, end_utc=None):
    ts = end_utc or now_utc()
    with tx(con):
        cur = con.execute("SELECT id, start_utc FROM entries WHERE end_utc IS NULL").fetchone()
        if not cur:
            raise Invalid("no timer is running")
        if ts < cur["start_utc"]:
            raise Invalid("end is before start")
        before = _snap(con, cur["id"])
        con.execute("UPDATE entries SET end_utc = ?, updated_utc = ? WHERE id = ?",
                    (ts, now_utc(), cur["id"]))
        audit(con, "update", "entries", cur["id"], before, _snap(con, cur["id"]))
    return get_entry(con, cur["id"])


def discard_timer(con):
    r = running(con)
    if not r:
        raise Invalid("no timer is running")
    return delete_entries(con, [r["id"]])


def continue_entry(con, id_):
    e = get_entry(con, id_)
    if not e:
        raise Invalid(f"entry {id_} not found")
    return start_timer(con, {"description": e["description"],
                             "project_id": e["project_id"], "task_id": e["task_id"],
                             "billable": e["billable"],
                             "tag_ids": [t["id"] for t in e["tags"]]})


def duplicate_entry(con, id_):
    e = _snap(con, id_)
    if not e:
        raise Invalid(f"entry {id_} not found")
    if e["end_utc"] is None:
        raise Invalid("cannot duplicate a running entry")
    return create_entry(con, {k: e[k] for k in (
        "description", "project_id", "task_id", "start_utc", "end_utc",
        "billable", "hourly_rate")} | {"tag_ids": e["tag_ids"]})


def suggestions(con, q="", limit=12):
    """Recent distinct description/project/task combos for the tracker input."""
    rows = con.execute(
        "SELECT e.description, e.project_id, e.task_id, MAX(e.start_utc) AS last "
        "FROM entries e WHERE e.description LIKE ? AND e.description <> '' "
        "GROUP BY e.description, e.project_id, e.task_id "
        "ORDER BY last DESC LIMIT ?", (f"%{q}%", limit)).fetchall()
    return [dict(r) for r in rows]


def overlaps(con, start_utc, end_utc, exclude_id=None):
    return [dict(r) for r in con.execute(
        "SELECT id, description, start_utc, end_utc FROM entries "
        "WHERE start_utc < ? AND COALESCE(end_utc, '9999') > ? AND id IS NOT ?",
        (end_utc, start_utc, exclude_id))]


__all__ = ["Invalid", "create", "update", "delete", "meta", "set_settings",
           "list_entries", "count_entries", "get_entry", "create_entry",
           "update_entry", "update_entry_with", "delete_entries", "restore_entry",
           "recent_deletes", "running", "start_timer", "switch_timer", "stop_timer",
           "discard_timer", "continue_entry", "duplicate_entry", "suggestions", "seconds"]
