"""MCP server: DuTemps as tools for Claude Code, Codex and other agents.

    python -m dutemps [--db PATH] mcp

The Model Context Protocol's stdio transport on the standard library:
JSON-RPC 2.0, one UTF-8 JSON message per line on stdin and stdout. stdout
carries protocol messages and nothing else; logs go to stderr.

Everything the app does has a tool, and every tool calls the functions the
HTTP API calls (core, reports, exports, importers, invoices), so validation,
the audit log and the frozen-invoice rules are the same as in the app. Dates
and times going in and out are local, in the timezone on Settings. Issuing,
voiding and deleting an invoice take the invoice number twice, so one can't
happen by a slip.

The app may be open on the same database at the same time. db.connect turns
on WAL and a 5 s busy timeout, and every write is one short BEGIN IMMEDIATE
transaction, so the two processes take turns instead of failing.
"""
import contextlib
import difflib
import json
import math
import os
import re
import sqlite3
import sys
import traceback
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfoNotFoundError

from . import __version__ as VERSION
from . import core, db, exports, importers, invoices, reports
from .timeutil import fmt_utc, hms, local, parse_utc, seconds, week_start, zone

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")     # newest first
NO_BATCHES = ("2025-06-18",)    # JSON-RPC batching was dropped from the protocol here
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND = -32700, -32600, -32601
INVALID_PARAMS, INTERNAL_ERROR = -32602, -32603
MAX_LIMIT = 500
MAX_SECONDS = 24 * 3600         # a longer manual entry is refused as a likely typo
PERIODS = ("today", "yesterday", "this_week", "last_week", "this_month",
           "last_month", "this_year", "last_year")

INSTRUCTIONS = (
    "DuTemps is the user's local, single-user time tracker "
    "with PDF invoices. Use these tools to run the timer, log and correct time "
    "entries, read reports and draft invoices. Dates and times you send and get back "
    "are local, in the time zone on the user's Settings; get_status shows it with "
    "today's date. Dates accept YYYY-MM-DD, 'today' or 'yesterday'; times accept "
    "HH:MM. On the night the clocks go back, a time in the repeated hour needs its "
    "UTC offset ('01:30-05:00'); a time in the hour skipped when they go forward is "
    "refused. Refer to projects, tasks, clients and tags by name (case-insensitive) or "
    "id; an ambiguous or unknown name returns an error listing the candidates with "
    "their ids. Durations come back as seconds, h:mm:ss and decimal hours. Deleted "
    "entries can be brought back with restore_entry. Files (exports, PDFs, backups) "
    "go to the path given, or beside the database, and never overwrite a file. "
    "Invoices: drafts can be changed freely; issue_invoice and void_invoice can't be "
    "undone, so use them only when the user asks, and pass the invoice number again "
    "as confirm.")


class ToolError(Exception):
    """Bad arguments or a domain rule: returned as a tool result with isError."""


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def log(msg):
    print(f"dutemps-mcp: {msg}", file=sys.stderr, flush=True)


# --- dates, times and durations (local, in the Settings timezone) ------------

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CLOCK_RE = re.compile(r"^(\d{1,2})(?::?(\d{2}))?(?::(\d{2}))?(a|am|p|pm)?$")
STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}|today|yesterday)[ T]+(.+)$", re.I)
OFFSET_RE = re.compile(r"^(.*?)\s*(?:([+-])(\d{1,2})(?::?(\d{2}))?|(z))$", re.I)


def _tz(con):
    return db.settings(con)["timezone"]


def _now(con):
    """Now in the Settings timezone: for its local date and clock. Compare
    instants with _utcnow(): arithmetic on a local time drops its fold, so in
    the hour repeated when the clocks go back it can land an hour off."""
    return datetime.now(zone(_tz(con)))


def _utcnow():
    return datetime.now(timezone.utc)


def _today(con):
    return _now(con).date()


def _day(con, v, field):
    """'YYYY-MM-DD', 'today' or 'yesterday' -> date; None when not given."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    s = str(v).strip().lower()
    if s == "today":
        return _today(con)
    if s == "yesterday":
        return _today(con) - timedelta(days=1)
    if DATE_RE.match(s):
        try:
            return date.fromisoformat(s)
        except ValueError:
            pass
    raise ToolError(f"{field} must be a date as YYYY-MM-DD, 'today' or 'yesterday', "
                    f"not {v!r}")


def _clock(v, field, shown=None):
    """'14:30', '9:05:30', '2:30pm' -> (hour, minute, second); as the app reads them."""
    m = CLOCK_RE.match(re.sub(r"\s+", "", str(v).lower()))
    if m:
        h, mi, se = int(m[1]), int(m[2] or 0), int(m[3] or 0)
        if m[4]:
            h = h % 12 + (12 if m[4][0] == "p" else 0) if 1 <= h <= 12 else 99
        if h <= 23 and mi <= 59 and se <= 59:
            return h, mi, se
    raise ToolError(f"{field} must be a local time as HH:MM (24-hour, or with am/pm), "
                    "optionally after a date ('YYYY-MM-DD HH:MM'), not "
                    f"{v if shown is None else shown!r}")


def _offset(td):
    m = round(td.total_seconds() / 60)
    return f"{'-' if m < 0 else '+'}{abs(m) // 60:02d}:{abs(m) % 60:02d}"


def _readings(wall, z):
    """The instants a naive local wall time can be in zone z, as {UTC offset:
    UTC datetime}, earlier first: none for a time the clocks skip, two for one
    they repeat. A reading is real if converting it to UTC and back keeps it."""
    seen = {}
    for fold in (0, 1):
        dt = wall.replace(tzinfo=z, fold=fold)
        if dt.astimezone(timezone.utc).astimezone(z).replace(tzinfo=None) == wall:
            seen.setdefault(dt.utcoffset(), dt.astimezone(timezone.utc))
    return seen


def _instant(wall, tz, off, field):
    """A naive local wall time in zone tz -> UTC string. A time the clocks skip
    when daylight saving time starts is refused. One they repeat when it ends is
    refused too unless off, its UTC offset, says which of the two is meant."""
    seen = _readings(wall, zone(tz))
    secs = ":%S" if wall.second else ""
    shown, clock = wall.strftime("%Y-%m-%d %H:%M" + secs), wall.strftime("%H:%M" + secs)
    if off is not None and off in seen:
        return fmt_utc(seen[off])
    if off is not None and seen:
        raise ToolError(f"{field} {shown} is not at UTC{_offset(off)} in {tz}; there it "
                        "is at " + " or ".join(f"UTC{_offset(o)}" for o in seen))
    if not seen:
        raise ToolError(f"{field} {shown} doesn't exist in {tz}: the clocks skip it when "
                        "daylight saving time starts. Use a time before or after the "
                        "skipped hour.")
    if len(seen) > 1:
        first, second = seen
        raise ToolError(f"{field} {shown} happens twice in {tz}: the clocks go back when "
                        "daylight saving time ends. Add the UTC offset to say which: "
                        f"'{clock}{_offset(first)}' (the earlier) or "
                        f"'{clock}{_offset(second)}' (the later).")
    return fmt_utc(next(iter(seen.values())))


def _parse_moment(con, v, field):
    """'HH:MM' or 'YYYY-MM-DD HH:MM', optionally ending in a UTC offset
    ('01:30-05:00') -> (the date given or None, the time, the offset or None)."""
    s, day, off = str(v).strip(), None, None
    m = STAMP_RE.match(s)
    if m:
        day, s = _day(con, m[1], field), m[2]
    o = OFFSET_RE.match(s)
    if o:
        s = o[1]
        oh, om = int(o[3] or 0), int(o[4] or 0)
        if oh > 14 or om > 59:
            raise ToolError(f"{field} {v!r} has a bad UTC offset; write it as -HH:MM or "
                            "+HH:MM, e.g. '01:30-05:00'")
        off = timedelta(hours=oh, minutes=om) * (-1 if o[2] == "-" else 1)
    return day, dtime(*_clock(s, field, v)), off


def _moment(con, v, field, day):
    """A local time on `day`, or on the date it gives -> (UTC string, date given?).
    An offset is checked against the zone's rules on that date."""
    given, t, off = _parse_moment(con, v, field)
    return (_instant(datetime.combine(given or day, t), _tz(con), off, field),
            given is not None)


def _recent(con, v, field):
    """A time that has already happened: a bare HH:MM later than now is taken as
    yesterday's, as the app does when the timer's start is edited. The date is
    settled first and the time checked on it, so '23:00-05:00' just after a
    clock change is last night's. Instants are compared in UTC."""
    now, now_u = _now(con), _utcnow()
    day, t, off = _parse_moment(con, v, field)
    if day is None:
        day = now.date()
        wall = datetime.combine(day, t)
        if off is not None:             # the instant it names on today's date
            come = (wall - off).replace(tzinfo=timezone.utc) <= now_u
        else:
            seen = _readings(wall, zone(_tz(con)))
            come = (any(u <= now_u for u in seen.values()) if seen
                    else wall <= now.replace(tzinfo=None))     # skipped: by the clock
        if not come:
            day -= timedelta(days=1)
    utc = _instant(datetime.combine(day, t), _tz(con), off, field)
    if parse_utc(utc) > now_u + timedelta(minutes=1):
        raise ToolError(f"{field} {_local(utc, _tz(con))} is in the future")
    return utc


def _duration(v, field="duration"):
    """'1:30', '1:30:00', '1.5', '90m', '1h30m', or a number of hours -> seconds."""
    sec = None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        sec = round(float(v) * 3600)
    elif isinstance(v, str):
        s = re.sub(r"\s+", "", v.lower())
        m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", s)
        if m and int(m[2]) < 60 and int(m[3] or 0) < 60:
            sec = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3] or 0)
        elif re.fullmatch(r"\d*\.?\d+", s):
            sec = round(float(s) * 3600)
        else:
            m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h(?:ours?|rs?)?)?(?:(\d+)m(?:in(?:utes?|s)?)?)?"
                             r"(?:(\d+)s(?:ec(?:onds?|s)?)?)?", s)
            if m and any(m.groups()):
                sec = round(float(m[1] or 0) * 3600) + int(m[2] or 0) * 60 + int(m[3] or 0)
    if sec is None:
        raise ToolError(f"{field} must be decimal hours (1.5), H:MM (1:30) or like "
                        f"1h30m / 45m, not {v!r}")
    if sec <= 0:
        raise ToolError(f"{field} must be more than zero")
    if sec > MAX_SECONDS:
        raise ToolError(f"{field} {hms(sec)} is more than 24 hours; split it by day "
                        "(for minutes, write e.g. '90m')")
    return sec


def _shift(utc, secs):
    return fmt_utc(parse_utc(utc) + timedelta(seconds=secs))


def _local(utc, tz):
    return local(utc, tz).strftime("%Y-%m-%d %H:%M:%S") if utc else None


def _dur(sec):
    sec = int(sec)
    return {"seconds": sec, "duration": hms(sec), "hours": round(sec / 3600, 2)}


def _month_end(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _period(con, name):
    t = _today(con)
    if name == "today":
        return t, t
    if name == "yesterday":
        return t - timedelta(days=1), t - timedelta(days=1)
    if name in ("this_week", "last_week"):
        w0 = week_start(t, int(db.settings(con)["week_start"]))
        w0 -= timedelta(days=7 if name == "last_week" else 0)
        return w0, w0 + timedelta(days=6)
    if name == "this_month":
        return t.replace(day=1), _month_end(t)
    if name == "last_month":
        end = t.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    y = t.year - (name == "last_year")
    return date(y, 1, 1), date(y, 12, 31)


# --- names or ids --------------------------------------------------------------

ROWS_SQL = {
    "project": "SELECT p.id, p.name, p.client_id, c.name AS client, p.archived "
               "FROM projects p LEFT JOIN clients c ON c.id = p.client_id "
               "ORDER BY p.archived, p.name COLLATE NOCASE, c.name COLLATE NOCASE",
    "task": "SELECT t.id, t.name, t.project_id, p.name AS project, t.done AS archived "
            "FROM tasks t JOIN projects p ON p.id = t.project_id "
            "ORDER BY t.done, p.name COLLATE NOCASE, t.name COLLATE NOCASE",
    "client": "SELECT id, name, archived FROM clients ORDER BY archived, name COLLATE NOCASE",
    "tag": "SELECT id, name, archived FROM tags ORDER BY archived, name COLLATE NOCASE",
}


def _rows(con, kind):
    return [dict(r) for r in con.execute(ROWS_SQL[kind])]


def _label(kind, r):
    if kind == "project":
        s = f"{r['name']} (id {r['id']}, " + (
            f"client {r['client']}" if r["client"] else "no client")
    elif kind == "task":
        s = f"{r['project']} / {r['name']} (id {r['id']}"
    else:
        s = f"{r['name']} (id {r['id']}"
    if r["archived"]:
        s += ", done" if kind == "task" else ", archived"
    return s + ")"


def _known(kind, rows, key=None):
    """The tail of a 'no such name' message: close names, else what there is."""
    if not rows:
        return f"; there are none yet (create_{kind} adds one)"
    near = []
    if key:
        close = set(difflib.get_close_matches(key, [r["name"].casefold() for r in rows],
                                              n=8, cutoff=0.6))
        near = [r for r in rows if key in r["name"].casefold()
                or r["name"].casefold() in close]
    if near:
        return ". Did you mean: " + "; ".join(_label(kind, r) for r in near[:10])
    shown = [r for r in rows if not r["archived"]] or rows
    more = len(shown) - 25
    return (f". Known {kind}s: " + "; ".join(_label(kind, r) for r in shown[:25])
            + (f"; and {more} more" if more > 0 else ""))


def _resolve(con, kind, value, rows=None, scope=""):
    """One row of `kind` from an id or a name (case-insensitive, exact). A string
    is a name first; a string of digits that names nothing is an id."""
    rows = _rows(con, kind) if rows is None else rows
    where = f" {scope}" if scope else ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        for r in rows:
            if r["id"] == value:
                return r
        raise ToolError(f"no {kind} with id {value}{where}" + _known(kind, rows))
    s = str(value).strip()
    if not s:
        raise ToolError(f"the {kind} name is empty")
    key = s.casefold()
    hit = [r for r in rows if r["name"].strip().casefold() == key]
    if len(hit) == 1:
        return hit[0]
    if hit:
        raise ToolError(f"{len(hit)} {kind}s are named {s!r}{where}; pass the id of the "
                        "one you mean: " + "; ".join(_label(kind, r) for r in hit))
    if s.isdigit():
        for r in rows:
            if r["id"] == int(s):
                return r
    raise ToolError(f"no {kind} named {s!r}{where}" + _known(kind, rows, key))


def _given(v):
    return v is not None and v != ""


def _project_task(con, project=None, task=None, client_id=None):
    """(project_id, task_id) from names or ids. A task named without a project
    must be unique across projects; its project is then used."""
    pid = tid = None
    if _given(project):
        rows, scope = _rows(con, "project"), ""
        if client_id is not None:
            rows = [r for r in rows if r["client_id"] == client_id]
            scope = "for that client"
        p = _resolve(con, "project", project, rows, scope)
        pid = p["id"]
    if _given(task):
        rows, scope = _rows(con, "task"), ""
        if pid is not None:
            rows = [r for r in rows if r["project_id"] == pid]
            scope = f"in project {p['name']}"
        t = _resolve(con, "task", task, rows, scope)
        tid, pid = t["id"], t["project_id"]
    return pid, tid


def _tag_ids(con, values):
    rows = _rows(con, "tag")
    return sorted({_resolve(con, "tag", v, rows)["id"] for v in values or []})


def _dates(con, a, need=False):
    if a.get("period"):
        if _given(a.get("from")) or _given(a.get("to")):
            raise ToolError("give either period or from/to, not both")
        return _period(con, a["period"])
    d1, d2 = _day(con, a.get("from"), "from"), _day(con, a.get("to"), "to")
    if need and not (d1 and d2):
        raise ToolError("give a period (such as 'this_week') or both from and to")
    if d1 and d2 and d1 > d2:
        raise ToolError(f"from ({d1}) is after to ({d2})")
    return d1, d2


def _filters(con, a, need_dates=False):
    """Tool filter arguments -> the filter dict core.entry_where takes."""
    f = {}
    d1, d2 = _dates(con, a, need_dates)
    if d1:
        f["from"] = d1.isoformat()
    if d2:
        f["to"] = d2.isoformat()
    # Several of a kind match any of them; different kinds must all match, as in
    # the app. Projects are looked up among the clients given, tasks among the
    # projects given.
    rows, scope = {}, {}
    for kind in ("client", "project", "task", "tag"):
        vs = a.get(kind)
        vs = [v for v in (vs if isinstance(vs, list) else [vs]) if _given(v)]
        if not vs:
            continue
        got = [_resolve(con, kind, v, rows.get(kind), scope.get(kind, "")) for v in vs]
        f[kind] = sorted({r["id"] for r in got})
        if kind == "client":
            rows["project"] = [r for r in _rows(con, "project")
                               if r["client_id"] in f["client"]]
            scope["project"] = "for that client" if len(got) == 1 else "for those clients"
        elif kind == "project":
            rows["task"] = [r for r in _rows(con, "task") if r["project_id"] in f["project"]]
            scope["task"] = (f"in project {got[0]['name']}" if len(f["project"]) == 1
                             else "in those projects")
    if a.get("billable") is not None:
        f["billable"] = "true" if a["billable"] else "false"
    if _given(a.get("text")):
        f["q"] = str(a["text"])
    if a.get("no_project"):
        f["no_project"] = True
    return f


# --- output shapes ---------------------------------------------------------------

def _entry(e, tz):
    if not e:
        return None
    return {"id": e["id"], "description": e["description"],
            "project": e["project"], "project_id": e["project_id"],
            "task": e["task"], "task_id": e["task_id"], "client": e["client"],
            "tags": [t["name"] for t in e["tags"]], "billable": bool(e["billable"]),
            "start": _local(e["start_utc"], tz), "end": _local(e["end_utc"], tz),
            "running": e["running"], **_dur(e["seconds"]),
            "rate": e["rate"], "amount": e["amount"], "source": e["source"]}


def _group(g):
    out = {"name": g["name"], **_dur(g["seconds"]),
           "billable_seconds": g["billable_seconds"],
           "billable_hours": round(g["billable_seconds"] / 3600, 2),
           "amount": g["amount"], "entries": g["entries"]}
    if g.get("children"):
        out["children"] = [_group(c) for c in g["children"]]
    return out


def _overlap_note(con, out, s_utc, e_utc, own_id, tz):
    ov = core.overlaps(con, s_utc, e_utc or "9999", own_id)
    if ov:
        out["overlaps"] = [{"id": o["id"], "description": o["description"],
                            "start": _local(o["start_utc"], tz),
                            "end": _local(o["end_utc"], tz)} for o in ov[:10]]
        out["warning"] = (f"this entry overlaps {len(ov)} other entr"
                          f"{'y' if len(ov) == 1 else 'ies'}; fix them if that is not "
                          "intended")
    return out


def _find_invoice(con, v):
    """An invoice row by number (case-insensitive) or id."""
    rows = [dict(r) for r in con.execute(
        "SELECT id, number, status FROM invoices ORDER BY issued DESC, id DESC")]
    if isinstance(v, int) and not isinstance(v, bool):
        hit = [r for r in rows if r["id"] == v]
    else:
        s = str(v).strip()
        hit = [r for r in rows if r["number"].casefold() == s.casefold()]
        if not hit and s.isdigit():
            hit = [r for r in rows if r["id"] == int(s)]
    if hit:
        return hit[0]
    known = "; ".join(f"{r['number']} (id {r['id']}, {r['status']})" for r in rows[:15])
    raise ToolError(f"no invoice {v!r}" + (f". Invoices: {known}" if known
                                           else "; there are no invoices yet"))


FROZEN = ("This invoice is {status} and frozen. A wrong invoice is voided "
          "(void_invoice) and a new draft made.")


def _invoice_out(g):
    out = {k: g[k] for k in ("id", "number", "status", "issued", "period_from",
                             "period_to", "retainer", "retainer_date", "credit_pcts",
                             "note", "created_utc", "updated_utc", "issued_utc",
                             "voided_utc")}
    out["editable"] = g["status"] == "draft"
    for k in ("invoice", "error", "live_hours"):
        if k in g:
            out[k] = g[k]
    if g["status"] != "draft":
        out["frozen"] = FROZEN.format(status=g["status"])
    return out


def _invoice_body(con, a):
    """Invoice tool arguments -> the body invoices.create/update/preview take."""
    b = {}
    for k in ("number", "note"):
        if a.get(k) is not None:
            b[k] = str(a[k])
    for k in ("issued", "period_from", "period_to", "retainer_date"):
        if a.get(k) is not None:
            d = _day(con, a[k], k)
            b[k] = d.isoformat() if d else ""
    for k in ("retainer", "credit_pcts"):
        if a.get(k) is not None:
            b[k] = a[k]
    return b


@contextlib.contextmanager
def _one_read(con):
    """The reads inside see one state of the database: a read transaction, which
    WAL keeps steady while the app writes. Inside a transaction already, nothing."""
    if con.in_transaction:
        yield
        return
    con.execute("BEGIN")
    try:
        yield
    finally:
        if con.in_transaction:
            con.execute("COMMIT")


# --- tools -------------------------------------------------------------------------

TOOLS = {}


def tool(name, title, description, properties=None, required=(), read_only=False,
         destructive=False, idempotent=None):
    schema = {"type": "object", "properties": properties or {},
              "additionalProperties": False}
    if required:
        schema["required"] = list(required)

    def register(fn):
        TOOLS[name] = {"fn": fn, "spec": {
            "name": name, "title": title, "description": description,
            "inputSchema": schema,
            "annotations": {"title": title, "readOnlyHint": read_only,
                            "destructiveHint": destructive,
                            "idempotentHint": read_only if idempotent is None else idempotent,
                            "openWorldHint": False}}}
        return fn
    return register


def _s(desc, **kw):
    return {"type": "string", "description": desc, **kw}


def _i(desc, **kw):
    return {"type": "integer", "description": desc, **kw}


def _n(desc, nullable=False, **kw):
    return {"type": ["number", "null"] if nullable else "number", "description": desc,
            **kw}


def _b(desc):
    return {"type": "boolean", "description": desc}


def _a(desc, items):
    return {"type": "array", "description": desc, "items": items}


DATE = "YYYY-MM-DD, 'today' or 'yesterday' (local)."
TIME_NOTE = (" It may end in a UTC offset, e.g. '01:30-05:00'; one is required for a time "
             "in the hour repeated when the clocks go back. A time in the hour skipped "
             "when they go forward is refused.")
PROJECT = _s("Project name (case-insensitive) or id.")
TASK = _s("Task name or id. Without a project, the task's own project is used, so the "
          "name must then be unique.")
TAGS = _a("Tag names or ids (they must exist; create_tag adds one).", {"type": "string"})
BILLABLE = _b("Billable or not. Default: the project's setting.")
DESCRIPTION = _s("What was done.")
FILTERS = {
    "period": _s("A date range by name, instead of from/to.", enum=list(PERIODS)),
    "from": _s("First local date, inclusive: " + DATE),
    "to": _s("Last local date, inclusive: " + DATE),
    "client": _a("Only these clients (names or ids; one is fine).", {"type": "string"}),
    "project": _a("Only these projects (names or ids; one is fine).",
                  {"type": "string"}),
    "task": _a("Only these tasks (names or ids; one is fine).", {"type": "string"}),
    "tag": _a("Only entries with any of these tags (names or ids; one is fine).",
              {"type": "string"}),
    "billable": _b("Only billable (true) or only non-billable (false) entries."),
    "text": _s("Only entries whose description contains this text."),
    "no_project": _b("Only entries without a project."),
}
INVOICE = {
    "number": _s("Invoice number; unique among all invoices."),
    "issued": _s("Invoice date: " + DATE + " Default: today."),
    "period_from": _s("First day billed: " + DATE),
    "period_to": _s("Last day billed: " + DATE),
    "retainer": _n("Retainer already paid, as a positive amount; it is deducted. "
                   "0 for none.", minimum=0),
    "retainer_date": _s("Date the retainer was paid (printed; required with a "
                        "retainer): " + DATE),
    "credit_pcts": _a("This invoice's credit percentages (0-100), one per credit in "
                      "Invoice settings, in that order. Default: the settings' "
                      "percentages.", {"type": "number"}),
    "note": _s("Internal note, never printed."),
}
NOT_ISSUE = (" It never issues: issue_invoice does, when the user asks.")


@tool("get_status", "Timer status and totals",
      "Today's local date, time and time zone, the running timer (if any), and the "
      "hours tracked today, this week and this month, counting the running timer. "
      "Call this first to learn the user's 'today'.", read_only=True)
def get_status(con, a):
    s = db.settings(con)
    tz = s["timezone"]
    now = datetime.now(zone(tz))
    today = now.date()
    w0 = week_start(today, int(s["week_start"]))

    def total(d1, d2):
        es = core.list_entries(con, {"from": d1.isoformat(), "to": d2.isoformat()})
        bill = sum(e["seconds"] for e in es if e["billable"])
        return {**_dur(sum(e["seconds"] for e in es)), "billable_hours": round(bill / 3600, 2),
                "amount": round(sum(e["amount"] for e in es), 2), "entries": len(es)}

    return {"now": now.strftime("%Y-%m-%d %H:%M"), "today": today.isoformat(),
            "weekday": now.strftime("%A"), "timezone": tz, "currency": s["currency"],
            "running": _entry(core.running(con), tz),
            "today_total": total(today, today),
            "week": {"from": w0.isoformat(), "to": (w0 + timedelta(days=6)).isoformat(),
                     **total(w0, w0 + timedelta(days=6))},
            "month": {"from": today.replace(day=1).isoformat(),
                      "to": _month_end(today).isoformat(),
                      **total(today.replace(day=1), _month_end(today))}}


@tool("get_settings", "Settings",
      "The app settings (time zone, currency, default rate, week start, formats), the "
      "database file in use, whether the invoice profile is still the shipped example, "
      "and the groupings report_summary accepts.", read_only=True)
def get_settings(con, a):
    return {"settings": db.settings(con),
            "database": con.execute("PRAGMA database_list").fetchone()["file"] or ":memory:",
            "invoice_profile": "saved" if os.path.exists(invoices.PROFILE_PATH)
            else "example (the user must save their own in the app before issuing)",
            "report_groupings": list(reports.GROUPS)}


@tool("list_projects", "List projects",
      "Projects with their client, billable flag, rates and tasks. The effective rate "
      "follows the app's chain: project, then client, then the default rate (a task or "
      "entry rate overrides it).",
      {"include_archived": _b("Include archived projects and done tasks. Default false."),
       "client": _s("Only this client's projects (name or id).")}, read_only=True)
def list_projects(con, a):
    m = core.meta(con)
    default = float(m["settings"].get("default_rate") or 0)
    clients = {c["id"]: c for c in m["clients"]}
    cid = _resolve(con, "client", a["client"])["id"] if _given(a.get("client")) else None
    every = bool(a.get("include_archived"))
    tasks = {}
    for t in m["tasks"]:
        if every or not t["done"]:
            tasks.setdefault(t["project_id"], []).append(
                {"id": t["id"], "name": t["name"], "done": bool(t["done"]),
                 "hourly_rate": t["hourly_rate"], "estimate_hours": t["estimate_hours"]})
    out = []
    for p in m["projects"]:
        if (p["archived"] and not every) or (cid is not None and p["client_id"] != cid):
            continue
        c = clients.get(p["client_id"]) or {}
        rate = next((r for r in (p["hourly_rate"], c.get("hourly_rate")) if r is not None),
                    default)
        out.append({"id": p["id"], "name": p["name"], "client": p["client"],
                    "client_id": p["client_id"], "billable": bool(p["billable"]),
                    "archived": bool(p["archived"]), "hourly_rate": p["hourly_rate"],
                    "effective_rate": rate, "estimate_hours": p["estimate_hours"],
                    "note": p["note"], "tasks": tasks.get(p["id"], [])})
    return {"projects": out, "count": len(out)}


@tool("list_clients", "List clients", "Clients with their rate and number of projects.",
      {"include_archived": _b("Include archived clients. Default false.")}, read_only=True)
def list_clients(con, a):
    m = core.meta(con)
    n = {}
    for p in m["projects"]:
        n[p["client_id"]] = n.get(p["client_id"], 0) + 1
    out = [{"id": c["id"], "name": c["name"], "hourly_rate": c["hourly_rate"],
            "archived": bool(c["archived"]), "note": c["note"], "projects": n.get(c["id"], 0)}
           for c in m["clients"] if a.get("include_archived") or not c["archived"]]
    return {"clients": out, "count": len(out)}


@tool("list_tags", "List tags", "Tags that can be put on entries.",
      {"include_archived": _b("Include archived tags. Default false.")}, read_only=True)
def list_tags(con, a):
    out = [{"id": t["id"], "name": t["name"], "archived": bool(t["archived"])}
           for t in core.meta(con)["tags"] if a.get("include_archived") or not t["archived"]]
    return {"tags": out, "count": len(out)}


@tool("list_entries", "List time entries",
      "Time entries, newest first, with filters. Dates filter on the local start date. "
      "Start and end come back as local 'YYYY-MM-DD HH:MM:SS'. For totals over a range, "
      "use report_summary.",
      {**FILTERS,
       "limit": _i(f"At most this many entries (1-{MAX_LIMIT}). Default 50.",
                   minimum=1, maximum=MAX_LIMIT),
       "offset": _i("Skip this many entries first, for paging. Default 0.", minimum=0),
       "order": _s("newest (default) or oldest first.", enum=["newest", "oldest"])},
      read_only=True)
def list_entries(con, a):
    f = _filters(con, a)
    limit, offset = a.get("limit") or 50, a.get("offset") or 0
    es = core.list_entries(con, f, limit=limit, offset=offset,
                           order="ASC" if a.get("order") == "oldest" else "DESC")
    count = core.count_entries(con, f)
    tz = _tz(con)
    return {"from": f.get("from"), "to": f.get("to"), "timezone": tz, "count": count,
            "offset": offset, "returned": len(es), "has_more": offset + len(es) < count,
            "page_total": _dur(sum(e["seconds"] for e in es)),
            "entries": [_entry(e, tz) for e in es]}


@tool("list_deleted_entries", "Recently deleted entries",
      "Entries deleted recently and not yet restored (the app's Recently deleted), "
      "newest first, with the audit_id that restore_entry takes.",
      {"limit": _i("How many deletions to look back over (1-100). Default 20.",
                   minimum=1, maximum=100)}, read_only=True)
def list_deleted_entries(con, a):
    tz = _tz(con)
    out = []
    for d in core.recent_deletes(con, a.get("limit") or 20):
        x = {"audit_id": d["audit_id"], "deleted_at": _local(d["at_utc"], tz),
             "description": d["description"], "start": _local(d["start_utc"], tz),
             "end": _local(d["end_utc"], tz)}
        if d["end_utc"]:
            x.update(_dur(seconds(d["start_utc"], d["end_utc"])))
        out.append(x)
    return {"deleted": out, "count": len(out)}


@tool("report_summary", "Summary report",
      "Hours, billable hours and amounts over a date range, grouped one or two levels "
      "deep (as the app's Summary report). Only finished entries count; the running "
      "timer does not. Grouped by tag, an entry with several tags counts under each.",
      {**FILTERS,
       "group1": _s("First grouping. Default project.", enum=list(reports.GROUPS)),
       "group2": _s("Optional second grouping, inside the first.",
                    enum=list(reports.GROUPS))}, read_only=True)
def report_summary(con, a):
    f = _filters(con, a, need_dates=True)
    r = reports.summary(con, f, a.get("group1") or "project", a.get("group2") or None)
    total = _group({**r["total"], "name": "total"})
    del total["name"]
    return {"from": f["from"], "to": f["to"], "timezone": _tz(con),
            "currency": db.settings(con)["currency"], "group1": r["group1"],
            "group2": r["group2"], "total": total, "groups": [_group(g) for g in r["groups"]]}


@tool("invoice_lines", "Invoice lines",
      "Billable time as it is invoiced: one line per local date, client, project and "
      "task, each rounded to the nearest 0.25 h with a 0.25 h minimum, so totals can "
      "differ slightly from report_summary. Covers every client; an invoice bills only "
      "the client chosen in Invoice settings.",
      {k: FILTERS[k] for k in ("period", "from", "to", "client", "project", "task",
                               "tag", "text")}, read_only=True)
def invoice_lines(con, a):
    f = _filters(con, a, need_dates=True)
    r = reports.invoice_lines(con, f)
    lines = []
    for ln in r["lines"]:
        x = {k: ln[k] for k in ("date", "client", "project", "task", "description",
                                "entries", "raw_hours", "hours", "rate", "amount")}
        if ln.get("mixed_rate"):
            x["mixed_rate"] = True
        lines.append(x)
    return {"from": f["from"], "to": f["to"], "lines": lines, "count": len(lines),
            "hours": r["hours"], "raw_hours": r["raw_hours"], "amount": r["amount"]}


@tool("list_invoices", "List invoices",
      "Invoices, newest first, with hours, total and net (drafts are recalculated from "
      "the entries now; issued and void ones show what was sent). Also returns the "
      "app's suggested values for the next invoice.",
      {"status": _s("Only invoices with this status.", enum=["draft", "issued", "void"])},
      read_only=True)
def list_invoices(con, a):
    out = [x for x in invoices.list_all(con) if not a.get("status")
           or x["status"] == a["status"]]
    try:
        nxt = invoices.defaults(con, _today(con).isoformat())
    except (core.Invalid, KeyError, ValueError) as e:
        nxt = {"error": str(e)}
    return {"invoices": out, "count": len(out), "next_invoice_defaults": nxt,
            "invoice_profile": "saved" if os.path.exists(invoices.PROFILE_PATH)
            else "example"}


@tool("get_invoice", "Get an invoice",
      "One invoice by number or id. A draft is rebuilt from the entries now (or shows "
      "why it can't be built); an issued or void invoice shows its frozen snapshot, and "
      "live_hours shows what the entries add up to today.",
      {"invoice": _s("Invoice number or id."),
       "include_lines": _b("Also return every billed line, by section. Default false.")},
      ["invoice"], read_only=True)
def get_invoice(con, a):
    with _one_read(con):            # the totals and the lines come from one build
        r = _find_invoice(con, a["invoice"])
        g = invoices.get(con, r["id"], lines=bool(a.get("include_lines")))
    out = _invoice_out(g)
    if "sections" in g:
        out["sections"] = g["sections"]
    return out


@tool("preview_invoice", "Preview an invoice",
      "Builds an invoice from parameters without saving anything: hours, sections, "
      "credits, retainer and net, so they can be checked before a draft is made.",
      {**INVOICE, "number": _s("Invoice number. Default 'PREVIEW'.")},
      ["period_from", "period_to"], read_only=True)
def preview_invoice(con, a):
    b = _invoice_body(con, a)
    b.setdefault("number", "PREVIEW")
    b.setdefault("issued", _today(con).isoformat())
    return {"saved": False, "invoice": invoices.preview(con, b)}


@tool("start_timer", "Start the timer",
      "Starts the timer now, or at an earlier start time. A timer already running is "
      "stopped where the new one starts (now, or at that earlier start), in one step, "
      "so the two don't overlap; an earlier start must then be after the running "
      "timer's own start, or nothing changes and an error says so.",
      {"description": DESCRIPTION, "project": PROJECT, "task": TASK, "tags": TAGS,
       "billable": BILLABLE,
       "start": _s("When it started, if earlier than now: local HH:MM (taken as "
                   "yesterday's if that time hasn't come yet today) or "
                   "'YYYY-MM-DD HH:MM'." + TIME_NOTE)})
def start_timer(con, a):
    data = {"description": str(a.get("description") or "")}
    pid, tid = _project_task(con, a.get("project"), a.get("task"))
    if pid:
        data["project_id"] = pid
    if tid:
        data["task_id"] = tid
    if a.get("tags") is not None:
        data["tag_ids"] = _tag_ids(con, a["tags"])
    if a.get("billable") is not None:
        data["billable"] = a["billable"]
    if _given(a.get("start")):
        data["start_utc"] = _recent(con, a["start"], "start")
    # a running timer ends at the new one's start, in the same transaction
    r = core.switch_timer(con, data, stop_at=data.get("start_utc"))
    new, tz = r["started"], _tz(con)
    out = {"started": _entry(new, tz), "stopped": _entry(r["stopped"], tz)}
    if "start_utc" in data:
        _overlap_note(con, out, new["start_utc"], None, new["id"], tz)
    return out


@tool("stop_timer", "Stop the timer", "Stops the running timer, now or at an end time.",
      {"end": _s("When it ended, if earlier than now: local HH:MM (taken as yesterday's "
                 "if that time hasn't come yet today) or 'YYYY-MM-DD HH:MM'."
                 + TIME_NOTE)})
def stop_timer(con, a):
    if not core.running(con):
        raise ToolError("no timer is running")
    end = _recent(con, a["end"], "end") if _given(a.get("end")) else None
    return {"stopped": _entry(core.stop_timer(con, end), _tz(con))}


@tool("log_time", "Log time",
      "Adds a finished time entry. Give start and end, start and duration, end and "
      "duration, or only a duration (it is then placed after that day's last entry, or "
      "at 09:00, as the app's timesheet does). Entries over 24 hours are refused. "
      "Overlaps with other entries are allowed but reported.",
      {"date": _s("Local date of the entry: " + DATE + " Default: today."),
       "start": _s("Local start time HH:MM, or 'YYYY-MM-DD HH:MM'." + TIME_NOTE),
       "end": _s("Local end time HH:MM on the start's date; for an entry past midnight "
                 "give 'YYYY-MM-DD HH:MM'." + TIME_NOTE),
       "duration": _s("Length: decimal hours ('1.5'), H:MM ('1:30') or '1h30m' / '45m'."),
       "description": DESCRIPTION, "project": PROJECT, "task": TASK, "tags": TAGS,
       "billable": BILLABLE})
def log_time(con, a):
    tz = _tz(con)
    pid, tid = _project_task(con, a.get("project"), a.get("task"))
    tag_ids = _tag_ids(con, a["tags"]) if a.get("tags") is not None else None
    day = _day(con, a.get("date"), "date") or _today(con)
    start, end, dur = (a.get(k) if _given(a.get(k)) else None
                       for k in ("start", "end", "duration"))
    if None not in (start, end, dur):
        raise ToolError("give two of start, end and duration, not all three")
    sec = _duration(dur) if dur is not None else None
    s_utc = _moment(con, start, "start", day)[0] if start is not None else None
    e_utc = None
    if end is not None:
        e_utc = _moment(con, end, "end", local(s_utc, tz).date() if s_utc else day)[0]
    if s_utc and sec:
        e_utc = _shift(s_utc, sec)
    elif e_utc and sec:
        s_utc = _shift(e_utc, -sec)
    elif sec:
        ends = [e["end_utc"] for e in core.list_entries(
            con, {"from": day.isoformat(), "to": day.isoformat(), "finished": True})]
        s_utc = max(ends) if ends else _moment(con, "09:00", "start", day)[0]
        e_utc = _shift(s_utc, sec)
    elif not (s_utc and e_utc):
        raise ToolError("give start and end, start and duration, end and duration, "
                        "or just a duration")
    if e_utc <= s_utc:
        raise ToolError(f"end ({_local(e_utc, tz)}) is not after start "
                        f"({_local(s_utc, tz)}); for time past midnight give the end "
                        "with its date, e.g. 'YYYY-MM-DD 01:30'")
    if seconds(s_utc, e_utc) > MAX_SECONDS:
        raise ToolError("an entry can't be longer than 24 hours; split it by day")
    data = {"description": str(a.get("description") or ""), "start_utc": s_utc,
            "end_utc": e_utc}
    if pid:
        data["project_id"] = pid
    if tid:
        data["task_id"] = tid
    if tag_ids is not None:
        data["tag_ids"] = tag_ids
    if a.get("billable") is not None:
        data["billable"] = a["billable"]
    e = core.create_entry(con, data)
    out = _overlap_note(con, {"entry": _entry(e, tz)}, s_utc, e_utc, e["id"], tz)
    if parse_utc(e_utc) > _utcnow():
        out["note"] = "this entry ends in the future"
    return out


@tool("update_entry", "Update an entry",
      "Changes an entry; only the fields given change. Changing the date or start "
      "without an end or duration keeps the entry's length. Moving it to another project "
      "drops a task of the old project. Setting an end on the running timer stops it.",
      {"id": _i("Entry id (from list_entries)."),
       "description": DESCRIPTION,
       "project": _s("Project name or id; an empty string removes the project."),
       "task": _s("Task name or id; an empty string removes the task."),
       "tags": _a("Replaces the entry's tags (names or ids); [] removes them all.",
                  {"type": "string"}),
       "billable": _b("Billable or not."),
       "date": _s("Move to this local date: " + DATE),
       "start": _s("New local start time HH:MM, or 'YYYY-MM-DD HH:MM'." + TIME_NOTE),
       "end": _s("New local end time HH:MM (on the start's date), or 'YYYY-MM-DD HH:MM'."
                 + TIME_NOTE),
       "duration": _s("New length, keeping the start: '1.5', '1:30' or '1h30m'.")},
      ["id"], destructive=True, idempotent=True)
def update_entry(con, a):
    tz = _tz(con)
    day = _day(con, a.get("date"), "date")
    start, end, dur = (a.get(k) if _given(a.get(k)) else None
                       for k in ("start", "end", "duration"))
    if end is not None and dur is not None:
        raise ToolError("give end or duration, not both")
    timed = day or start is not None or end is not None or dur is not None
    # Everything that depends on the entry as it is now (its times, its project)
    # is worked out inside the update's transaction, from the row read there, so
    # an edit made in the app meanwhile is built on and not overwritten.
    e = core.update_entry_with(con, a["id"], lambda cur: _entry_change(
        con, a, cur, tz, day, start, end, dur, timed))
    out = {"entry": _entry(e, tz), "changed": sorted(k for k in a if k != "id")}
    if timed:
        _overlap_note(con, out, e["start_utc"], e["end_utc"], e["id"], tz)
    return out


def _entry_change(con, a, cur, tz, day, start, end, dur, timed):
    """update_entry's arguments -> the change for core, given the entry's row
    `cur` as read inside the update's transaction."""
    data = {}
    if a.get("description") is not None:
        data["description"] = str(a["description"])
    if "project" in a:
        data["project_id"] = (_resolve(con, "project", a["project"])["id"]
                              if _given(a["project"]) else None)
    if "task" in a:
        if _given(a["task"]):
            pid = data.get("project_id", cur["project_id"])
            rows, scope = _rows(con, "task"), ""
            if pid:
                rows = [r for r in rows if r["project_id"] == pid]
                scope = "in that project"
            t = _resolve(con, "task", a["task"], rows, scope)
            data["task_id"], data["project_id"] = t["id"], t["project_id"]
        else:
            data["task_id"] = None
    if a.get("tags") is not None:
        data["tag_ids"] = _tag_ids(con, a["tags"])
    if a.get("billable") is not None:
        data["billable"] = a["billable"]
    if timed:
        cs, ce = cur["start_utc"], cur["end_utc"]
        if start is not None:
            s = _moment(con, start, "start", day or local(cs, tz).date())[0]
        elif day:
            s = _moment(con, local(cs, tz).strftime("%H:%M:%S"), "start", day)[0]
        else:
            s = cs
        if end is not None:
            e = _moment(con, end, "end", local(s, tz).date())[0]
        elif dur is not None:
            e = _shift(s, _duration(dur))
        elif ce and s != cs:
            e = _shift(s, seconds(cs, ce))              # moved: keep its length
        else:
            e = ce
        if e is not None and e <= s:
            raise ToolError(f"end ({_local(e, tz)}) is not after start ({_local(s, tz)}); "
                            "for time past midnight give the end with its date")
        if e is not None and seconds(s, e) > MAX_SECONDS:
            raise ToolError("an entry can't be longer than 24 hours; split it by day")
        if e is None and parse_utc(s) > _utcnow() + timedelta(minutes=1):
            raise ToolError("the running timer can't start in the future")
        if s != cs:
            data["start_utc"] = s
        if e != ce:
            data["end_utc"] = e
    if not data:
        raise ToolError("nothing to change; give at least one field besides id"
                        if len(a) == 1 else "nothing to change; the entry already has "
                        "those times")
    return data


@tool("delete_entries", "Delete entries",
      "Deletes time entries (the running timer too, if its id is given). Each deletion "
      "is kept in the audit log: list_deleted_entries and restore_entry, or Recently "
      "deleted in the app, bring them back.",
      {"ids": _a("Entry ids to delete (at most 200).", {"type": "integer"})},
      ["ids"], destructive=True, idempotent=True)
def delete_entries(con, a):
    ids = list(dict.fromkeys(a["ids"]))
    if not ids:
        raise ToolError("ids is empty")
    if len(ids) > 200:
        raise ToolError("at most 200 entries per call")
    tz = _tz(con)
    r = core.delete_entries(con, ids)
    # Describe what was deleted from the snapshots the delete's own transaction
    # wrote to the audit log, not from a read made before it.
    gone = []
    for i, aid in zip(r["deleted"], r["audit_ids"]):
        at, snap = con.execute("SELECT at_utc, before FROM audit WHERE id = ?",
                               (aid,)).fetchone()
        snap = json.loads(snap)
        p = db.row(con, "projects", snap["project_id"]) if snap["project_id"] else None
        gone.append({"id": i, "audit_id": aid, "description": snap["description"],
                     "project": p["name"] if p else None,
                     "start": _local(snap["start_utc"], tz),
                     "end": _local(snap["end_utc"], tz),
                     **_dur(max(0, seconds(snap["start_utc"], snap["end_utc"] or at)))})
    return {"deleted": gone, "not_found": [i for i in ids if i not in r["deleted"]],
            "undo": "restore_entry with an audit_id puts an entry back"}


@tool("restore_entry", "Restore a deleted entry",
      "Puts back an entry deleted earlier, from its audit_id (see "
      "list_deleted_entries or the result of delete_entries).",
      {"audit_id": _i("The audit_id of the deletion.")}, ["audit_id"], idempotent=True)
def restore_entry(con, a):
    return {"entry": _entry(core.restore_entry(con, a["audit_id"]), _tz(con))}


@tool("create_client", "Create a client", "Adds a client.",
      {"name": _s("Client name; unique."),
       "hourly_rate": _n("Rate for this client's projects without their own.", minimum=0),
       "note": _s("Free text.")}, ["name"])
def create_client(con, a):
    data = {k: a[k] for k in ("name", "hourly_rate", "note") if a.get(k) is not None}
    return {"client": core.create(con, "clients", data)}


@tool("create_project", "Create a project", "Adds a project, optionally for a client.",
      {"name": _s("Project name; unique per client."),
       "client": _s("Client name or id. Default: none."),
       "billable": _b("Whether its time is billable by default. Default true."),
       "hourly_rate": _n("Its rate; default: the client's, then the default rate.",
                         minimum=0),
       "estimate_hours": _n("Estimated hours.", minimum=0),
       "color": _s("Colour as #RRGGBB."),
       "note": _s("Free text.")}, ["name"])
def create_project(con, a):
    data = {k: a[k] for k in ("name", "billable", "hourly_rate", "estimate_hours",
                              "color", "note") if a.get(k) is not None}
    if _given(a.get("client")):
        data["client_id"] = _resolve(con, "client", a["client"])["id"]
    p = core.create(con, "projects", data)
    return {"project": {**p, "client": (db.row(con, "clients", p["client_id"]) or {})
                        .get("name")}}


@tool("create_task", "Create a task", "Adds a task to a project.",
      {"project": PROJECT, "name": _s("Task name; unique within the project."),
       "hourly_rate": _n("Its rate, overriding the project's.", minimum=0),
       "estimate_hours": _n("Estimated hours.", minimum=0)}, ["project", "name"])
def create_task(con, a):
    p = _resolve(con, "project", a["project"])
    data = {k: a[k] for k in ("name", "hourly_rate", "estimate_hours")
            if a.get(k) is not None}
    return {"task": {**core.create(con, "tasks", {**data, "project_id": p["id"]}),
                     "project": p["name"]}}


@tool("create_tag", "Create a tag", "Adds a tag.", {"name": _s("Tag name; unique.")},
      ["name"])
def create_tag(con, a):
    return {"tag": core.create(con, "tags", {"name": a["name"]})}


@tool("create_invoice_draft", "Create an invoice draft",
      "Saves a draft invoice. A draft stores only its parameters and is rebuilt from "
      "the entries whenever it is shown; the result includes its totals, or the reason "
      "it can't be built yet (such as a project with no invoice section)." + NOT_ISSUE,
      INVOICE, ["number", "period_from", "period_to"])
def create_invoice_draft(con, a):
    b = _invoice_body(con, a)
    b.setdefault("issued", _today(con).isoformat())
    return _invoice_out(invoices.create(con, b))


@tool("update_invoice_draft", "Update an invoice draft",
      "Changes a draft invoice's parameters; only the fields given change. Issued and "
      "void invoices are frozen and refused." + NOT_ISSUE,
      {"invoice": _s("Number or id of the draft to change."), **INVOICE,
       "number": _s("A new invoice number.")},
      ["invoice"], destructive=True, idempotent=True)
def update_invoice_draft(con, a):
    r = _find_invoice(con, a["invoice"])
    b = _invoice_body(con, {k: v for k, v in a.items() if k != "invoice"})
    if not b:
        raise ToolError("nothing to change; give at least one field besides invoice")
    return _invoice_out(invoices.update(con, r["id"], b))


# --- entries and the timer, as the tracker's buttons ------------------------------

@tool("duplicate_entry", "Duplicate an entry",
      "Copies a finished entry: same times, description, project, task, tags, billable "
      "flag and rate (the app's Duplicate). Change the copy with update_entry.",
      {"id": _i("Entry id (from list_entries).")}, ["id"])
def duplicate_entry(con, a):
    tz = _tz(con)
    e = core.duplicate_entry(con, a["id"])
    return _overlap_note(con, {"entry": _entry(e, tz)}, e["start_utc"], e["end_utc"],
                         e["id"], tz)


@tool("continue_entry", "Continue an entry",
      "Starts the timer now with an entry's description, project, task, tags and "
      "billable flag (the app's Continue). A running timer is stopped now first.",
      {"id": _i("Entry id (from list_entries).")}, ["id"])
def continue_entry(con, a):
    e = core.get_entry(con, a["id"])
    if not e:
        raise ToolError(f"no entry with id {a['id']}")
    r = core.switch_timer(con, {"description": e["description"],
                                "project_id": e["project_id"], "task_id": e["task_id"],
                                "billable": e["billable"],
                                "tag_ids": [t["id"] for t in e["tags"]]})
    tz = _tz(con)
    return {"started": _entry(r["started"], tz), "stopped": _entry(r["stopped"], tz)}


@tool("discard_timer", "Discard the running timer",
      "Deletes the running timer without keeping its time (the app's Discard). "
      "restore_entry with the audit_id brings it back.", destructive=True)
def discard_timer(con, a):
    r = core.discard_timer(con)
    return {"discarded": r["deleted"], "audit_ids": r["audit_ids"],
            "undo": "restore_entry with the audit_id puts it back"}


@tool("recent_descriptions", "Recent descriptions",
      "Description, project and task combinations used recently, newest first (the "
      "tracker's suggestions), to reuse the user's own wording.",
      {"text": _s("Only descriptions containing this text."),
       "limit": _i("At most this many (1-50). Default 12.", minimum=1, maximum=50)},
      read_only=True)
def recent_descriptions(con, a):
    m = core.meta(con)
    projects = {p["id"]: p["name"] for p in m["projects"]}
    tasks = {t["id"]: t["name"] for t in m["tasks"]}
    tz = _tz(con)
    out = [{"description": r["description"], "project": projects.get(r["project_id"]),
            "task": tasks.get(r["task_id"]), "last_used": _local(r["last"], tz)}
           for r in core.suggestions(con, str(a.get("text") or ""), a.get("limit") or 12)]
    return {"suggestions": out, "count": len(out)}


# --- settings ------------------------------------------------------------------------

@tool("update_settings", "Change settings",
      "Changes the app's settings; only the fields given change.",
      {"timezone": _s("IANA time zone, e.g. 'Europe/Paris'."),
       "currency": _s("Currency code, e.g. 'USD'."),
       "default_rate": _n("Hourly rate for projects and clients without their own.",
                          minimum=0),
       "week_start": _i("First day of the week: 1 = Monday ... 7 = Sunday.",
                        minimum=1, maximum=7),
       "user_name": _s("Your name."),
       "user_email": _s("Your email."),
       "time_format": _s("12- or 24-hour clock in the app.", enum=["12", "24"]),
       "duration_format": _s("Durations as h:mm:ss or decimal hours in the app.",
                             enum=["hms", "decimal"])},
      destructive=True, idempotent=True)
def update_settings(con, a):
    a = {k: v for k, v in a.items() if v is not None}
    if not a:
        raise ToolError("nothing to change; give at least one setting")
    return {"settings": core.set_settings(con, a), "changed": sorted(a)}


# --- clients, projects, tasks and tags -----------------------------------------------

IN_USE = (" One still in use (by entries, or a client by projects) is archived instead, "
          "so past entries keep their labels.")
TASK_PROJECT = _s("The task's project (name or id), when the task name alone is not "
                  "unique.")


def _changes(a, keys):
    data = {k: a[k] for k in keys if k in a}
    if not data:
        raise ToolError(f"nothing to change; give at least one of {', '.join(keys)}")
    return data


@tool("update_client", "Update a client",
      "Changes a client; only the fields given change. A null hourly_rate removes its "
      "own rate.",
      {"client": _s("Client name or id."), "name": _s("New name."),
       "hourly_rate": _n("Its rate; null for none.", minimum=0, nullable=True),
       "archived": _b("Archived or not."), "note": _s("Free text.")},
      ["client"], destructive=True, idempotent=True)
def update_client(con, a):
    c = _resolve(con, "client", a["client"])
    return {"client": core.update(con, "clients", c["id"],
                                  _changes(a, ("name", "hourly_rate", "archived", "note")))}


@tool("update_project", "Update a project",
      "Changes a project; only the fields given change. A null hourly_rate or "
      "estimate_hours removes it; an empty client removes the client.",
      {"project": PROJECT, "name": _s("New name."),
       "client": _s("Client name or id; an empty string for none."),
       "billable": _b("Whether its time is billable by default."),
       "hourly_rate": _n("Its rate; null for none.", minimum=0, nullable=True),
       "estimate_hours": _n("Estimated hours; null for none.", minimum=0, nullable=True),
       "color": _s("Colour as #RRGGBB."), "archived": _b("Archived or not."),
       "note": _s("Free text.")},
      ["project"], destructive=True, idempotent=True)
def update_project(con, a):
    p = _resolve(con, "project", a["project"])
    data = {k: a[k] for k in ("name", "billable", "hourly_rate", "estimate_hours",
                              "color", "archived", "note") if k in a}
    if "client" in a:
        data["client_id"] = (_resolve(con, "client", a["client"])["id"]
                             if _given(a["client"]) else None)
    if not data:
        raise ToolError("nothing to change; give at least one field besides project")
    p = core.update(con, "projects", p["id"], data)
    return {"project": {**p, "client": (db.row(con, "clients", p["client_id"]) or {})
                        .get("name")}}


@tool("update_task", "Update a task",
      "Changes a task; only the fields given change. A null hourly_rate or "
      "estimate_hours removes it.",
      {"task": TASK, "project": TASK_PROJECT, "name": _s("New name."),
       "hourly_rate": _n("Its rate; null for none.", minimum=0, nullable=True),
       "estimate_hours": _n("Estimated hours; null for none.", minimum=0, nullable=True),
       "done": _b("Done (hidden from pickers) or not.")},
      ["task"], destructive=True, idempotent=True)
def update_task(con, a):
    _, tid = _project_task(con, a.get("project"), a["task"])
    t = core.update(con, "tasks", tid,
                    _changes(a, ("name", "hourly_rate", "estimate_hours", "done")))
    return {"task": {**t, "project": db.row(con, "projects", t["project_id"])["name"]}}


@tool("update_tag", "Update a tag", "Renames or archives a tag.",
      {"tag": _s("Tag name or id."), "name": _s("New name."),
       "archived": _b("Archived or not.")},
      ["tag"], destructive=True, idempotent=True)
def update_tag(con, a):
    t = _resolve(con, "tag", a["tag"])
    return {"tag": core.update(con, "tags", t["id"], _changes(a, ("name", "archived")))}


def _remove(con, table, r):
    return {**core.delete(con, table, r["id"]), "id": r["id"], "name": r["name"]}


@tool("delete_client", "Delete a client", "Deletes a client." + IN_USE,
      {"client": _s("Client name or id.")}, ["client"], destructive=True)
def delete_client(con, a):
    return _remove(con, "clients", _resolve(con, "client", a["client"]))


@tool("delete_project", "Delete a project", "Deletes a project." + IN_USE,
      {"project": PROJECT}, ["project"], destructive=True)
def delete_project(con, a):
    return _remove(con, "projects", _resolve(con, "project", a["project"]))


@tool("delete_task", "Delete a task",
      "Deletes a task. One used by entries is marked done instead.",
      {"task": TASK, "project": TASK_PROJECT}, ["task"], destructive=True)
def delete_task(con, a):
    _, tid = _project_task(con, a.get("project"), a["task"])
    return _remove(con, "tasks", db.row(con, "tasks", tid))


@tool("delete_tag", "Delete a tag", "Deletes a tag." + IN_USE,
      {"tag": _s("Tag name or id.")}, ["tag"], destructive=True)
def delete_tag(con, a):
    return _remove(con, "tags", _resolve(con, "tag", a["tag"]))


# --- more reports ----------------------------------------------------------------------

WEEK = _s("Any date in the week: " + DATE + " Default: this week.")
NO_DATES = {k: v for k, v in FILTERS.items() if k not in ("period", "from", "to")}


def _week_of(con, v):
    return week_start(_day(con, v, "week") or _today(con),
                      int(db.settings(con)["week_start"]))


def _hours(sec):
    return round(sec / 3600, 2)


@tool("report_weekly", "Weekly report",
      "One week as a grid: a row per project and task with the hours of each day, and "
      "the totals (the app's Weekly report and timesheet).",
      {"week": WEEK, **NO_DATES}, read_only=True)
def report_weekly(con, a):
    w0 = _week_of(con, a.get("week"))
    r = reports.weekly(con, _filters(con, {k: v for k, v in a.items() if k != "week"}),
                       w0.isoformat())
    return {"days": r["days"],
            "rows": [{"project": x["project"], "task": x["task"], "client": x["client"],
                      "day_hours": [_hours(s) for s in x["days"]], **_dur(x["total"])}
                     for x in r["rows"]],
            "day_totals": [_hours(s) for s in r["totals"]], "total": _dur(r["total"])}


@tool("report_series", "Hours over time",
      "Hours per day or per week, split by project, over a date range (the app's "
      "charts).",
      {**FILTERS, "by": _s("day (default) or week.", enum=["day", "week"])},
      read_only=True)
def report_series(con, a):
    by = a.get("by") or "day"
    f = _filters(con, {k: v for k, v in a.items() if k != "by"}, need_dates=True)
    d1, d2 = f.pop("from"), f.pop("to")
    r = reports.series(con, f, d1, d2, by)
    return {"from": d1, "to": d2, "by": by,
            "buckets": [{"start": b,
                         "hours": {p: _hours(s) for p, s in r["values"][b].items()},
                         "total_hours": _hours(sum(r["values"][b].values()))}
                        for b in r["buckets"]]}


@tool("get_dashboard", "Dashboard",
      "The app's Dashboard: hours and amounts today, this week and this month, the "
      "week's and the month's projects, and the latest entries.", read_only=True)
def get_dashboard(con, a):
    tz = _tz(con)
    r = reports.dashboard(con, _today(con).isoformat())

    def total(t):
        return {**_dur(t["seconds"]), "billable_hours": _hours(t["billable_seconds"]),
                "amount": t["amount"], "entries": t["entries"]}
    return {"today": total(r["today"]),
            "week": {"from": r["week_start"], **total(r["week"]),
                     "projects": [_group(g) for g in r["week_projects"]]},
            "month": {"from": r["month_start"], **total(r["month"]),
                      "projects": [_group(g) for g in r["month_projects"]]},
            "recent": [_entry(e, tz) for e in r["recent"]]}


# --- files: exports, PDFs, imports and backups ---------------------------------------

PATH = _s("File or folder to write to. Default: the exports folder beside the "
          "database. An existing file is never overwritten.")


def _beside_db(con, sub):
    f = con.execute("PRAGMA database_list").fetchone()["file"]
    if not f:
        raise ToolError("the database is in memory; give a path")
    return os.path.join(os.path.dirname(f), sub)


def _safe_name(name):
    """A file name from text such as an invoice number: no folders, nothing
    Windows refuses."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", name).strip(" .")
    return name or "file"


def _out_path(con, path, name):
    """Where a file goes: the path given (a file, or a folder for `name`), or
    the exports folder beside the database. Never an existing file."""
    name = _safe_name(name)
    if _given(path):
        p = os.path.abspath(os.path.expanduser(str(path)))
        if os.path.isdir(p):
            p = os.path.join(p, name)
    else:
        p = os.path.join(_beside_db(con, "exports"), name)
    if os.path.exists(p):
        raise ToolError(f"{p} already exists; give another path")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def _write(path, data):
    try:
        with open(path, "xb") as f:
            f.write(data)
    except FileExistsError:
        raise ToolError(f"{path} already exists; give another path") from None
    return {"path": path, "bytes": len(data)}


@tool("export_report", "Export a report",
      "Writes a report to a CSV or Excel file, as the app's Export does: detailed "
      "(every entry), summary, weekly or invoice lines. Returns the file's path.",
      {**FILTERS,
       "kind": _s("Which report. Default detailed.",
                  enum=["detailed", "summary", "weekly", "invoice"]),
       "format": _s("csv (default) or xlsx.", enum=["csv", "xlsx"]),
       "group1": _s("Summary only: first grouping. Default project.",
                    enum=list(reports.GROUPS)),
       "group2": _s("Summary only: second grouping.", enum=list(reports.GROUPS)),
       "week": _s("Weekly only, instead of dates: any date in the week. Default: this "
                  "week."),
       "path": PATH})
def export_report(con, a):
    kind, fmt = a.get("kind") or "detailed", a.get("format") or "csv"
    monday = None
    if kind == "weekly":
        w0 = _week_of(con, a.get("week"))
        monday, span = w0.isoformat(), f"week-of-{w0}"
        f = _filters(con, {k: a[k] for k in NO_DATES if k in a})
    else:
        f = _filters(con, {k: a[k] for k in FILTERS if k in a}, need_dates=True)
        span = f"{f['from']}_{f['to']}"
    head, rows = exports.table(con, kind, f, a.get("group1") or "project",
                               a.get("group2") or None, monday)
    data = (exports.to_xlsx(head, rows, kind.title()) if fmt == "xlsx"
            else exports.to_csv(head, rows))
    path = _out_path(con, a.get("path"), f"DuTemps_{kind}_{span}.{fmt}")
    return {**_write(path, data), "rows": len(rows)}


@tool("save_invoice_pdf", "Save an invoice PDF",
      "Writes an invoice's PDF to a file and returns its path. A draft is built now and "
      "marked DRAFT; an issued or void invoice gives the document stored when it was "
      "issued.",
      {"invoice": _s("Invoice number or id."), "path": PATH}, ["invoice"])
def save_invoice_pdf(con, a):
    r = _find_invoice(con, a["invoice"])
    name, data = invoices.pdf_for(con, r["id"])
    return {**_write(_out_path(con, a.get("path"), name), data), "number": r["number"],
            "status": r["status"]}


@tool("import_csv", "Import entries from a CSV",
      "Adds the entries in a DuTemps CSV file (start_date, start_time, end_date, "
      "end_time, duration_hours, billable_rate, description, project, client, task, "
      "billable), creating missing clients, projects and tasks. Rows imported before "
      "are skipped. Run it with dry_run first to see what it would add.",
      {"path": _s("The CSV file."),
       "dry_run": _b("Report what would be added and change nothing. Default false.")},
      ["path"])
def import_csv(con, a):
    path = os.path.abspath(os.path.expanduser(a["path"]))
    if not os.path.isfile(path):
        raise ToolError(f"no file {path}")
    try:
        return importers.import_csv(con, path, dry_run=bool(a.get("dry_run")))
    except (KeyError, ValueError) as e:
        raise ToolError(f"{os.path.basename(path)} can't be imported: {e}") from None


@tool("backup_database", "Back up the database",
      "Copies the database to the backups folder beside it (or to a path given) and "
      "returns the copy's path. Worth doing before a large change.",
      {"label": _s("A word or two for the file name, e.g. 'before-import'."),
       "path": _s("Where to write the copy instead. An existing file is never "
                  "overwritten.")})
def backup_database(con, a):
    label = re.sub(r"[^\w-]+", "-", str(a.get("label") or "")).strip("-")
    name = f"dutemps-{datetime.now():%Y%m%d-%H%M%S}" + (f"-{label}" if label else "")
    if _given(a.get("path")):
        dest = _out_path(con, a["path"], name + ".db")
    else:
        dest = os.path.join(_beside_db(con, "backups"), name + ".db")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:        # claim the name first: a backup would overwrite a file there
        open(dest, "xb").close()
    except FileExistsError:
        raise ToolError(f"{dest} already exists; give another path or try again "
                        "in a second") from None
    try:
        db.backup(con, dest)
    except BaseException:
        with contextlib.suppress(OSError):      # the backup's own error matters more
            os.remove(dest)
        raise
    return {"path": dest, "bytes": os.path.getsize(dest)}


# --- invoices: delete, issue, void ------------------------------------------------------

CONFIRM = _s("The invoice number again, exactly, to confirm.")


def _confirmed(con, a, verb):
    r = _find_invoice(con, a["invoice"])
    if str(a["confirm"]).strip() != r["number"]:
        raise ToolError(f"to {verb} {r['number']}, give confirm={r['number']!r}; "
                        "nothing was changed")
    return r


@tool("delete_invoice_draft", "Delete an invoice draft",
      "Deletes a draft invoice. Issued invoices can't be deleted; they are voided.",
      {"invoice": _s("Number or id of the draft."), "confirm": CONFIRM},
      ["invoice", "confirm"], destructive=True)
def delete_invoice_draft(con, a):
    r = _confirmed(con, a, "delete")
    invoices.delete(con, r["id"], number=r["number"])
    return {"deleted": r["number"], "id": r["id"]}


@tool("issue_invoice", "Issue an invoice",
      "Issues a draft: its PDF and figures are stored and never change afterwards (a "
      "wrong invoice is voided and a new one issued). Do it only when the user asks, "
      "after checking the draft with get_invoice or save_invoice_pdf. Refused while "
      "the invoice settings are the shipped example, or when an issued invoice "
      "already bills some of the same time.",
      {"invoice": _s("Number or id of the draft."), "confirm": CONFIRM},
      ["invoice", "confirm"], destructive=True)
def issue_invoice(con, a):
    r = _confirmed(con, a, "issue")
    return _invoice_out(invoices.issue(con, r["id"], number=r["number"]))


@tool("void_invoice", "Void an invoice",
      "Voids an issued invoice, only when the user asks. Its PDF is kept, marked void, "
      "and its time can be invoiced again. This can't be undone.",
      {"invoice": _s("Number or id of the issued invoice."), "confirm": CONFIRM,
       "reason": _s("Why; kept in the audit log.")},
      ["invoice", "confirm"], destructive=True)
def void_invoice(con, a):
    r = _confirmed(con, a, "void")
    return _invoice_out(invoices.void(con, r["id"], str(a.get("reason") or ""),
                                      number=r["number"]))


# --- invoice settings -------------------------------------------------------------------

PROFILE_KEYS = ("issuer", "client", "agreement", "terms", "intro", "rate", "max_hours",
                "remittance", "retainer_label", "sections", "credits", "credit_note")
MERGED = ("issuer", "client", "credit_note")       # only the keys given change
OBJECT = {"type": "object"}
PROFILE = {
    "issuer": {**OBJECT, "description": "You, as printed: name, phone, email."},
    "client": {**OBJECT, "description": "Who is billed: name (one of your clients; "
               "only its time is billed), address (a list of up to 6 lines), attn, "
               "email."},
    "agreement": _s("Agreement line."),
    "terms": _s("Payment terms line."),
    "intro": _s("Introduction under the header (up to 3 printed lines)."),
    "rate": _n("Hourly rate printed and billed.", minimum=0),
    "max_hours": _n("Monthly maximum hours, printed with the maximum and additional "
                    "amount authorized rows; 0 leaves those rows off.", minimum=0),
    "remittance": _a("Remittance lines under the billing summary (up to 6).",
                     {"type": "string"}),
    "retainer_label": _s("Retainer line; {date} becomes the date, e.g. 30-Sep-26."),
    "sections": _a("All sections, replacing them: heading, key, optional intro, and "
                   "either subtotal and pairs ([project, task]) or groups of label, "
                   "subtotal and pairs. Read the current ones with get_invoice_settings "
                   "first.", OBJECT),
    "credits": _a("All credits, replacing them: label (may use {pct}), pct, and scope "
                  "{project: name} or {section: key}.", OBJECT),
    "credit_note": {**OBJECT, "description": "The note about credits at the foot: head "
                    "and text."},
}


def _profile_body(a, cur=None):
    """The current profile with the given fields changed: issuer, client and
    credit_note key by key, the rest whole."""
    cur = invoices.load_profile() if cur is None else cur
    b = {k: cur[k] for k in PROFILE_KEYS if k in cur}
    for k in PROFILE_KEYS:
        if a.get(k) is not None:
            b[k] = {**cur.get(k, {}), **a[k]} if k in MERGED else a[k]
    return b


@tool("get_invoice_settings", "Invoice settings",
      "The invoice settings (the app's Invoice settings): you and the client as "
      "printed, agreement, terms, rate, monthly maximum, sections, credits and "
      "wording. example is true while they are still the shipped example.",
      read_only=True)
def get_invoice_settings(con, a):
    return {"settings": invoices.profile_for_editor()}


@tool("update_invoice_settings", "Change invoice settings",
      "Changes the invoice settings; only the fields given change, and in issuer, "
      "client and credit_note only the keys given. They are checked as the app checks "
      "them (wording must fit where it is printed; every billed project in a section). "
      "The old settings are backed up first. Drafts follow the new settings; issued "
      "invoices keep the ones they were issued with.",
      PROFILE, destructive=True, idempotent=True)
def update_invoice_settings(con, a):
    if all(a.get(k) is None for k in PROFILE_KEYS):
        raise ToolError("nothing to change; give at least one field")
    seen = {}

    def patch(cur):     # applied to the settings as saved, inside save_profile's lock
        seen["before"] = cur
        return _profile_body(a, cur)
    after = invoices.save_profile(con, patch)
    return {"settings": after,
            "changed": [k for k in PROFILE_KEYS
                        if a.get(k) is not None and seen["before"].get(k) != after.get(k)]}


@tool("preview_invoice_settings", "Preview invoice settings",
      "Writes a DRAFT PDF of the newest invoice with changed invoice settings, without "
      "saving them, so the result can be checked first. Takes the fields "
      "update_invoice_settings takes, and where to write the PDF.",
      {**PROFILE, "path": PATH})
def preview_invoice_settings(con, a):
    data = invoices.profile_preview(con, _profile_body(a))
    name = f"Invoice settings preview {datetime.now():%Y%m%d-%H%M%S}.pdf"
    return {**_write(_out_path(con, a.get("path"), name), data), "saved": False}


# --- argument checking -------------------------------------------------------------

ID_ARGS = {"project", "task", "client", "tag", "tags", "invoice"}     # a name, or an id
NUMBER_ARGS = {"duration"}      # text, or a number of hours
MAX_INT = 2 ** 63               # SQLite integers are 64-bit


def _finite(v):
    return -MAX_INT < v < MAX_INT if isinstance(v, int) else math.isfinite(v)


def _not_finite(key, v):
    return ToolError(f"{key} must be a finite number, not {v!r}" if isinstance(v, float)
                     else f"{key} is too large")


def _what(v):
    return ("true or false" if isinstance(v, bool) else "a number"
            if isinstance(v, (int, float)) else "a list" if isinstance(v, list)
            else "an object" if isinstance(v, dict) else type(v).__name__)


def _check(key, spec, v, numeric=None):
    """One argument against its schema -> the value, converted where that is
    unambiguous ("2" for an integer, "true" for a boolean); else ToolError.
    Text arguments take strings only, except names-or-ids, which also take a
    whole number (an id), and a duration, which also takes a number of hours.
    Infinite and NaN numbers are refused everywhere."""
    if numeric is None:
        numeric = "id" if key in ID_ARGS else "number" if key in NUMBER_ARGS else None
    t = spec.get("type")
    if isinstance(t, list):         # ["number", "null"]: null is handled just below
        t = next(x for x in t if x != "null")
    if v is None:
        return None
    if t == "string":
        if numeric and isinstance(v, (int, float)) and not isinstance(v, bool):
            if not _finite(v):
                raise _not_finite(key, v)
            if numeric == "id":
                if not float(v).is_integer() or not -MAX_INT < int(v) < MAX_INT:
                    raise ToolError(f"{key} must be a name or a whole-number id, not {v!r}")
                v = int(v)
        elif not isinstance(v, str):
            raise ToolError(f"{key} must be a string (in quotes), not {_what(v)}")
    elif t == "integer":
        if isinstance(v, str) and re.fullmatch(r"\s*-?\d{1,19}\s*", v):
            v = int(v)
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ToolError(f"{key} must be a whole number")
        if not _finite(v):
            raise ToolError(f"{key} is too large")
    elif t == "number":
        if isinstance(v, str):
            try:
                v = float(v)        # 'Infinity' and 'nan' convert too; refused below
            except ValueError:
                pass
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ToolError(f"{key} must be a number")
        if not _finite(v):
            raise _not_finite(key, v)
    elif t == "boolean":
        if isinstance(v, str) and v.strip().lower() in ("true", "false"):
            v = v.strip().lower() == "true"
        if not isinstance(v, bool):
            raise ToolError(f"{key} must be true or false")
    elif t == "object":
        if not isinstance(v, dict):
            raise ToolError(f"{key} must be an object ({{...}}), not {_what(v)}")
    elif t == "array":
        if not isinstance(v, list):
            v = [v]
        v = [_check(f"{key} item", spec["items"], x, numeric) for x in v]
        if any(x is None for x in v):
            raise ToolError(f"{key} can't contain null")
    if "enum" in spec and v not in spec["enum"]:
        raise ToolError(f"{key} must be one of: {', '.join(spec['enum'])}")
    if "minimum" in spec and v < spec["minimum"]:
        raise ToolError(f"{key} must be at least {spec['minimum']}")
    if "maximum" in spec and v > spec["maximum"]:
        raise ToolError(f"{key} must be at most {spec['maximum']}")
    return v


def _validate(schema, args):
    props = schema["properties"]
    extra = sorted(set(args) - set(props))
    if extra:
        raise ToolError(f"unknown argument {', '.join(extra)}; this tool takes "
                        + (", ".join(props) if props else "no arguments"))
    missing = [k for k in schema.get("required", ()) if args.get(k) is None]
    if missing:
        raise ToolError(f"missing required argument {', '.join(missing)}")
    return {k: _check(k, props[k], v) for k, v in args.items()}


# --- JSON-RPC ----------------------------------------------------------------------

def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tool_error(message):
    return {"content": [{"type": "text", "text": message}],
            "structuredContent": {"error": message}, "isError": True}


def _no_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def _finite_float(s):
    x = float(s)
    if not math.isfinite(x):
        raise ValueError(f"{s} is too large for a number")
    return x


def _loads(line):
    """Strict JSON: NaN, Infinity and numbers beyond a float's range are refused."""
    return json.loads(line, parse_constant=_no_constant, parse_float=_finite_float)


class Server:
    """Handles one decoded JSON-RPC message at a time; serve() does the I/O."""

    def __init__(self, con, verbose=True):
        self.con = con
        self.protocol = None
        self.log = log if verbose else (lambda msg: None)
        self.methods = {"initialize": self.initialize, "ping": lambda p: {},
                        "tools/list": self.list_tools, "tools/call": self.call_tool}

    def handle_line(self, line):
        """One line of input -> one line of output (no newline), or None."""
        line = line.strip().lstrip("﻿")
        if not line:
            return None
        try:
            msg = _loads(line)
        except (ValueError, RecursionError):
            out = _error(None, PARSE_ERROR, "Parse error: the line is not valid JSON "
                                            "(NaN, Infinity and out-of-range numbers are "
                                            "not JSON)")
        else:
            out = self.handle(msg)
        if out is None:
            return None
        # ASCII on the wire: valid UTF-8, and no U+2028 for a line splitter to trip on.
        # allow_nan=False: never a bare NaN or Infinity, which no JSON parser accepts.
        try:
            return json.dumps(out, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError) as e:
            self.log(f"a reply could not be written as JSON: {e}")
            id_ = out.get("id") if isinstance(out, dict) else None
            return json.dumps(_error(id_, INTERNAL_ERROR, "Internal error: the reply "
                                     f"can't be written as JSON: {e}"),
                              separators=(",", ":"))

    def handle(self, msg):
        if isinstance(msg, list):           # a batch: protocols 2025-03-26 and older
            if self.protocol is None or self.protocol in NO_BATCHES:
                return _error(None, INVALID_REQUEST, "Invalid Request: batches are not "
                              "supported " + (f"in protocol {self.protocol}" if self.protocol
                                              else "before initialize"))
            if not msg:
                return _error(None, INVALID_REQUEST, "Invalid Request: empty batch")
            out = [r for r in map(self.handle_one, msg) if r is not None]
            return out or None
        return self.handle_one(msg)

    def handle_one(self, msg):
        if not isinstance(msg, dict):
            return _error(None, INVALID_REQUEST, "Invalid Request: expected an object")
        method = msg.get("method")
        if "id" not in msg:
            return None         # a notification (initialized, cancelled...): no reply
        id_ = msg["id"]
        if isinstance(id_, bool) or not isinstance(id_, (str, int, float)) or (
                isinstance(id_, float) and not id_.is_integer()):
            return _error(None, INVALID_REQUEST, "Invalid Request: id must be a string "
                                                 "or a whole number")
        if not isinstance(method, str):
            if "result" in msg or "error" in msg:
                return None     # a response; this server sends no requests
            return _error(id_, INVALID_REQUEST, "Invalid Request: method is missing")
        if msg.get("jsonrpc") != "2.0":
            return _error(id_, INVALID_REQUEST, 'Invalid Request: jsonrpc must be "2.0"')
        fn = self.methods.get(method)
        if fn is None:
            return _error(id_, METHOD_NOT_FOUND, f"Method not found: {method}")
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(id_, INVALID_PARAMS, "Invalid params: expected an object")
        try:
            return {"jsonrpc": "2.0", "id": id_, "result": fn(params)}
        except RpcError as e:
            return _error(id_, e.code, e.message)
        except Exception as e:      # keep serving; show the cause
            traceback.print_exc(file=sys.stderr)
            return _error(id_, INTERNAL_ERROR, f"Internal error: {type(e).__name__}: {e}")

    def initialize(self, p):
        want = p.get("protocolVersion")
        self.protocol = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        client = p.get("clientInfo") if isinstance(p.get("clientInfo"), dict) else {}
        self.log(f"client {client.get('name', '?')} {client.get('version', '')}, "
            f"protocol {self.protocol}")
        return {"protocolVersion": self.protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "dutemps", "version": VERSION},
                "instructions": INSTRUCTIONS}

    def list_tools(self, p):
        return {"tools": [t["spec"] for t in TOOLS.values()]}

    def call_tool(self, p):
        name, args = p.get("name"), p.get("arguments")
        if not isinstance(name, str) or name not in TOOLS:
            raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise RpcError(INVALID_PARAMS, "Invalid params: arguments must be an object")
        t = TOOLS[name]
        try:
            out = t["fn"](self.con, _validate(t["spec"]["inputSchema"], args))
        except (ToolError, core.Invalid) as e:
            return _tool_error(str(e))
        except sqlite3.IntegrityError as e:     # a CHECK or trigger said no
            return _tool_error(str(e))
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e):
                traceback.print_exc(file=sys.stderr)
                return _tool_error(f"database error in {name}: {e}")
            return _tool_error("the database is busy (the DuTemps app may be saving); "
                               "try again in a moment")
        except ZoneInfoNotFoundError as e:
            return _tool_error(str(e.args[0] if e.args else e))
        except Exception as e:      # a bug: keep serving, and show where on stderr
            traceback.print_exc(file=sys.stderr)
            return _tool_error(f"internal error in {name}: {type(e).__name__}: {e}")
        read_only = t["spec"]["annotations"]["readOnlyHint"]
        if not read_only:
            self.log(f"{name} done")
        try:
            text = json.dumps(out, ensure_ascii=False, default=str, allow_nan=False)
        except ValueError:          # an infinite or NaN number read from the database
            return _tool_error(("" if read_only else f"{name} was carried out, but ")
                               + "the result holds a number that is not finite "
                               "(infinite or NaN), which JSON can't carry. A value in "
                               "the database needs fixing in the DuTemps app.")
        return {"content": [{"type": "text", "text": text}],
                "structuredContent": json.loads(text), "isError": False}


def serve(con, stdin=None, stdout=None):
    """Read requests from stdin until it closes, one per line; answer on stdout."""
    inp = stdin or sys.stdin.buffer
    out = stdout or sys.stdout.buffer
    real_stdout, sys.stdout = sys.stdout, sys.stderr    # a stray print() can't corrupt it
    srv = Server(con)
    log(f"DuTemps MCP server {VERSION} on "
        f"{con.execute('PRAGMA database_list').fetchone()['file'] or ':memory:'}")
    try:
        while True:
            raw = inp.readline()
            if not raw:
                break
            try:
                reply = srv.handle_line(raw.decode("utf-8"))
            except UnicodeDecodeError:
                reply = json.dumps(_error(None, PARSE_ERROR, "Parse error: not UTF-8"))
            if reply is not None:
                out.write(reply.encode("utf-8") + b"\n")
                out.flush()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        sys.stdout = real_stdout
