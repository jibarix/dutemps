"""Summary, detailed, weekly and invoice-line reports over filtered entries.
All grouping by date uses the local timezone setting."""
import collections
from datetime import date, timedelta

from . import core, db
from .timeutil import local, week_start

GROUPS = ("client", "project", "task", "tag", "description", "day", "week",
          "month", "billable")
STEP = 0.25     # the billing unit: a quarter hour


def billed_hours(raw_hours):
    """Nearest quarter hour, with a quarter-hour minimum."""
    return max(STEP, round(raw_hours / STEP) * STEP) if raw_hours > 0 else 0.0


def _keys(e, by, tz, ws):
    d = local(e["start_utc"], tz).date()
    if by == "client":
        return [e["client"] or "(no client)"]
    if by == "project":
        return [e["project"] or "(no project)"]
    if by == "task":
        return [e["task"] or "(no task)"]
    if by == "tag":
        return [t["name"] for t in e["tags"]] or ["(no tag)"]
    if by == "description":
        return [e["description"] or "(no description)"]
    if by == "day":
        return [d.isoformat()]
    if by == "week":
        return [week_start(d, ws).isoformat()]
    if by == "month":
        return [d.strftime("%Y-%m")]
    if by == "billable":
        return ["Billable" if e["billable"] else "Non-billable"]
    raise core.Invalid(f"unknown grouping {by}")


def _acc():
    return {"seconds": 0, "billable_seconds": 0, "amount": 0.0, "entries": 0}


def _add(a, e):
    a["seconds"] += e["seconds"]
    a["entries"] += 1
    if e["billable"]:
        a["billable_seconds"] += e["seconds"]
        a["amount"] += e["amount"]


def _finish(a):
    a["hours"] = round(a["seconds"] / 3600, 4)
    a["billable_hours"] = round(a["billable_seconds"] / 3600, 4)
    a["amount"] = round(a["amount"], 2)
    return a


def summary(con, f, group1="project", group2=None):
    """Two-level summary. Entries with several tags count once per tag when
    grouped by tag, so tag totals can exceed the total."""
    s = db.settings(con)
    tz, ws = s["timezone"], int(s["week_start"])
    for g in (group1, group2):
        if g and g not in GROUPS:
            raise core.Invalid(f"unknown grouping {g}")
    entries = core.list_entries(con, {**f, "finished": True}, order="ASC")
    total = _acc()
    groups = collections.OrderedDict()
    colors = {}
    for e in entries:
        _add(total, e)
        for k1 in _keys(e, group1, tz, ws):
            g = groups.setdefault(k1, {"name": k1, **_acc(), "children": {}})
            _add(g, e)
            if group1 == "project" and e["project_color"]:
                colors[k1] = e["project_color"]
            if group2:
                for k2 in _keys(e, group2, tz, ws):
                    c = g["children"].setdefault(k2, {"name": k2, **_acc()})
                    _add(c, e)
    rows = []
    for g in groups.values():
        g["children"] = sorted((_finish(c) for c in g["children"].values()),
                               key=lambda c: -c["seconds"])
        g["color"] = colors.get(g["name"])
        rows.append(_finish(g))
    if group1 in ("day", "week", "month"):
        rows.sort(key=lambda r: r["name"])
    else:
        rows.sort(key=lambda r: -r["seconds"])
    return {"total": _finish(total), "groups": rows, "group1": group1, "group2": group2}


def series(con, f, d_from, d_to, by="day"):
    """Hours per day (or week) per project, for the dashboard bar chart."""
    s = db.settings(con)
    tz, ws = s["timezone"], int(s["week_start"])
    start, end = date.fromisoformat(d_from), date.fromisoformat(d_to)
    buckets = []
    d = start if by == "day" else week_start(start, ws)
    while d <= end:
        buckets.append(d.isoformat())
        d += timedelta(days=1 if by == "day" else 7)
    data = {b: collections.defaultdict(int) for b in buckets}
    proj = {}
    for e in core.list_entries(con, {**f, "from": d_from, "to": d_to}, order="ASC"):
        ld = local(e["start_utc"], tz).date()
        b = (ld if by == "day" else week_start(ld, ws)).isoformat()
        if b in data:
            name = e["project"] or "(no project)"
            data[b][name] += e["seconds"]
            proj.setdefault(name, e["project_color"] or "#9E9E9E")
    return {"buckets": buckets, "projects": proj,
            "values": {b: dict(v) for b, v in data.items()}}


def page_totals(con, f, entries):
    """Day and week totals for every full week a page of entries touches, so
    the tracker's headers don't depend on how many entries are loaded."""
    s = db.settings(con)
    tz, ws = s["timezone"], int(s["week_start"])
    ds = [local(e["start_utc"], tz).date() for e in entries if e["end_utc"]]
    if not ds:
        return {"days": {}, "weeks": {}}
    a, b = week_start(min(ds), ws), week_start(max(ds), ws) + timedelta(days=6)
    days, weeks = collections.defaultdict(int), collections.defaultdict(int)
    for e in core.list_entries(con, {**f, "from": a.isoformat(), "to": b.isoformat(),
                                     "finished": True}):
        ld = local(e["start_utc"], tz).date()
        days[ld.isoformat()] += e["seconds"]
        weeks[week_start(ld, ws).isoformat()] += e["seconds"]
    return {"days": dict(days), "weeks": dict(weeks)}


def weekly(con, f, monday):
    """Project/task x 7 days grid, as in the weekly report and the
    timesheet. `monday` is the local first day of the week."""
    s = db.settings(con)
    tz = s["timezone"]
    d0 = date.fromisoformat(monday)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range(7)]
    rows = collections.OrderedDict()
    totals = [0] * 7
    for e in core.list_entries(con, {**f, "from": days[0], "to": days[-1]}, order="ASC"):
        i = days.index(local(e["start_utc"], tz).date().isoformat())
        k = (e["project_id"], e["task_id"])
        r = rows.setdefault(k, {"project_id": e["project_id"], "task_id": e["task_id"],
                                "project": e["project"] or "(no project)",
                                "task": e["task"], "client": e["client"],
                                "color": e["project_color"] or "#9E9E9E",
                                "days": [0] * 7, "total": 0})
        r["days"][i] += e["seconds"]
        r["total"] += e["seconds"]
        totals[i] += e["seconds"]
    out = sorted(rows.values(), key=lambda r: (r["project"].lower(), (r["task"] or "").lower()))
    return {"days": days, "rows": out, "totals": totals, "total": sum(totals)}


def invoice_lines(con, f):
    """One line per local date x client x project x task, raw hours rounded
    with the quarter-hour rule."""
    s = db.settings(con)
    tz = s["timezone"]
    lines = collections.OrderedDict()
    for e in core.list_entries(con, {**f, "finished": True, "billable": "true"},
                               order="ASC"):
        d = local(e["start_utc"], tz).date().isoformat()
        k = (d, e["client_id"] or 0, e["project"] or "", e["task"] or "")
        ln = lines.setdefault(k, {"date": d, "project": k[2], "task": k[3],
                                  "client": e["client"] or "",
                                  "client_id": e["client_id"], "descriptions": [],
                                  "seconds": 0, "rate": e["rate"], "entries": 0,
                                  "first_utc": e["start_utc"], "last_utc": e["end_utc"]})
        ln["seconds"] += e["seconds"]
        ln["entries"] += 1
        ln["last_utc"] = max(ln["last_utc"], e["end_utc"])
        if e["description"] and e["description"] not in ln["descriptions"]:
            ln["descriptions"].append(e["description"])
        if e["rate"] != ln["rate"]:
            ln["mixed_rate"] = True
    out = []
    for k in sorted(lines):
        ln = lines[k]
        ln["raw_hours"] = round(ln["seconds"] / 3600, 4)
        ln["hours"] = billed_hours(ln["seconds"] / 3600)
        ln["amount"] = round(ln["hours"] * (ln["rate"] or 0), 2)
        ln["description"] = " / ".join(ln.pop("descriptions"))
        out.append(ln)
    return {"lines": out, "hours": sum(l["hours"] for l in out),
            "raw_hours": round(sum(l["seconds"] for l in out) / 3600, 4),
            "amount": round(sum(l["amount"] for l in out), 2)}


def dashboard(con, today_local):
    s = db.settings(con)
    ws = int(s["week_start"])
    t = date.fromisoformat(today_local)
    w0 = week_start(t, ws)
    m0 = t.replace(day=1)
    week = summary(con, {"from": w0.isoformat(), "to": (w0 + timedelta(days=6)).isoformat()})
    month = summary(con, {"from": m0.isoformat(), "to": t.isoformat()})
    today = summary(con, {"from": t.isoformat(), "to": t.isoformat()})
    return {
        "today": today["total"], "week": week["total"], "month": month["total"],
        "week_projects": week["groups"], "month_projects": month["groups"],
        "week_series": series(con, {}, w0.isoformat(), (w0 + timedelta(days=6)).isoformat()),
        "last30": series(con, {}, (t - timedelta(days=29)).isoformat(), t.isoformat()),
        "recent": core.list_entries(con, {}, limit=8),
        "week_start": w0.isoformat(), "month_start": m0.isoformat(),
    }
