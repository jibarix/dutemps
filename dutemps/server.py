"""Local HTTP server: JSON API under /api, the single-page UI under /.

Safety for a localhost app (BRIEF, Constraints):
  * binds 127.0.0.1 only;
  * rejects a Host header that is not 127.0.0.1/localhost:<port> (DNS rebinding);
  * every write must carry `X-DuTemps: 1`. Browsers will not send a custom
    header cross-origin without a CORS preflight, which this server never
    approves, so another tab cannot forge a write.
One connection, serialised by a lock: SQLite plus a single user needs no more.
"""
import json
import mimetypes
import os
import re
import sqlite3
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import core, db, exports, invoices, reports
from .timeutil import zone

STATIC = os.path.join(db.HERE, "static")
mimetypes.add_type("text/javascript", ".js")   # Windows registry may say text/plain
mimetypes.add_type("text/css", ".css")
LOCK = threading.Lock()
ID = r"(\d+)"
TABLES = "clients|projects|tasks|tags"


def _filters(q):
    keys = ("from", "to", "project", "task", "client", "tag", "billable", "q",
            "no_project", "source")
    return {k: q[k] for k in keys if q.get(k) not in (None, "")}


def _today(con):
    return datetime.now(zone(db.settings(con)["timezone"])).date().isoformat()


class Api:
    """Route table: (method, regex) -> handler(con, match, query, body)."""

    def __init__(self):
        self.routes = []
        r = self.route
        r("GET", r"/api/meta", lambda c, m, q, b: {
            **core.meta(c), "running": core.running(c), "today": _today(c)})
        r("PUT", r"/api/settings", lambda c, m, q, b: core.set_settings(c, b))

        r("GET", rf"/api/({TABLES})", lambda c, m, q, b: core.meta(c)[m[1]])
        r("POST", rf"/api/({TABLES})", lambda c, m, q, b: core.create(c, m[1], b))
        r("PATCH", rf"/api/({TABLES})/{ID}",
          lambda c, m, q, b: core.update(c, m[1], int(m[2]), b))
        r("DELETE", rf"/api/({TABLES})/{ID}",
          lambda c, m, q, b: core.delete(c, m[1], int(m[2])))

        r("GET", r"/api/entries", self.entries)
        r("POST", r"/api/entries", lambda c, m, q, b: core.create_entry(c, b))
        r("GET", rf"/api/entries/{ID}", lambda c, m, q, b: core.get_entry(c, int(m[1])))
        r("PATCH", rf"/api/entries/{ID}",
          lambda c, m, q, b: core.update_entry(c, int(m[1]), b))
        r("DELETE", rf"/api/entries/{ID}",
          lambda c, m, q, b: core.delete_entries(c, [int(m[1])]))
        r("POST", r"/api/entries/delete", lambda c, m, q, b: core.delete_entries(c, b["ids"]))
        r("POST", rf"/api/entries/{ID}/duplicate",
          lambda c, m, q, b: core.duplicate_entry(c, int(m[1])))
        r("POST", rf"/api/entries/{ID}/continue",
          lambda c, m, q, b: core.continue_entry(c, int(m[1])))
        r("GET", r"/api/suggest", lambda c, m, q, b: core.suggestions(c, q.get("q", "")))
        r("GET", r"/api/deleted", lambda c, m, q, b: core.recent_deletes(c))
        r("POST", rf"/api/restore/{ID}", lambda c, m, q, b: core.restore_entry(c, int(m[1])))

        r("GET", r"/api/timer", lambda c, m, q, b: {"running": core.running(c)})
        r("POST", r"/api/timer/start", lambda c, m, q, b: core.start_timer(c, b))
        r("POST", r"/api/timer/stop", lambda c, m, q, b: core.stop_timer(c))
        r("POST", r"/api/timer/discard", lambda c, m, q, b: core.discard_timer(c))

        r("GET", r"/api/reports/summary", lambda c, m, q, b: reports.summary(
            c, _filters(q), q.get("group1") or "project", q.get("group2") or None))
        r("GET", r"/api/reports/weekly", lambda c, m, q, b: reports.weekly(
            c, _filters(q), q["week"]))
        r("GET", r"/api/reports/invoice", lambda c, m, q, b: reports.invoice_lines(
            c, _filters(q)))
        r("GET", r"/api/reports/series", lambda c, m, q, b: reports.series(
            c, _filters(q), q["from"], q["to"], q.get("by", "day")))
        r("GET", r"/api/dashboard", lambda c, m, q, b: reports.dashboard(
            c, q.get("today") or _today(c)))
        r("GET", r"/api/invoices", lambda c, m, q, b: {
            "invoices": invoices.list_all(c), "defaults": invoices.defaults(c, _today(c))})
        r("POST", r"/api/invoices", lambda c, m, q, b: invoices.create(c, b))
        r("POST", r"/api/invoices/preview", lambda c, m, q, b: invoices.preview(c, b))
        r("GET", rf"/api/invoices/{ID}", lambda c, m, q, b: invoices.get(c, int(m[1])))
        r("PATCH", rf"/api/invoices/{ID}",
          lambda c, m, q, b: invoices.update(c, int(m[1]), b))
        r("DELETE", rf"/api/invoices/{ID}", lambda c, m, q, b: invoices.delete(c, int(m[1])))
        r("POST", rf"/api/invoices/{ID}/issue", lambda c, m, q, b: invoices.issue(c, int(m[1])))
        r("POST", rf"/api/invoices/{ID}/void",
          lambda c, m, q, b: invoices.void(c, int(m[1]), b.get("reason", "")))
        r("GET", r"/api/invoice-profile", lambda c, m, q, b: invoices.profile_for_editor())
        r("PUT", r"/api/invoice-profile", lambda c, m, q, b: invoices.save_profile(c, b))
        r("POST", r"/api/backup", lambda c, m, q, b: {"path": db.backup(
            c, os.path.join(db.db_dir(c), "backups",
                            f"dutemps-{datetime.now():%Y%m%d-%H%M%S}.db"))})

    def route(self, method, pattern, fn):
        self.routes.append((method, re.compile(pattern + "$"), fn))

    def entries(self, c, m, q, b):
        f = _filters(q)
        limit = int(q.get("limit") or 50)
        offset = int(q.get("offset") or 0)
        out = {"entries": core.list_entries(c, f, limit=limit, offset=offset),
               "count": core.count_entries(c, f)}
        if q.get("totals") == "1":
            out["totals"] = reports.page_totals(c, f, out["entries"])
        return out

    def find(self, method, path):
        allowed = False
        for meth, rx, fn in self.routes:
            mm = rx.match(path)
            if mm:
                if meth == method:
                    return fn, mm
                allowed = True
        return (None, "method") if allowed else (None, None)


API = Api()


class Handler(BaseHTTPRequestHandler):
    server_version = "DuTemps"
    con = None          # set by serve()
    port = 8765

    def log_message(self, fmt, *args):     # quiet: one line per write only
        if self.command != "GET":
            super().log_message(fmt, *args)

    # --- plumbing ---
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None,
              csp=True):
        if ctype.startswith("application/json"):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if csp:     # off for PDFs, which Chrome's viewer would otherwise refuse
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self'; script-src 'self'; "
                             "img-src 'self' data:; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _handle(self):
        if not self._host_ok():
            return self._send(421, {"error": "bad Host header"})
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if not url.path.startswith("/api/"):
            if self.command != "GET":
                return self._send(405, {"error": "method not allowed"})
            return self._static(url.path)
        if self.command != "GET" and self.headers.get("X-DuTemps") != "1":
            return self._send(403, {"error": "missing X-DuTemps header"})
        if url.path == "/api/export":
            return self._export(q)
        if url.path == "/api/invoice-profile/preview" and self.command == "POST":
            return self._profile_preview()
        m = re.fullmatch(r"/api/invoices/(\d+)/pdf", url.path)
        if m and self.command == "GET":
            return self._invoice_pdf(int(m[1]), q)
        fn, m = API.find(self.command, url.path)
        if fn is None:
            return self._send(405 if m == "method" else 404, {"error": "not found"})
        body = {}
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return self._send(400, {"error": "body is not JSON"})
        try:
            with LOCK:
                out = fn(self.con, m, q, body)
            return self._send(200, out)
        except (core.Invalid, KeyError) as e:
            return self._send(400, {"error": str(e).strip("'")})
        except sqlite3.IntegrityError as e:     # a CHECK or trigger said no
            return self._send(400, {"error": str(e)})
        except Exception as e:      # keep serving; show the cause
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _export(self, q):
        kind, fmt = q.get("kind", "detailed"), q.get("format", "csv")
        try:
            with LOCK:
                head, rows = exports.table(self.con, kind, _filters(q),
                                           q.get("group1") or "project",
                                           q.get("group2") or None, q.get("week"))
        except (core.Invalid, KeyError) as e:
            return self._send(400, {"error": str(e)})
        span = f"{q.get('from', 'all')}_{q.get('to', 'all')}"
        name = f"DuTemps_{kind}_{span}.{fmt}"
        if fmt == "xlsx":
            data = exports.to_xlsx(head, rows, kind.title())
            ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            data, ctype = exports.to_csv(head, rows), "text/csv; charset=utf-8"
        self._send(200, data, ctype,
                   {"Content-Disposition": f'attachment; filename="{name}"'})

    def _invoice_pdf(self, id_, q):
        try:
            with LOCK:
                name, data = invoices.pdf_for(self.con, id_)
        except (core.Invalid, ValueError) as e:     # ValueError: unprintable text
            return self._send(400 if "not found" not in str(e) else 404, {"error": str(e)})
        how = "attachment" if q.get("download") == "1" else "inline"
        safe = re.sub(r'[^\w .()-]', "_", name)
        self._send(200, data, "application/pdf",
                   {"Content-Disposition": f'{how}; filename="{safe}"'}, csp=False)

    def _profile_preview(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        except ValueError:
            return self._send(400, {"error": "body is not JSON"})
        try:
            with LOCK:
                data = invoices.profile_preview(self.con, body)
        except core.Invalid as e:
            return self._send(400, {"error": str(e)})
        self._send(200, data, "application/pdf",
                   {"Content-Disposition": 'inline; filename="Invoice settings preview.pdf"'},
                   csp=False)

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        full = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle


def make_server(con, port=8765):
    Handler.con, Handler.port = con, port
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(con, port=8765, open_browser=True):
    httpd = make_server(con, port)
    url = f"http://127.0.0.1:{port}/"
    print(f"DuTemps on {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
