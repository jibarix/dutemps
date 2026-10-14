"""Core behaviour: CSV import, rounding, timer, rates, export, recovery and
the server's guards.

    python -m unittest discover -s tests -v
"""
import contextlib
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from dutemps import core, db, exports, importers, invoices, reports, server  # noqa: E402
from dutemps import __main__ as cli  # noqa: E402

SAMPLE_CSV = os.path.join(HERE, "tests", "fixtures", "sample_month.csv")

try:
    import openpyxl  # noqa: F401
    HAVE_OPENPYXL = True
except ImportError:
    HAVE_OPENPYXL = False


def fresh():
    """An empty database at UTC-4 with a $125 default rate; the expectations
    below are written for those settings."""
    con = db.connect(":memory:")
    core.set_settings(con, {"timezone": "Etc/GMT+4", "default_rate": "125"})
    return con


def expected_lines(path):
    """Billed hours per (date, project, task), worked out straight from the
    CSV: minutes summed per local start date, rounded half up to the quarter
    hour, never less than 0.25 h."""
    mins = defaultdict(int)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["billable"] != "true":
                continue
            s = int(r["start_time"][:2]) * 60 + int(r["start_time"][3:5])
            e = int(r["end_time"][:2]) * 60 + int(r["end_time"][3:5])
            if r["end_date"] != r["start_date"]:
                e += 24 * 60
            mins[(r["start_date"], r["project"], r["task"])] += e - s
    return {k: max(0.25, float((Decimal(m) / 15).quantize(0, ROUND_HALF_UP)) / 4)
            for k, m in mins.items()}


class CsvImport(unittest.TestCase):
    def test_sample_month_matches_and_reimport_adds_nothing(self):
        con = fresh()
        first = importers.import_csv(con, SAMPLE_CSV)
        self.assertEqual(first["added"], 72)
        again = importers.import_csv(con, SAMPLE_CSV)
        self.assertEqual((again["added"], again["already_present"]), (0, 72))

        r = reports.invoice_lines(con, {"from": "2026-09-01", "to": "2026-09-30"})
        self.assertEqual((r["hours"], r["amount"]), (71.25, 7125.0))
        mine = {(l["date"], l["project"], l["task"]): l["hours"] for l in r["lines"]}
        want = expected_lines(SAMPLE_CSV)
        self.assertEqual(mine, want)
        self.assertEqual(want[("2026-09-13", "Data Warehouse", "Pipelines")], 0.75)  # past midnight
        self.assertEqual(want[("2026-09-19", "Support", "")], 0.25)                 # 1 minute

    def test_dry_run_writes_nothing(self):
        con = fresh()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["project", "client", "description", "task", "user", "email",
                        "billable", "start_date", "start_time", "end_date", "end_time",
                        "duration_hours", "billable_rate", "billable_amount"])
            w.writerow(["P", "C", "d", "T", "u", "e", "true", "2026-09-01", "23:30:00",
                        "2026-09-02", "00:00:00", "0.5", "125.00", "62.50"])
        try:
            out = importers.import_csv(con, f.name, dry_run=True)
            self.assertEqual(out["added"], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 0)
            importers.import_csv(con, f.name)
            e = core.list_entries(con)[0]
            # 23:30 local (UTC-4) is 03:30 UTC next day, but groups under Sept 1
            self.assertEqual(e["start_utc"], "2026-09-02T03:30:00Z")
            self.assertEqual(reports.invoice_lines(con, {"from": "2026-09-01",
                                                         "to": "2026-09-01"})["hours"], 0.5)
            self.assertEqual(e["hourly_rate"], 125.0)
        finally:
            os.unlink(f.name)


class Clients(unittest.TestCase):
    def test_same_project_name_for_two_clients_stays_apart(self):
        cat = importers.Catalog(fresh())
        a = cat.project("Website", "Client A")
        b = cat.project("Website", "Client B")
        self.assertEqual(len({a, b, cat.project("Website")}), 3)
        # a project filed under no client is taken over by the first client
        con = cat.con
        loose = cat.project("Intranet")
        self.assertEqual(cat.project("Intranet", "Client A"), loose)
        self.assertEqual(db.row(con, "projects", loose)["client_id"],
                         cat.client("Client A"))
        self.assertNotEqual(cat.project("Intranet", "Client B"), loose)


class ScratchDatabase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_bare_file_name(self):
        old = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, old)
        con = db.connect("scratch.db")
        self.assertEqual(os.path.realpath(db.db_dir(con)), os.path.realpath(self.dir))
        con.close()

    def test_invoice_profile_lives_beside_the_database(self):
        old = invoices.PROFILE_PATH
        self.addCleanup(setattr, invoices, "PROFILE_PATH", old)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--db", os.path.join(self.dir, "s.db"), "backup",
                      os.path.join(self.dir, "b.db")])
        self.assertEqual(os.path.realpath(invoices.PROFILE_PATH),
                         os.path.realpath(os.path.join(self.dir, "invoice_profile.json")))


class Totals(unittest.TestCase):
    def test_page_totals_cover_the_whole_week_not_just_the_page(self):
        con = fresh()
        for day in ("2026-10-12", "2026-10-13", "2026-10-14"):      # Mon-Wed
            core.create_entry(con, {"start_utc": f"{day}T14:00:00Z",
                                    "end_utc": f"{day}T15:00:00Z"})
        page = core.list_entries(con, limit=1)                       # Wed only
        t = reports.page_totals(con, {}, page)
        self.assertEqual(t["weeks"], {"2026-10-12": 3 * 3600})
        self.assertEqual(t["days"]["2026-10-12"], 3600)


class Rounding(unittest.TestCase):
    def test_quarter_hour_rule(self):
        self.assertEqual(reports.billed_hours(0.0112), 0.25)     # floor
        self.assertEqual(reports.billed_hours(1.3003), 1.25)
        self.assertEqual(reports.billed_hours(1.38), 1.5)
        self.assertEqual(reports.billed_hours(0), 0.0)


class Timer(unittest.TestCase):
    def test_second_start_stops_first_at_same_instant(self):
        con = fresh()
        a = core.start_timer(con, {"description": "a", "start_utc": "2026-10-14T12:00:00Z"})
        b = core.start_timer(con, {"description": "b"})
        a2 = core.get_entry(con, a["id"])
        self.assertIsNotNone(a2["end_utc"])
        self.assertEqual(a2["end_utc"], b["start_utc"])
        self.assertEqual(core.running(con)["id"], b["id"])

    def test_database_refuses_two_running(self):
        con = fresh()
        core.start_timer(con, {})
        with self.assertRaises(Exception):
            con.execute("INSERT INTO entries(start_utc, created_utc, updated_utc) "
                        "VALUES ('2026-01-01T00:00:00Z', 'x', 'x')")

    def test_stop_and_continue(self):
        con = fresh()
        p = core.create(con, "projects", {"name": "P", "color": "#03A9F4"})
        a = core.start_timer(con, {"description": "x", "project_id": p["id"],
                                   "start_utc": "2026-10-14T12:00:00Z"})
        core.stop_timer(con)
        c = core.continue_entry(con, a["id"])
        self.assertEqual((c["description"], c["project_id"]), ("x", p["id"]))
        self.assertTrue(c["running"])


class Rates(unittest.TestCase):
    def test_chain_entry_task_project_client_default(self):
        con = fresh()
        c = core.create(con, "clients", {"name": "C"})
        p = core.create(con, "projects", {"name": "P", "client_id": c["id"], "color": "#03A9F4"})
        t = core.create(con, "tasks", {"name": "T", "project_id": p["id"]})
        e = core.create_entry(con, {"project_id": p["id"], "task_id": t["id"],
                                    "start_utc": "2026-10-14T12:00:00Z",
                                    "end_utc": "2026-10-14T13:00:00Z"})
        rate = lambda: core.get_entry(con, e["id"])["rate"]
        self.assertEqual(rate(), 125.0)                                   # default
        core.update(con, "clients", c["id"], {"hourly_rate": 100})
        self.assertEqual(rate(), 100.0)
        core.update(con, "projects", p["id"], {"hourly_rate": 90})
        self.assertEqual(rate(), 90.0)
        core.update(con, "tasks", t["id"], {"hourly_rate": 80})
        self.assertEqual(rate(), 80.0)
        core.update_entry(con, e["id"], {"hourly_rate": 70})
        self.assertEqual(rate(), 70.0)
        self.assertEqual(core.get_entry(con, e["id"])["amount"], 70.0)

    def test_task_must_belong_to_project(self):
        con = fresh()
        p1 = core.create(con, "projects", {"name": "P1", "color": "#03A9F4"})
        p2 = core.create(con, "projects", {"name": "P2", "color": "#03A9F4"})
        t = core.create(con, "tasks", {"name": "T", "project_id": p1["id"]})
        with self.assertRaises(core.Invalid):
            core.create_entry(con, {"project_id": p2["id"], "task_id": t["id"],
                                    "start_utc": "2026-10-14T12:00:00Z",
                                    "end_utc": "2026-10-14T13:00:00Z"})


class Export(unittest.TestCase):
    def test_detailed_has_its_columns_in_order(self):
        con = fresh()
        core.create_entry(con, {"description": "x", "start_utc": "2026-10-14T12:00:00Z",
                                "end_utc": "2026-10-14T13:30:00Z"})
        head, rows = exports.detailed_rows(con, {})
        self.assertEqual(head, [
            "Project", "Client", "Description", "Task", "User", "Group", "Email", "Tags",
            "Billable", "Start Date", "Start Time", "End Date", "End Time",
            "Duration (h)", "Duration (decimal)", "Billable Rate (USD)",
            "Billable Amount (USD)", "Date of creation"])
        self.assertEqual(rows[0][10], "08:00:00")          # local UTC-4
        self.assertEqual(rows[0][13], "01:30:00")
        self.assertEqual(rows[0][14], 1.5)
        self.assertEqual(rows[0][16], 187.5)
        if HAVE_OPENPYXL:
            self.assertTrue(exports.to_xlsx(head, rows, "Detailed").startswith(b"PK"))
        else:
            self.assertRaises(core.Invalid, exports.to_xlsx, head, rows, "Detailed")


class Recovery(unittest.TestCase):
    def test_delete_then_restore(self):
        con = fresh()
        tg = core.create(con, "tags", {"name": "urgent"})
        e = core.create_entry(con, {"description": "keep me", "tag_ids": [tg["id"]],
                                    "start_utc": "2026-10-14T12:00:00Z",
                                    "end_utc": "2026-10-14T13:00:00Z"})
        core.delete_entries(con, [e["id"]])
        self.assertIsNone(core.get_entry(con, e["id"]))
        d = core.recent_deletes(con)
        self.assertEqual(d[0]["description"], "keep me")
        back = core.restore_entry(con, d[0]["audit_id"])
        self.assertEqual(back["description"], "keep me")
        self.assertEqual([t["name"] for t in back["tags"]], ["urgent"])
        self.assertEqual(core.recent_deletes(con), [])

    def test_undo_after_a_newer_entry_took_the_id(self):
        con = fresh()
        hour = {"start_utc": "2026-10-14T12:00:00Z", "end_utc": "2026-10-14T13:00:00Z"}
        e = core.create_entry(con, {**hour, "description": "old"})
        [aid] = core.delete_entries(con, [e["id"]])["audit_ids"]
        newer = core.create_entry(con, {**hour, "description": "newer"})
        self.assertEqual(newer["id"], e["id"])        # SQLite reused it
        self.assertEqual([d["audit_id"] for d in core.recent_deletes(con)], [aid])
        back = core.restore_entry(con, aid)
        self.assertEqual(back["description"], "old")
        self.assertNotEqual(back["id"], newer["id"])
        self.assertEqual(core.recent_deletes(con), [])
        with self.assertRaisesRegex(core.Invalid, "already restored"):
            core.restore_entry(con, aid)

    def test_delete_returns_the_audit_ids_undo_needs(self):
        con = fresh()
        a = core.create_entry(con, {"start_utc": "2026-10-14T12:00:00Z",
                                    "end_utc": "2026-10-14T13:00:00Z"})
        b = core.create_entry(con, {"start_utc": "2026-10-14T14:00:00Z",
                                    "end_utc": "2026-10-14T15:00:00Z"})
        out = core.delete_entries(con, [a["id"], b["id"]])
        for aid in out["audit_ids"]:
            core.restore_entry(con, aid)
        self.assertEqual(core.count_entries(con, {}), 2)

    def test_used_project_is_archived_not_deleted(self):
        con = fresh()
        p = core.create(con, "projects", {"name": "P", "color": "#03A9F4"})
        core.create_entry(con, {"project_id": p["id"], "start_utc": "2026-10-14T12:00:00Z",
                                "end_utc": "2026-10-14T13:00:00Z"})
        self.assertEqual(core.delete(con, "projects", p["id"]), {"archived": True})


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = fresh()
        cls.httpd = server.make_server(cls.con, port=0)
        cls.port = cls.httpd.server_address[1]
        server.Handler.port = cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, method, path, body=None, headers=None):
        h = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json",
             "X-DuTemps": "1", **(headers or {})}
        h = {k: v for k, v in h.items() if v is not None}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=h)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_write_without_header_is_refused(self):
        code, _ = self.call("POST", "/api/timer/start", {}, {"X-DuTemps": None})
        self.assertEqual(code, 403)

    def test_foreign_host_is_refused(self):
        code, _ = self.call("GET", "/api/meta", headers={"Host": "evil.example:80"})
        self.assertEqual(code, 421)

    def test_round_trip_and_static(self):
        code, body = self.call("POST", "/api/clients", {"name": "Acme"})
        self.assertEqual(code, 200)
        code, body = self.call("GET", "/api/meta")
        self.assertIn("Acme", [c["name"] for c in json.loads(body)["clients"]])
        code, body = self.call("POST", "/api/entries", {"start_utc": "bad"})
        self.assertEqual(code, 400)
        self.assertEqual(self.call("GET", "/")[0], 200)
        self.assertEqual(self.call("GET", "/../dutemps/db.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
