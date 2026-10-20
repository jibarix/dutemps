"""Invoices: lines -> sections, credits and net (build), then a PDF (render).

build() returns plain data, so an issued invoice can be stored as a snapshot
and re-rendered later without consulting the time entries again:

  subtotal      = hours x rate
  credit        = -(pct x rate x hours in scope), rounded to the cent, sized
                  from the lines rather than typed, so a corrected line carries
                  into its credit (scope: a project, or a section's subtotal)
  net           = subtotal + credits + retainer, never below zero

The monthly maximum and the "additional amount authorized" rows are
informational: the invoice total is the subtotal. A maximum of 0 leaves
them off.
"""
import json
import os
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from . import core, db, pdf, reports

# Beside the database; python -m dutemps points it at the --db folder.
PROFILE_PATH = os.path.join(db.HERE, "data", "invoice_profile.json")
# Used until the first save on Invoice settings writes PROFILE_PATH.
EXAMPLE_PROFILE = os.path.join(db.HERE, "examples", "invoice_profile.json")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def profile_path(con):
    return os.path.join(db.db_dir(con), "invoice_profile.json")


def load_profile(path=None):
    path = path or PROFILE_PATH
    if not os.path.exists(path) and path == PROFILE_PATH:
        path = EXAMPLE_PROFILE
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def profile_for_editor():
    """The profile, flagged when it is still the shipped example."""
    p = load_profile()
    if not os.path.exists(PROFILE_PATH):
        p["example"] = True
    return p


def cents(x):
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _client_key(name):
    return (name or "").strip().casefold()


def _clients_ever_named(con, name):
    """Ids of the clients that have this name or had it before a rename."""
    key = _client_key(name)
    ids = {r[0] for r in con.execute("SELECT id, name FROM clients")
           if _client_key(r[1]) == key}
    for r in con.execute("SELECT entity_id, before, after FROM audit "
                         "WHERE entity = 'clients'"):
        for side in (r[1], r[2]):
            if side and _client_key(json.loads(side).get("name")) == key:
                ids.add(r[0])
    return ids


def with_client_id(con, profile):
    """The profile with its client's id, looked up by name when it has none
    yet (profiles saved before ids were kept, renamed clients included).
    Invoices compare ids, so renaming a client changes nothing. A name that
    fits no client, or several, stays without an id."""
    c = profile.get("client") or {}
    if "id" in c:
        return profile
    cid = None
    if _client_key(c.get("name")):
        hit = _clients_ever_named(con, c["name"])
        if len(hit) != 1:
            return profile
        [cid] = hit
    return {**profile, "client": {**c, "id": cid}}


def _client_ident(profile):
    c = profile.get("client") or {}
    return ("id", c["id"]) if "id" in c else ("name", _client_key(c.get("name")))


def for_client(lines, profile):
    """The lines of the profile's client (time with no client when it names
    none), and the hours of the rest by client, for their own invoices."""
    kind, mine = _client_ident(profile)
    kept, left = [], {}
    for l in lines:
        if (l["client_id"] if kind == "id" else _client_key(l["client"])) == mine:
            kept.append(l)
        else:
            k = l["client"] or "(no client)"
            left[k] = left.get(k, 0) + l["hours"]
    return kept, left


def build(con, profile, number, issued, d_from, d_to, retainer=0.0,
          retainer_label="", credit_pcts=None):
    """credit_pcts overrides the profile's percentages, in order."""
    rate = float(profile["rate"])
    profile = with_client_id(con, profile)
    got = reports.invoice_lines(con, {"from": d_from, "to": d_to})["lines"]
    got, left_out = for_client(got, profile)
    if not got:
        who = (profile.get("client") or {}).get("name") or "time with no client"
        raise core.Invalid(f"no billable time between {d_from} and {d_to} for {who}" + (
            "; the time there is filed under " + ", ".join(
                f"{k} ({v:g} h)" for k, v in left_out.items()) if left_out else ""))
    mixed = [l for l in got if l.get("mixed_rate")]
    if mixed:
        raise core.Invalid("entries at different rates on one line: " + ", ".join(
            f"{l['date']} {l['project']} / {l['task'] or '(no task)'}" for l in mixed))
    odd = sorted({l["rate"] for l in got if l["rate"] != rate})
    if odd:
        raise core.Invalid(f"lines billed at {odd}, not the invoice rate {rate}")

    where = {}                    # (project, task) -> (section index, group index)
    sections = []
    for si, s in enumerate(profile["sections"]):
        groups = s.get("groups") or [{"label": "", "subtotal": s["subtotal"],
                                      "pairs": s["pairs"]}]
        sections.append({"heading": s["heading"], "key": s.get("key", ""),
                         "intro": s.get("intro", ""), "groups": [
                             {"label": g["label"], "subtotal": g["subtotal"],
                              "lines": [], "hours": 0.0} for g in groups]})
        for gi, g in enumerate(groups):
            for p, t in g["pairs"]:
                where[(p, t)] = (si, gi)
    stray = sorted({(l["project"], l["task"]) for l in got} - set(where))
    if stray:
        raise core.Invalid("no invoice section for: " + ", ".join(
            f"{p} / {t or '(no task)'}" for p, t in stray))

    lines = []
    for l in got:
        ln = {"date": l["date"], "project": l["project"], "task": l["task"],
              "description": l["description"], "hours": l["hours"]}
        si, gi = where[(l["project"], l["task"])]
        sections[si]["groups"][gi]["lines"].append(ln)
        sections[si]["groups"][gi]["hours"] += l["hours"]
        lines.append(ln)
    # A section with nothing this period is left out, not printed empty.
    for s in sections:
        s["groups"] = [g for g in s["groups"] if g["lines"]]
        s["hours"] = sum(g["hours"] for g in s["groups"])
    sections = [s for s in sections if s["groups"]]

    hours = sum(l["hours"] for l in lines)
    subtotal = cents(hours * rate)
    max_amount = cents(profile["max_hours"] * rate)
    additional = cents(subtotal - max_amount) if hours >= profile["max_hours"] else 0.0
    credits = []
    pcts = credit_pcts or [c["pct"] for c in profile["credits"]]
    if len(pcts) != len(profile["credits"]):
        raise core.Invalid(f"{len(profile['credits'])} credit percentages needed")
    for c, pct in zip(profile["credits"], pcts):
        if not pct:               # 0% this month: no line, rather than a $0.00 one
            continue
        sc = c["scope"]
        if "project" in sc:
            h = sum(l["hours"] for l in lines if l["project"] == sc["project"])
        else:
            h = sum(s["hours"] for s in sections if s["key"] == sc["section"])
        if not h:
            raise core.Invalid(f"no hours in scope for credit {c['label']!r}; "
                               "drop it or change its scope before issuing")
        credits.append({"label": c["label"].format(pct=_pct(pct)), "pct": pct,
                        "hours": h, "amount": -cents(pct / 100 * rate * h)})
    retainer = -abs(float(retainer or 0))
    net = cents(subtotal + sum(c["amount"] for c in credits) + retainer)

    by_project = {}
    by_day = {}
    for l in lines:
        by_project[l["project"]] = by_project.get(l["project"], 0) + l["hours"]
        day = by_day.setdefault(l["date"], {})
        day[l["project"]] = day.get(l["project"], 0) + l["hours"]
    return {
        "number": number, "issued": issued, "from": d_from, "to": d_to,
        "rate": rate, "max_hours": profile["max_hours"],
        "sections": sections, "hours": hours, "subtotal": subtotal,
        "max_amount": max_amount, "additional": additional,
        "total_max": cents(max_amount + additional), "total": subtotal,
        "credits": credits,
        "retainer": {"label": retainer_label, "amount": retainer} if retainer else None,
        "net": max(0.0, net),
        "by_project": sorted(by_project.items(), key=lambda kv: (-kv[1], kv[0])),
        "by_day": by_day,
        "left_out": sorted(left_out.items()),
    }


def _pct(p):
    return f"{p:g}"


# ---- rendering ---------------------------------------------------------------

INK, MUTED, FAINT = "#1F2933", "#6B7280", "#9CA3AF"
RULE, SOFT, ZEBRA = "#E5E7EB", "#F3F4F6", "#F9FAFB"
ACCENT, ACCENT_SOFT, WHITE = "#1E3A8A", "#E8EEF9", "#FFFFFF"
# Assigned by hours, largest first. Project colours are picked for a timer
# list, not paper, so charts use their own palette.
PALETTE = ("#1E3A8A", "#0D9488", "#F59E0B", "#8B5CF6", "#64748B", "#E11D48",
           "#0EA5E9", "#84CC16")

M = 40.0                          # page margin
W, H = pdf.LETTER
R = W - M                         # right edge of the text block
BOTTOM = H - 46                   # last usable baseline above the footer


def money(x):
    return ("-$" if x < 0 else "$") + f"{abs(x):,.2f}"


def hrs(x):
    return f"{x:,.2f}"


def nice_date(iso, year=True):
    d = date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}" if year else f"{MONTHS[d.month - 1]} {d.day}"


def period(a, b):
    da, db_ = date.fromisoformat(a), date.fromisoformat(b)
    if da.year == db_.year:
        return f"{nice_date(a, False)} – {nice_date(b)}"
    return f"{nice_date(a)} – {nice_date(b)}"


def render(inv, profile, draft=False):
    doc = pdf.Document(title=f"Invoice {inv['number']}",
                       author=profile["issuer"]["name"],
                       date=inv["issued"].replace("-", ""))
    colors = {p: PALETTE[i % len(PALETTE)] for i, (p, _) in enumerate(inv["by_project"])}
    p = doc.page()
    y = _summary(p, inv, profile, colors)
    note_on_summary = _credit_note(None, y, inv, profile) <= BOTTOM
    if note_on_summary:
        _credit_note(p, y, inv, profile)
    _detail(doc, inv, profile, note_on_summary)
    n = len(doc.pages)
    for i, pg in enumerate(doc.pages, 1):
        pg.line(M, H - 40, R, H - 40, RULE, 0.5)
        pg.text(M, H - 28, f"{profile['issuer']['name']}  ·  Invoice {inv['number']}",
                "R", 7, FAINT)
        pg.text(R, H - 28, f"Page {i} of {n}", "R", 7, FAINT, "right")
        if draft:
            pg.stamp("DRAFT")
    return doc.render()


def _label(p, x, y, s):
    p.text(x, y, s.upper(), "B", 6.5, MUTED)


def _summary(p, inv, prof, colors):
    iss, cli = prof["issuer"], prof["client"]
    p.rect(0, 0, W, 6, fill=ACCENT)
    p.text(M, 58, iss["name"].upper(), "B", 19, INK)
    p.text(M, 72, f"{iss['phone']}   ·   {iss['email']}", "R", 8, MUTED)
    p.text(R, 58, "INVOICE", "B", 19, ACCENT, "right")
    p.text(R, 72, inv["number"], "R", 9, MUTED, "right")
    p.line(M, 86, R, 86, RULE, 0.75)

    y0 = 104
    _label(p, M, y0, "Bill to")
    y = y0 + 13
    for i, s in enumerate(cli["address"]):
        p.text(M, y, s, "B" if i == 0 else "R", 8.5, INK)
        y += 11
    p.text(M, y, cli["email"], "R", 8.5, MUTED)
    x2, x3 = 250, 420
    _label(p, x2, y0, "Agreement")
    p.text(x2, y0 + 13, prof["agreement"], "R", 8.5, INK)
    _label(p, x2, y0 + 34, "Service period")
    p.text(x2, y0 + 47, period(inv["from"], inv["to"]), "R", 8.5, INK)
    _label(p, x2, y0 + 68, "Attention")
    p.text(x2, y0 + 81, cli["attn"], "R", 8.5, INK)
    _label(p, x3, y0, "Invoice no.")
    p.text(x3, y0 + 13, inv["number"], "B", 8.5, INK)
    _label(p, x3, y0 + 34, "Invoice date")
    p.text(x3, y0 + 47, nice_date(inv["issued"]), "R", 8.5, INK)
    _label(p, x3, y0 + 68, "Terms")
    p.text(x3, y0 + 81, prof["terms"], "R", 8.5, INK)

    y = p.paragraph(M, 206, R - M, prof["intro"], "I", 8, color=MUTED) + 2

    # -- headline figures --
    deductions = sum(c["amount"] for c in inv["credits"]) + (
        inv["retainer"]["amount"] if inv["retainer"] else 0)
    cards = [("Hours billed", hrs(inv["hours"]), f"at {money(inv['rate'])} per hour"),
             ("Services", money(inv["total"]), "before credits"),
             ("Credits and retainer", money(deductions),
              f"{len(inv['credits'])} credit{'s' * (len(inv['credits']) != 1)}"
              + (" and retainer" if inv["retainer"] else "")),
             ("Net payable", money(inv["net"]), prof["terms"])]
    gap, top, ch = 10, y, 52
    cw = (R - M - gap * 3) / 4
    for i, (lab, val, sub) in enumerate(cards):
        x = M + i * (cw + gap)
        last = i == 3
        p.rect(x, top, cw, ch, fill=ACCENT if last else SOFT, r=5)
        p.text(x + 10, top + 15, lab.upper(), "B", 6.5, "#C7D2FE" if last else MUTED)
        p.text(x + 10, top + 34, val, "B", 15, WHITE if last else INK)
        p.text(x + 10, top + 45, sub, "R", 6.5, "#C7D2FE" if last else MUTED)

    y = _charts(p, top + ch + 24, inv, colors)
    return _billing(p, y + 22, inv)


def _charts(p, top, inv, colors):
    # -- hours by day, stacked by project --
    x0, x1 = M + 18, 372.0
    p.text(M, top, "Hours by day", "B", 9, INK)
    ct, cb = top + 14, top + 124           # chart top / baseline
    d0, d1 = date.fromisoformat(inv["from"]), date.fromisoformat(inv["to"])
    days = [d0 + timedelta(n) for n in range((d1 - d0).days + 1)]
    peak = max(sum(v.values()) for v in inv["by_day"].values())
    step = 2 if peak <= 12 else 4
    ceil_ = max(step, -(-peak // step) * step)
    scale = (cb - ct) / ceil_
    v = 0
    while v <= ceil_:
        yy = cb - v * scale
        p.line(x0, yy, x1, yy, RULE if v else FAINT, 0.4)
        p.text(x0 - 4, yy + 2.2, f"{v:g}", "R", 6, FAINT, "right")
        v += step
    slot = (x1 - x0) / len(days)
    bw = max(2.0, slot * 0.62)
    order = [pr for pr, _ in inv["by_project"]]
    for i, d in enumerate(days):
        cx = x0 + slot * i + slot / 2
        if d.weekday() >= 5:
            p.rect(x0 + slot * i, ct, slot, cb - ct, fill=ZEBRA)
        acc = 0.0
        for pr in reversed(order):         # largest project at the bottom
            h = inv["by_day"].get(d.isoformat(), {}).get(pr, 0)
            if h:
                p.rect(cx - bw / 2, cb - (acc + h) * scale, bw, h * scale, fill=colors[pr])
                acc += h
        if len(days) <= 16 or d.day % 2 == 1 or i == len(days) - 1:
            p.text(cx, cb + 9, str(d.day), "R", 5.5, MUTED, "center")
    # legend
    lx, ly = x0, cb + 22
    for pr in order:
        wdt = pdf.width(pr, "R", 7) + 20
        if lx + wdt > x1:
            lx, ly = x0, ly + 11
        p.rect(lx, ly - 6, 6, 6, fill=colors[pr], r=1)
        p.text(lx + 9, ly, pr, "R", 7, INK)
        lx += wdt
    chart_end = ly + 4

    # -- hours by project --
    bx, bxr = 396.0, R
    p.text(bx, top, "Hours by project", "B", 9, INK)
    y = top + 24
    total = inv["hours"]
    for pr, h in inv["by_project"]:
        hs = f"{hrs(h)} h"
        p.text(bx, y, pr, "R", 7.5, INK)
        p.text(bxr, y, hs, "B", 7.5, INK, "right")
        p.text(bxr - pdf.width(hs, "B", 7.5) - 6, y, f"{h / total:.0%}", "R", 7, MUTED, "right")
        p.rect(bx, y + 5, bxr - bx, 5, fill=SOFT, r=2.5)
        p.rect(bx, y + 5, max(5, (bxr - bx) * h / total), 5, fill=colors[pr], r=2.5)
        y += 23
    return max(chart_end, y - 8)


def _billing(p, top, inv):
    p.text(M, top, "Billing summary", "B", 9, INK)
    rows = [("Total hours", hrs(inv["hours"]), "R", INK),
            ("Rate per hour", money(inv["rate"]), "R", INK),
            ("Subtotal", money(inv["subtotal"]), "R", INK)]
    if inv["max_hours"]:
        rows += [(f"Maximum billable amount per month ({inv['max_hours']:g} h)",
                  money(inv["max_amount"]), "R", MUTED),
                 ("Additional amount authorized for current month",
                  money(inv["additional"]), "R", MUTED),
                 ("Total maximum billable amount for current month",
                  money(inv["total_max"]), "R", MUTED)]
    rows.append(("Total invoice", money(inv["total"]), "B", INK))
    rows += [(c["label"], money(c["amount"]), "R", INK) for c in inv["credits"]]
    if inv["retainer"]:
        rows.append((inv["retainer"]["label"], money(inv["retainer"]["amount"]), "R", INK))
    y = top + 8
    for lab, val, f, col in rows:
        p.line(M, y, R, y, RULE, 0.4)
        p.text(M + 4, y + 10.5, lab, f, 8, col)
        p.text(R - 4, y + 10.5, val, f, 8, col, "right")
        y += 14.5
    p.rect(M, y + 2, R - M, 20, fill=ACCENT, r=3)
    p.text(M + 8, y + 15.5, "Net invoice payable", "B", 9, WHITE)
    p.text(R - 8, y + 15.5, money(inv["net"]), "B", 10, WHITE, "right")
    return y + 22


def _credit_note(p, y, inv, prof):
    """Draws the remittance lines and the credit definition below y; with
    p=None it only measures, returning where it would end."""
    y += 18
    for s in prof["remittance"]:
        if p:
            p.text(M, y, s, "R", 7.5, MUTED)
        y += 10
    if not inv["credits"]:
        return y
    y += 8
    n = prof["credit_note"]
    if p:
        p.text(M, y, n["head"], "B", 7.5, INK)
    y += 10
    for ln in pdf.wrap(n["text"], R - M, "I", 7):
        if p:
            p.text(M, y, ln, "I", 7, MUTED)
        y += 9
    return y


# ---- appendix: detail of services -------------------------------------------

COL_DATE, COL_PROJ, COL_DESC = M + 4, M + 52, M + 140
DESC_W = R - 52 - COL_DESC


class _Flow:
    """A y cursor that starts a new page, with the table header, when the
    next block would cross the bottom margin."""

    def __init__(self, doc, title):
        self.doc, self.title = doc, title
        self.page, self.y = None, 0
        self.new(first=True)

    def new(self, first=False):
        self.page = self.doc.page()
        p = self.page
        p.rect(0, 0, W, 6, fill=ACCENT)
        p.text(M, 40, "APPENDIX A", "B", 7, ACCENT)
        p.text(M, 58, self.title + ("" if first else " (continued)"), "B", 14, INK)
        self.y = 74
        self.header()

    def header(self):
        p, y = self.page, self.y
        p.rect(M, y, R - M, 16, fill=SOFT)
        for x, s in ((COL_DATE, "DATE"), (COL_PROJ, "PROJECT"), (COL_DESC, "WORK DESCRIPTION")):
            p.text(x, y + 10.5, s, "B", 6.5, MUTED)
        p.text(R - 4, y + 10.5, "HOURS", "B", 6.5, MUTED, "right")
        self.y += 22

    def need(self, h):
        if self.y + h > BOTTOM:
            self.new()


def _detail(doc, inv, prof, note_done):
    f = _Flow(doc, "Detail of services")
    last_group = inv["sections"][-1]["groups"][-1]
    for s in inv["sections"]:
        f.need(40)
        f.y += 6
        f.page.text(M, f.y + 8, s["heading"], "B", 9.5, ACCENT)
        f.y += 18
        if s["intro"]:
            for ln in pdf.wrap(s["intro"], R - M, "R", 7.5):
                f.need(10)
                f.page.text(M, f.y + 7, ln, "R", 7.5, MUTED)
                f.y += 10
            f.y += 6
        for g in s["groups"]:
            if g["label"]:
                f.need(34)
                f.page.text(M, f.y + 8, g["label"], "B", 8, INK)
                f.y += 14
            for i, ln in enumerate(g["lines"]):
                wrapped = pdf.wrap(ln["description"] or "", DESC_W, "R", 7.5)
                h = max(1, len(wrapped)) * 9.5 + 6
                tail = 0 if i < len(g["lines"]) - 1 else 22 + (26 if g is last_group else 0)
                f.need(h + tail)
                p, y = f.page, f.y
                if i % 2:
                    p.rect(M, y, R - M, h, fill=ZEBRA)
                p.text(COL_DATE, y + 10, nice_date(ln["date"], False), "R", 7.5, INK)
                p.text(COL_PROJ, y + 10, ln["project"], "R", 7.5, INK)
                for k, w in enumerate(wrapped):
                    p.text(COL_DESC, y + 10 + k * 9.5, w, "R", 7.5, INK)
                p.text(R - 4, y + 10, hrs(ln["hours"]), "R", 7.5, INK, "right")
                f.y += h
            f.need(20)
            f.page.line(M, f.y, R, f.y, FAINT, 0.5)
            f.page.text(R - 52, f.y + 11, g["subtotal"], "B", 7.5, INK, "right")
            f.page.text(R - 4, f.y + 11, hrs(g["hours"]), "B", 7.5, INK, "right")
            f.y += 22
    f.need(26)
    f.page.rect(M, f.y, R - M, 18, fill=ACCENT, r=3)
    f.page.text(M + 8, f.y + 12, "TOTAL HOURS", "B", 8.5, WHITE)
    f.page.text(R - 4, f.y + 12, hrs(inv["hours"]), "B", 8.5, WHITE, "right")
    f.y += 18
    if not note_done:
        f.need(_credit_note(None, 0, inv, prof))
        f.y = _credit_note(f.page, f.y, inv, prof)


# ---- stored invoices: draft -> issued -> void --------------------------------
#
# A draft holds only its parameters and is rebuilt from the entries whenever it
# is shown, so fixing an entry fixes the draft. Issuing stores the built
# invoice, the profile it used and the PDF; the database refuses any later
# change to them (db.py, invoices_frozen), so what was sent is what is kept.

FIELDS = ("number", "issued", "period_from", "period_to", "retainer",
          "retainer_date", "credit_pcts", "note")


def _date(v, field, required=True):
    if v in (None, ""):
        if required:
            raise core.Invalid(f"{field} is required")
        return None
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError:
        raise core.Invalid(f"{field} must be a date (YYYY-MM-DD)") from None


def retainer_label(profile, d):
    """e.g. 'Less: Retainer Fee Paid on 30-Sep-26'."""
    d = date.fromisoformat(d)
    s = f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year % 100:02d}"
    return profile.get("retainer_label", "Less: Retainer Fee Paid on {date}").format(date=s)


def _clean(con, b, profile, own_id=None):
    v = {}
    v["number"] = str(b.get("number") or "").strip()
    if not v["number"]:
        raise core.Invalid("invoice number is required")
    if len(v["number"]) > 40:
        raise core.Invalid("invoice number is too long")
    v["issued"] = _date(b.get("issued"), "invoice date")
    v["period_from"] = _date(b.get("period_from"), "period start")
    v["period_to"] = _date(b.get("period_to"), "period end")
    if v["period_from"] > v["period_to"]:
        raise core.Invalid("the period ends before it starts")
    try:
        v["retainer"] = round(float(b.get("retainer") or 0), 2)
    except (TypeError, ValueError):
        raise core.Invalid("retainer must be a number") from None
    if v["retainer"] < 0:
        raise core.Invalid("enter the retainer as a positive amount; it is deducted")
    v["retainer_date"] = _date(b.get("retainer_date"), "retainer date", required=False)
    if v["retainer"] and not v["retainer_date"]:
        raise core.Invalid("a retainer needs the date it was paid (it is printed)")
    if not v["retainer"]:
        v["retainer_date"] = None
    pcts = b.get("credit_pcts")
    if pcts is None:
        pcts = [c["pct"] for c in profile["credits"]]
    if isinstance(pcts, str):
        pcts = json.loads(pcts)
    if not isinstance(pcts, list) or len(pcts) != len(profile["credits"]):
        raise core.Invalid(f"the invoice settings now have {len(profile['credits'])} "
                           f"credits; open this draft, check the percentages and save")
    try:
        pcts = [float(p) for p in pcts]
    except (TypeError, ValueError):
        raise core.Invalid("credit percentages must be numbers") from None
    if any(not 0 <= p <= 100 for p in pcts):
        raise core.Invalid("credit percentages must be between 0 and 100")
    v["credit_pcts"] = json.dumps([int(p) if p == int(p) else p for p in pcts])
    v["note"] = str(b.get("note") or "")
    clash = con.execute("SELECT status FROM invoices WHERE number = ? COLLATE NOCASE "
                        "AND id IS NOT ?", (v["number"], own_id)).fetchone()
    if clash:
        raise core.Invalid(f"invoice number {v['number']} is already used "
                           f"({clash['status']})")
    return v


def _build_row(con, r, profile):
    ret = r["retainer"] or 0
    return build(con, profile, r["number"], r["issued"], r["period_from"],
                 r["period_to"], ret,
                 retainer_label(profile, r["retainer_date"]) if ret else "",
                 json.loads(r["credit_pcts"]))


def summary(inv):
    """The built invoice without the per-line detail, for the UI."""
    out = {k: v for k, v in inv.items() if k not in ("sections", "by_day")}
    out["days"] = len(inv["by_day"])
    out["sections"] = [{"heading": s["heading"], "hours": s["hours"], "groups": [
        {"label": g["label"], "subtotal": g["subtotal"], "hours": g["hours"],
         "lines": len(g["lines"])} for g in s["groups"]]} for s in inv["sections"]]
    return out


def preview(con, b):
    """Builds unsaved parameters, for the editor's live totals. Writes nothing."""
    profile = load_profile()
    return summary(_build_row(con, _clean(con, b, profile, b.get("id")), profile))


def _get_row(con, id_):
    r = con.execute("SELECT * FROM invoices WHERE id = ?", (id_,)).fetchone()
    if not r:
        raise core.Invalid(f"invoice {id_} not found")
    return dict(r)


def _public(r):
    out = {k: r[k] for k in ("id", "number", "issued", "period_from", "period_to",
                             "retainer", "retainer_date", "note", "status",
                             "created_utc", "updated_utc", "issued_utc", "voided_utc")}
    out["credit_pcts"] = json.loads(r["credit_pcts"])
    return out


def create(con, b):
    v = _clean(con, b, load_profile())
    now = db.now_utc()
    with db.tx(con):
        id_ = con.execute(
            "INSERT INTO invoices(number, issued, period_from, period_to, retainer, "
            "retainer_date, credit_pcts, note, created_utc, updated_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(v[k] for k in FIELDS) + (now, now)).lastrowid
        db.audit(con, "create", "invoice", id_, None, v)
    return get(con, id_)


def update(con, id_, b):
    r = _get_row(con, id_)
    if r["status"] != "draft":
        raise core.Invalid(f"{r['number']} is {r['status']}; only drafts can be edited")
    v = _clean(con, {**_public(r), **b}, load_profile(), id_)
    with db.tx(con):
        con.execute("UPDATE invoices SET " + ", ".join(f"{k} = ?" for k in FIELDS)
                    + ", updated_utc = ? WHERE id = ?",
                    tuple(v[k] for k in FIELDS) + (db.now_utc(), id_))
        db.audit(con, "update", "invoice", id_, {k: r[k] for k in FIELDS}, v)
    return get(con, id_)


def _expect(r, number):
    """Refuse when the invoice is no longer the number the caller confirmed
    (a draft renamed meanwhile). Checked inside the changing transaction."""
    if number is not None and r["number"] != number:
        raise core.Invalid(f"invoice {r['id']} is now {r['number']}, not {number}; "
                           "nothing was changed")


def delete(con, id_, number=None):
    with db.tx(con):
        r = _get_row(con, id_)
        _expect(r, number)
        if r["status"] != "draft":
            raise core.Invalid(f"{r['number']} is {r['status']}; void it instead")
        con.execute("DELETE FROM invoices WHERE id = ?", (id_,))
        db.audit(con, "delete", "invoice", id_, {k: r[k] for k in FIELDS}, None)
    return {"deleted": id_}


def issue(con, id_, number=None):
    # One write transaction from the first read to the last write, so an
    # invoice issued from another process (the app and an agent) can't slip
    # between the overlap check and the freeze.
    with db.tx(con):
        r = _get_row(con, id_)
        _expect(r, number)
        if r["status"] != "draft":
            raise core.Invalid(f"{r['number']} is already {r['status']}")
        if not os.path.exists(PROFILE_PATH):
            raise core.Invalid("these invoices still use the example business and "
                               "client; save your own on Invoice settings before issuing")
        profile = with_client_id(con, load_profile())     # stored with the invoice
        if "id" not in profile["client"]:
            raise core.Invalid(f"can't tell which client "
                               f"{profile['client']['name'].strip()!r} is; pick it again "
                               "on Invoice settings and save")
        # Another client's invoice may cover the same days.
        mine = _client_ident(profile)
        for o in con.execute(
                "SELECT number, period_from, period_to, profile FROM invoices "
                "WHERE status = 'issued' AND id <> ? AND period_from <= ? "
                "AND period_to >= ?", (id_, r["period_to"], r["period_from"])).fetchall():
            theirs = with_client_id(con, json.loads(o["profile"] or "{}"))
            name = (theirs.get("client") or {}).get("name") or ""
            if _client_ident(theirs)[0] == "name" and name.strip():
                raise core.Invalid(f"{o['number']} bills {o['period_from']} to "
                                   f"{o['period_to']} for {name.strip()!r}, which isn't "
                                   "exactly one of your clients now, so it may be this "
                                   "client; void it or change the period")
            if _client_ident(theirs) == mine:
                raise core.Invalid(f"{o['number']} already bills {o['period_from']} "
                                   f"to {o['period_to']}; void it or change the period")
        inv = _build_row(con, r, profile)
        data = render(inv, profile)
        now = db.now_utc()
        con.execute("UPDATE invoices SET status = 'issued', snapshot = ?, profile = ?, "
                    "pdf = ?, issued_utc = ?, updated_utc = ? WHERE id = ?",
                    (json.dumps(inv, ensure_ascii=False),
                     json.dumps(profile, ensure_ascii=False), data, now, now, id_))
        db.audit(con, "issue", "invoice", id_, None,
                 {"number": r["number"], "hours": inv["hours"], "total": inv["total"],
                  "net": inv["net"], "pdf_bytes": len(data)})
    return get(con, id_)


def void(con, id_, reason="", number=None):
    now = db.now_utc()
    with db.tx(con):
        r = _get_row(con, id_)
        _expect(r, number)
        if r["status"] != "issued":
            raise core.Invalid(f"{r['number']} is {r['status']}; only issued invoices "
                               "can be voided")
        con.execute("UPDATE invoices SET status = 'void', voided_utc = ?, updated_utc = ? "
                    "WHERE id = ?", (now, now, id_))
        db.audit(con, "void", "invoice", id_, {"status": "issued"},
                 {"status": "void", "reason": str(reason or "")})
    return get(con, id_)


def get(con, id_, lines=False):
    """An invoice with its summary. With lines, also its sections line by line,
    from the same build as the summary."""
    r = _get_row(con, id_)
    out = _public(r)
    if r["status"] == "draft":
        try:
            inv = _build_row(con, r, load_profile())
            out["invoice"] = summary(inv)
        except core.Invalid as e:
            out["error"] = str(e)
    else:
        inv = json.loads(r["snapshot"])
        out["invoice"] = summary(inv)
        # Entries stay editable after issue. Say so when they no longer add up
        # to what was sent, rather than let the reports and the invoice differ
        # silently.
        live = reports.invoice_lines(con, {"from": r["period_from"], "to": r["period_to"]})
        mine = for_client(live["lines"], with_client_id(con, json.loads(r["profile"])))[0]
        out["live_hours"] = sum(l["hours"] for l in mine)
    if lines and "invoice" in out:
        out["sections"] = inv["sections"]
    return out


def list_all(con):
    profile = None
    out = []
    for r in con.execute("SELECT * FROM invoices ORDER BY issued DESC, id DESC").fetchall():
        r = dict(r)
        x = _public(r)
        try:
            if r["snapshot"]:
                inv = json.loads(r["snapshot"])
            else:
                profile = profile or load_profile()
                inv = _build_row(con, r, profile)
            x.update(hours=inv["hours"], total=inv["total"], net=inv["net"])
        except core.Invalid as e:
            x["error"] = str(e)
        out.append(x)
    return out


def pdf_for(con, id_):
    """(filename, bytes). Issued and void invoices return the stored document;
    a draft is rendered now, watermarked DRAFT."""
    r = _get_row(con, id_)
    name = f"Invoice_{r['number']}"
    if r["status"] == "draft":
        profile = load_profile()
        return name + " (draft).pdf", render(_build_row(con, r, profile), profile,
                                             draft=True)
    return name + (" (void).pdf" if r["status"] == "void" else ".pdf"), r["pdf"]


def defaults(con, today):
    """Starting values for a new invoice: last month, dated today, with the
    previous invoice's credits and retainer. The number is not guessed (the
    sequence may have gaps); the last one is shown instead."""
    profile = load_profile()
    heads = {x.get("key"): x["heading"].capitalize() for x in profile["sections"]}
    end = date.fromisoformat(today).replace(day=1) - timedelta(days=1)
    last = con.execute("SELECT number, retainer, credit_pcts FROM invoices "
                       "WHERE status <> 'void' ORDER BY issued DESC, id DESC").fetchone()
    return {
        "issued": today, "period_from": end.replace(day=1).isoformat(),
        "period_to": end.isoformat(),
        "retainer": last["retainer"] if last else 0,
        "credit_pcts": _fit_pcts(json.loads(last["credit_pcts"]), profile) if last else
        [c["pct"] for c in profile["credits"]],
        "last_number": last["number"] if last else "",
        "credits": [{"scope": c["scope"].get("project") or heads.get(c["scope"].get("section"),
                                                                   c["scope"].get("section")),
                     "pct": c["pct"]} for c in profile["credits"]],
    }


# ---- the invoice profile: standing text and billing rules --------------------
#
# Edited on the Invoices page (Invoice settings). Saving validates it, checks
# every printed string fits where the PDF puts it, keeps a dated copy of the
# old file in data/backups/ and logs before/after in the audit table. Issued
# invoices keep the profile they were issued with, so only drafts change.

# (font, size, room in points) for strings with a fixed place on the PDF.
FIT = {"issuer.name": ("B", 19, 330), "issuer.contact": ("R", 8, 330),
       "client.address": ("R", 8.5, 200), "client.address[0]": ("B", 8.5, 200),
       "client.email": ("R", 8.5, 200), "agreement": ("R", 8.5, 160),
       "client.attn": ("R", 8.5, 160), "terms": ("R", 6.5, 104),
       "remittance": ("R", 7.5, R - M), "heading": ("B", 9.5, R - M),
       "group.label": ("B", 8, R - M), "group.subtotal": ("B", 7.5, 400),
       "credit.label": ("R", 8, 430), "retainer_label": ("R", 8, 430),
       "credit_note.head": ("B", 7.5, R - M)}
MAX_ADDRESS, MAX_INTRO_LINES, MAX_CREDITS = 6, 3, 4


def _txt(v, where, required=True, fit=None):
    s = v if isinstance(v, str) else ("" if v is None else str(v))
    s = s.strip()
    if required and not s:
        raise core.Invalid(f"{where} is required")
    try:
        pdf._encode(s)
        pdf.width(s)
    except ValueError as e:
        raise core.Invalid(f"{where}: {e}") from None
    if fit and s:
        font, size, room = FIT[fit]
        if pdf.width(s, font, size) > room:
            raise core.Invalid(f"{where} is too long to fit on the invoice: {s!r}")
    return s


def _num(v, where, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise core.Invalid(f"{where} must be a number") from None
    if not lo < x <= hi:
        raise core.Invalid(f"{where} must be more than {lo:g} and at most {hi:g}")
    return int(x) if x == int(x) else x


def _lines(v, where, fit, required=True, most=None):
    if isinstance(v, str):
        v = v.split("\n")
    out = [_txt(s, f"{where}, line {i + 1}", False, fit) for i, s in enumerate(v or [])]
    out = [s for s in out if s]
    if required and not out:
        raise core.Invalid(f"{where} needs at least one line")
    if most and len(out) > most:
        raise core.Invalid(f"{where} has room for {most} lines, not {len(out)}")
    return out


def clean_profile(con, b, current=None):
    """The posted profile, checked. Keys the editor doesn't know about are
    kept from the current file."""
    p = dict(current or {})
    iss, cli = b.get("issuer") or {}, b.get("client") or {}
    p["issuer"] = {**p.get("issuer", {}),
                   "name": _txt(iss.get("name"), "Your name"),
                   "phone": _txt(iss.get("phone"), "Phone", False),
                   "email": _txt(iss.get("email"), "Email", False)}
    _txt(p["issuer"]["name"].upper(), "Your name", fit="issuer.name")
    _txt(f"{p['issuer']['phone']}   ·   {p['issuer']['email']}", "Phone and email",
         False, "issuer.contact")
    addr = _lines(cli.get("address"), "Bill-to address", "client.address", most=MAX_ADDRESS)
    _txt(addr[0], "Bill-to address, line 1", fit="client.address[0]")
    # The client whose time is billed; empty bills time with no client.
    name = (cli["name"] if "name" in cli else p.get("client", {}).get("name")) or ""
    cid = None
    if name.strip():
        hit = [tuple(r) for r in con.execute("SELECT id, name FROM clients")
               if _client_key(r[1]) == _client_key(name)]
        if not hit:
            raise core.Invalid(f"Client {name.strip()!r} is not in your clients; "
                               "add it on Projects / Clients, or pick another")
        cid, name = hit[0]
    p["client"] = {**p.get("client", {}), "id": cid, "name": name.strip(), "address": addr,
                   "attn": _txt(cli.get("attn"), "Attention", False, "client.attn"),
                   "email": _txt(cli.get("email"), "Client email", False, "client.email")}
    p["agreement"] = _txt(b.get("agreement"), "Agreement", fit="agreement")
    p["terms"] = _txt(b.get("terms"), "Terms", fit="terms")
    p["intro"] = _txt(b.get("intro"), "Introduction", False)
    if len(pdf.wrap(p["intro"], R - M, "I", 8)) > MAX_INTRO_LINES:
        raise core.Invalid(f"Introduction is limited to {MAX_INTRO_LINES} printed lines")
    p["rate"] = _num(b.get("rate"), "Hourly rate", 0, 10000)
    mh = b.get("max_hours")
    p["max_hours"] = 0 if mh in (0, "0") else _num(mh, "Monthly maximum hours (0 for none)", 0, 744)
    p["remittance"] = _lines(b.get("remittance"), "Remittance lines", "remittance",
                             required=False, most=6)
    rl = _txt(b.get("retainer_label") or "Less: Retainer Fee Paid on {date}",
              "Retainer line")
    try:
        _txt(rl.format(date="30-Sep-26"), "Retainer line", fit="retainer_label")
    except (KeyError, IndexError, ValueError):
        raise core.Invalid("Retainer line may only use {date} in braces") from None
    p["retainer_label"] = rl

    known = {}                      # project name -> set of task names
    for r in con.execute("SELECT p.name AS p, t.name AS t FROM projects p "
                         "LEFT JOIN tasks t ON t.project_id = p.id"):
        known.setdefault(r["p"], {""}).add(r["t"] or "")
    seen, keys, sections = {}, set(), []
    if not b.get("sections"):
        raise core.Invalid("at least one section is needed")
    for si, s in enumerate(b["sections"], 1):
        head = _txt(s.get("heading"), f"Section {si} heading", fit="heading")
        out = {"heading": head}
        key = str(s.get("key") or "").strip()
        if key:
            if key in keys:
                raise core.Invalid(f"two sections share the key {key!r}")
            keys.add(key)
            out["key"] = key
        intro = _txt(s.get("intro"), f"{head} introduction", False)
        if intro:
            out["intro"] = intro
        groups = s.get("groups") or [{"label": "", "subtotal": s.get("subtotal"),
                                      "pairs": s.get("pairs")}]
        clean_groups = []
        for gi, g in enumerate(groups, 1):
            where = f"{head}, group {gi}"
            pairs = []
            for pr in g.get("pairs") or []:
                proj, tsk = (str(x or "").strip() for x in (list(pr) + [""])[:2])
                name = f"{proj} / {tsk or '(no task)'}"
                if proj not in known or tsk not in known[proj]:
                    raise core.Invalid(f"{where}: {name} is not a project and task here")
                if (proj, tsk) in seen:
                    raise core.Invalid(f"{name} is in both {seen[(proj, tsk)]} and {where}")
                seen[(proj, tsk)] = where
                pairs.append([proj, tsk])
            if not pairs:
                raise core.Invalid(f"{where} has no projects or tasks")
            clean_groups.append({
                "label": _txt(g.get("label"), f"{where} label", False, "group.label"),
                "subtotal": _txt(g.get("subtotal"), f"{where} subtotal line",
                                 fit="group.subtotal"),
                "pairs": pairs})
        # One unlabelled group is stored flat, the shape the file always had.
        if len(clean_groups) == 1 and not clean_groups[0]["label"]:
            out.update(subtotal=clean_groups[0]["subtotal"], pairs=clean_groups[0]["pairs"])
        else:
            out["groups"] = clean_groups
        sections.append(out)
    p["sections"] = sections

    credits = []
    if len(b.get("credits") or []) > MAX_CREDITS:
        raise core.Invalid(f"at most {MAX_CREDITS} credits fit on the invoice")
    for ci, c in enumerate(b.get("credits") or [], 1):
        label = _txt(c.get("label"), f"Credit {ci} wording")
        try:
            _txt(label.format(pct="100"), f"Credit {ci} wording", fit="credit.label")
        except (KeyError, IndexError, ValueError):
            raise core.Invalid(f"Credit {ci} wording may only use {{pct}} in braces") from None
        sc = c.get("scope") or {}
        if bool(sc.get("project")) == bool(sc.get("section")):
            raise core.Invalid(f"Credit {ci} applies to a project or a section; pick one")
        if sc.get("project") and sc["project"] not in known:
            raise core.Invalid(f"Credit {ci}: no project named {sc['project']!r}")
        if sc.get("section") and sc["section"] not in keys:
            raise core.Invalid(f"Credit {ci}: no section with key {sc['section']!r}")
        credits.append({"label": label, "pct": _pct_num(c.get("pct"), ci),
                        "scope": {"project": sc["project"]} if sc.get("project")
                        else {"section": sc["section"]}})
    p["credits"] = credits
    n = b.get("credit_note") or {}
    p["credit_note"] = {"head": _txt(n.get("head"), "Credit note heading", bool(credits),
                                     "credit_note.head"),
                        "text": _txt(n.get("text"), "Credit note text", bool(credits))}
    return p


def _pct_num(v, ci):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise core.Invalid(f"Credit {ci} default percentage must be a number") from None
    if not 0 <= x <= 100:
        raise core.Invalid(f"Credit {ci} default percentage must be between 0 and 100")
    return int(x) if x == int(x) else x


def save_profile(con, b, path=None):
    """Validate and save the profile, keeping a copy of the old one. `b` is
    the whole profile, or a function from the current one to it (a partial
    change). The read, the change and the write happen under the database's
    write lock, so a save made meanwhile from another process (the app and an
    agent) is built on, not silently undone."""
    path = path or PROFILE_PATH
    with db.tx(con):
        before = load_profile(path)
        p = clean_profile(con, b(before) if callable(b) else b, before)
        if p == before:
            return p
        # Keep the old file first, and check the copy before replacing it. The
        # first save replaces the example, which has nothing to keep.
        if os.path.exists(path):
            folder = os.path.join(os.path.dirname(path), "backups")
            os.makedirs(folder, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            with open(path, "rb") as f:
                old = f.read()
            bak, n = os.path.join(folder, f"invoice_profile-{stamp}.json"), 2
            while True:
                try:
                    with open(bak, "xb") as f:      # never over an older copy
                        f.write(old)
                    break
                except FileExistsError:             # two saves in one second
                    bak, n = os.path.join(folder, f"invoice_profile-{stamp}-{n}.json"), n + 1
            with open(bak, "rb") as f:
                if f.read() != old:
                    raise core.Invalid(f"backup {bak} did not verify; profile not saved")
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(p, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        db.audit(con, "update", "invoice_profile", None, before, p)
    return p


def _fit_pcts(pcts, profile):
    """A stored list of percentages, padded or cut to the profile's credits."""
    dflt = [c["pct"] for c in profile["credits"]]
    return (list(pcts) + dflt[len(pcts):])[:len(dflt)]


def profile_preview(con, b):
    """A DRAFT PDF of the newest invoice (or last month) with an unsaved
    profile, so a wording or layout change can be seen before saving."""
    profile = clean_profile(con, b, load_profile())
    r = con.execute("SELECT * FROM invoices ORDER BY (status = 'draft') DESC, "
                    "issued DESC, id DESC").fetchone()
    if r:
        r = dict(r)
    else:
        d = defaults(con, date.today().isoformat())
        r = {**d, "number": "PREVIEW", "retainer_date": d["period_to"] if d["retainer"]
             else None, "credit_pcts": json.dumps(d["credit_pcts"])}
    r["credit_pcts"] = json.dumps(_fit_pcts(json.loads(r["credit_pcts"]), profile))
    return render(_build_row(con, r, profile), profile, draft=True)
