"""Invoices for the sample month with the example profile, and the PDF writer.

    python -m unittest discover -s tests -v

Every test points invoices.PROFILE_PATH at a temporary copy of
examples/invoice_profile.json, so your own data/ is never read or written.
"""
import json
import os
import shutil
import tempfile
import sqlite3
import sys
import threading
import unittest
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from dutemps import core, db, importers, invoices, pdf, server  # noqa: E402

SAMPLE_CSV = os.path.join(HERE, "tests", "fixtures", "sample_month.csv")

# The sample month: 71.25 h at $100 (tests/test_core.py checks it line by
# line). Website 33.25 h, Support 13.75 h, Data Warehouse 24.25 h.
SEPT = {"number": "NW-2026-09", "issued": "2026-10-05", "period_from": "2026-09-01",
        "period_to": "2026-09-30", "retainer": 500, "retainer_date": "2026-09-30",
        "credit_pcts": [10, 50]}
NET = 5605.0          # 7,125.00 - 332.50 - 687.50 - 500.00
NET_20_20 = 5685.0    # 7,125.00 - 665.00 - 275.00 - 500.00


def sample_db():
    con = db.connect(":memory:")
    core.set_settings(con, {"timezone": "Etc/GMT+4"})
    importers.import_csv(con, SAMPLE_CSV)
    return con


class ProfileDir(unittest.TestCase):
    """Points PROFILE_PATH at a temporary copy of the example profile."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "invoice_profile.json")
        shutil.copy(invoices.EXAMPLE_PROFILE, self.path)
        old = invoices.PROFILE_PATH
        invoices.PROFILE_PATH = self.path
        self.addCleanup(setattr, invoices, "PROFILE_PATH", old)
        self.con = sample_db()
        self.prof = invoices.load_profile()

    def serve(self):
        httpd = server.make_server(self.con, port=0)
        port = httpd.server_address[1]
        server.Handler.port = port
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def call(method, path, body=None):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json", "X-DuTemps": "1"})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.headers, e.read()
        return call


class Amounts(ProfileDir):
    def build(self, **kw):
        return invoices.build(self.con, self.prof, "NW-2026-09", "2026-10-05",
                              "2026-09-01", "2026-09-30", 500,
                              "Less: Retainer Fee Paid on 30-Sep-26", **kw)

    def test_sample_month(self):
        inv = self.build()
        self.assertEqual((inv["hours"], inv["total"]), (71.25, 7125.0))
        self.assertEqual([c["amount"] for c in inv["credits"]], [-332.5, -687.5])
        self.assertEqual([c["hours"] for c in inv["credits"]], [33.25, 13.75])
        self.assertEqual(inv["retainer"]["amount"], -500.0)
        self.assertEqual(inv["net"], NET)
        self.assertEqual((inv["max_amount"], inv["additional"]), (4000.0, 3125.0))
        # TRAINING has no hours this month, so it is left out.
        self.assertEqual([s["heading"] for s in inv["sections"]], ["PROJECT WORK", "SUPPORT"])
        self.assertEqual(inv["by_project"][0], ("Website", 33.25))

    def test_credit_percentages_can_be_changed_per_invoice(self):
        inv = self.build(credit_pcts=[20, 20])
        self.assertEqual(inv["credits"][0]["label"],
                         "Less: Returning-client discount on Website (20%)")
        self.assertEqual(inv["net"], NET_20_20)

    def test_pdf_is_deterministic_and_well_formed(self):
        inv = self.build()
        a, b = invoices.render(inv, self.prof), invoices.render(inv, self.prof)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith(b"%PDF-1.4") and a.rstrip().endswith(b"%%EOF"))
        xref = int(a.rsplit(b"startxref", 1)[1].split()[0])
        self.assertTrue(a[xref:].startswith(b"xref"))
        # every object offset in the xref table points at that object
        rows = a[xref:].split(b"\n")[3:]
        for n, row in enumerate(rows, 1):
            if not row.endswith(b" n "):
                break
            off = int(row[:10])
            self.assertTrue(a[off:].startswith(b"%d 0 obj" % n), n)

    def test_unmapped_project_is_refused(self):
        prof = dict(self.prof, sections=self.prof["sections"][:1])
        with self.assertRaises(core.Invalid) as e:
            invoices.build(self.con, prof, "X", "2026-10-05", "2026-09-01", "2026-09-30")
        self.assertIn("Support / (no task)", str(e.exception))


    def add(self, project, task, client=None, rate=None, day="2026-09-30", hour=12):
        cat = importers.Catalog(self.con)
        p = cat.project(project, client)
        return core.create_entry(self.con, {
            "project_id": p, "task_id": cat.task(p, task), "hourly_rate": rate,
            "description": f"Work for {client}", "start_utc": f"{day}T{hour}:00:00Z",
            "end_utc": f"{day}T{hour + 1}:00:00Z"})

    def test_another_clients_time_stays_off(self):
        self.add("Website", "Design", "Other Co", day="2026-09-02")
        inv = self.build()
        self.assertEqual((inv["hours"], inv["net"]), (71.25, NET))
        self.assertEqual(inv["left_out"], [("Other Co", 1.0)])
        doc = invoices.render(inv, self.prof)
        text = b"".join(zlib.decompress(part.rsplit(b"stream\n", 1)[1])
                        for part in doc.split(b"\nendstream")[:-1])
        self.assertIn(b"Website", text)
        self.assertNotIn(b"Other Co", text)

    def test_time_under_another_client_name_is_explained(self):
        prof = dict(self.prof, client=dict(self.prof["client"], name="Contoso"))
        with self.assertRaises(core.Invalid) as e:
            invoices.build(self.con, prof, "X", "2026-10-05", "2026-09-01", "2026-09-30")
        self.assertIn("filed under Northwind Traders", str(e.exception))

    def test_mixed_rates_on_one_line_are_refused(self):
        self.add("Website", "Design", "Northwind Traders", rate=100)
        self.add("Website", "Design", "Northwind Traders", rate=200, hour=14)
        with self.assertRaises(core.Invalid) as e:
            self.build()
        self.assertIn("2026-09-30 Website / Design", str(e.exception))


class Workflow(ProfileDir):
    """Draft -> issued -> void, and the database refusing changes to what was sent."""

    add = Amounts.add

    def test_draft_is_live_and_issue_freezes_it(self):
        d = invoices.create(self.con, SEPT)
        self.assertEqual((d["status"], d["invoice"]["net"]), ("draft", NET))
        self.assertEqual(d["invoice"]["retainer"]["label"],
                         "Less: Retainer Fee Paid on 30-Sep-26")
        self.assertEqual(invoices.update(self.con, d["id"], {"credit_pcts": [20, 20]})
                         ["invoice"]["net"], NET_20_20)
        invoices.update(self.con, d["id"], {"credit_pcts": [10, 50]})
        name, draft_pdf = invoices.pdf_for(self.con, d["id"])
        self.assertEqual(name, "Invoice_NW-2026-09 (draft).pdf")

        i = invoices.issue(self.con, d["id"])
        self.assertEqual((i["status"], i["invoice"]["net"], i["live_hours"]),
                         ("issued", NET, 71.25))
        name, sent = invoices.pdf_for(self.con, d["id"])
        self.assertEqual(name, "Invoice_NW-2026-09.pdf")
        self.assertNotEqual(sent, draft_pdf)          # no DRAFT stamp
        self.assertEqual(sent, invoices.render(json.loads(self.con.execute(
            "SELECT snapshot FROM invoices").fetchone()[0]), invoices.load_profile()))

        # Editing an entry afterwards changes the live figure, not the invoice.
        self.con.execute("UPDATE entries SET end_utc = strftime('%Y-%m-%dT%H:%M:%SZ', "
                         "end_utc, '+1 hour') WHERE id = (SELECT MIN(id) FROM entries)")
        g = invoices.get(self.con, d["id"])
        self.assertEqual(g["invoice"]["hours"], 71.25)
        self.assertNotEqual(g["live_hours"], 71.25)
        self.assertEqual(invoices.pdf_for(self.con, d["id"])[1], sent)

        for fn in (lambda: invoices.update(self.con, d["id"], {"note": "x"}),
                   lambda: invoices.delete(self.con, d["id"]),
                   lambda: invoices.issue(self.con, d["id"])):
            self.assertRaises(core.Invalid, fn)

    def test_another_client_may_bill_the_same_days(self):
        self.add("Website", "Design", "Other Co", rate=100, day="2026-09-02")
        i = invoices.issue(self.con, invoices.create(self.con, SEPT)["id"])
        self.assertEqual(i["live_hours"], 71.25)    # Other Co's hour isn't counted
        invoices.save_profile(self.con, {**self.prof, "client": {
            **self.prof["client"], "name": "Other Co"}, "credits": [],
            "sections": self.prof["sections"][:1]})          # no Training project here
        o = invoices.create(self.con, {**SEPT, "number": "OC-1", "retainer": 0,
                                       "credit_pcts": []})
        self.assertEqual(invoices.issue(self.con, o["id"])["invoice"]["hours"], 1.0)
        again = invoices.create(self.con, {**SEPT, "number": "OC-2", "retainer": 0,
                                           "credit_pcts": []})
        with self.assertRaisesRegex(core.Invalid, "OC-1 already bills"):
            invoices.issue(self.con, again["id"])

    def test_renaming_the_client_does_not_bill_twice(self):
        i = invoices.issue(self.con, invoices.create(self.con, SEPT)["id"])
        core.update(self.con, "clients", 1, {"name": "Northwind Ltd"})
        self.assertEqual(invoices.get(self.con, i["id"])["live_hours"], 71.25)
        invoices.save_profile(self.con, {**self.prof, "client": {
            **self.prof["client"], "name": "Northwind Ltd"},
            "sections": self.prof["sections"][:2]})          # no Training project here
        again = invoices.create(self.con, {**SEPT, "number": "NW-2"})
        with self.assertRaisesRegex(core.Invalid, "NW-2026-09 already bills"):
            invoices.issue(self.con, again["id"])

    def test_invoices_issued_before_ids_were_kept(self):
        i = invoices.issue(self.con, invoices.create(self.con, SEPT)["id"])
        self.con.execute("DROP TRIGGER invoices_frozen")      # make it look old
        self.con.execute("UPDATE invoices SET profile = json_remove(profile, '$.client.id')")
        self.assertNotIn("id", json.loads(self.con.execute(
            "SELECT profile FROM invoices WHERE id = ?", (i["id"],)).fetchone()[0])["client"])
        core.update(self.con, "clients", 1, {"name": "Northwind Ltd"})
        self.assertEqual(invoices.get(self.con, i["id"])["live_hours"], 71.25)  # via the audit
        invoices.save_profile(self.con, {**self.prof, "client": {
            **self.prof["client"], "name": "Northwind Ltd"},
            "sections": self.prof["sections"][:2]})
        again = invoices.create(self.con, {**SEPT, "number": "NW-2"})
        with self.assertRaisesRegex(core.Invalid, "NW-2026-09 already bills"):
            invoices.issue(self.con, again["id"])
        # once a second client takes the old name, the old invoice is ambiguous
        core.create(self.con, "clients", {"name": "Northwind Traders"})
        with self.assertRaisesRegex(core.Invalid, "isn't exactly one of your clients"):
            invoices.issue(self.con, again["id"])

    def test_a_profile_without_a_clear_client_cannot_issue(self):
        with open(self.path, encoding="utf-8") as f:
            legacy = json.load(f)
        legacy["client"].pop("id", None)                # saved before ids were kept
        core.update(self.con, "clients", 1, {"name": "Northwind Ltd"})
        core.create(self.con, "clients", {"name": "Northwind Traders"})
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        with self.assertRaisesRegex(core.Invalid, "pick it again on Invoice settings"):
            invoices.issue(self.con, invoices.create(self.con, SEPT)["id"])

    def test_example_profile_cannot_be_issued(self):
        os.remove(self.path)                          # back on the shipped example
        self.assertEqual(invoices.load_profile(), self.prof)
        self.assertTrue(invoices.profile_for_editor()["example"])
        d = invoices.create(self.con, SEPT)
        with self.assertRaises(core.Invalid) as e:
            invoices.issue(self.con, d["id"])
        self.assertIn("example business", str(e.exception))
        self.assertEqual(invoices.get(self.con, d["id"])["status"], "draft")

    def test_database_refuses_edits_to_issued(self):
        d = invoices.create(self.con, SEPT)
        invoices.issue(self.con, d["id"])
        for sql in ("UPDATE invoices SET retainer = 0",
                    "UPDATE invoices SET pdf = x'00'",
                    "UPDATE invoices SET status = 'draft'",
                    "DELETE FROM invoices"):
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                self.con.execute(sql)
        invoices.void(self.con, d["id"], "test")
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("UPDATE invoices SET status = 'issued'")
        audit = [r[0] for r in self.con.execute(
            "SELECT action FROM audit WHERE entity = 'invoice' ORDER BY id")]
        self.assertEqual(audit, ["create", "issue", "void"])

    def test_overlap_numbers_and_validation(self):
        a = invoices.create(self.con, SEPT)
        invoices.issue(self.con, a["id"])
        with self.assertRaises(core.Invalid):
            invoices.create(self.con, SEPT)                       # same number
        b = invoices.create(self.con, {**SEPT, "number": "NW-2026-09B",
                                       "period_from": "2026-09-15"})
        with self.assertRaises(core.Invalid) as e:
            invoices.issue(self.con, b["id"])
        self.assertIn("NW-2026-09 already bills", str(e.exception))
        invoices.void(self.con, a["id"])
        self.assertEqual(invoices.issue(self.con, b["id"])["status"], "issued")
        for bad in ({"number": " "}, {"period_to": "2026-08-01"}, {"retainer": -5},
                    {"retainer_date": ""}, {"credit_pcts": [10]}, {"credit_pcts": [10, 101]},
                    {"issued": "05/10/2026"}):
            with self.assertRaises(core.Invalid, msg=bad):
                invoices.preview(self.con, {**SEPT, "number": "X", **bad})

    def test_zero_percent_credit_prints_no_line(self):
        inv = invoices.preview(self.con, {**SEPT, "credit_pcts": [10, 0]})
        self.assertEqual(len(inv["credits"]), 1)

    def test_defaults_follow_the_last_invoice(self):
        invoices.create(self.con, {**SEPT, "credit_pcts": [15, 40]})
        d = invoices.defaults(self.con, "2026-11-04")
        self.assertEqual((d["period_from"], d["period_to"]), ("2026-10-01", "2026-10-31"))
        self.assertEqual((d["credit_pcts"], d["retainer"], d["last_number"]),
                         ([15, 40], 500, "NW-2026-09"))
        self.assertEqual([c["scope"] for c in d["credits"]], ["Website", "Support"])

    def test_server_routes(self):
        call = self.serve()
        code, _, body = call("POST", "/api/invoices/preview", SEPT)
        self.assertEqual((code, json.loads(body)["net"]), (200, NET))
        code, _, body = call("POST", "/api/invoices", SEPT)
        id_ = json.loads(body)["id"]
        code, hdr, body = call("GET", f"/api/invoices/{id_}/pdf")
        self.assertEqual((code, hdr["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(hdr["Content-Disposition"],
                         'inline; filename="Invoice_NW-2026-09 (draft).pdf"')
        self.assertIsNone(hdr["Content-Security-Policy"])
        self.assertTrue(body.startswith(b"%PDF"))
        self.assertEqual(call("POST", f"/api/invoices/{id_}/issue")[0], 200)
        code, hdr, _ = call("GET", f"/api/invoices/{id_}/pdf?download=1")
        self.assertTrue(hdr["Content-Disposition"].startswith("attachment;"))
        code, _, body = call("DELETE", f"/api/invoices/{id_}")
        self.assertEqual(code, 400)
        code, _, body = call("GET", "/api/invoices")
        self.assertEqual([x["status"] for x in json.loads(body)["invoices"]], ["issued"])
        self.assertEqual(call("GET", "/api/invoices/999/pdf")[0], 404)


class Profile(ProfileDir):
    """Invoice settings: validation, the backup and audit on save, preview."""

    def setUp(self):
        super().setUp()
        # Projects named by the profile but with no time this month.
        names = {r["name"]: r["id"] for r in self.con.execute("SELECT id, name FROM projects")}
        for s in self.prof["sections"]:
            for g in s.get("groups") or [s]:
                for proj, tsk in g["pairs"]:
                    if proj not in names:
                        names[proj] = core.create(self.con, "projects", {"name": proj})["id"]
                    if tsk and not self.con.execute(
                            "SELECT 1 FROM tasks WHERE project_id = ? AND name = ?",
                            (names[proj], tsk)).fetchone():
                        core.create(self.con, "tasks", {"project_id": names[proj], "name": tsk})

    def edited(self, fn):
        p = json.loads(json.dumps(self.prof))
        fn(p)
        return p

    def test_example_profile_round_trips(self):
        p = invoices.clean_profile(self.con, self.prof, self.prof)
        self.assertEqual(p.pop("client").pop("id"), 1)       # Northwind Traders' id
        self.assertEqual(p, {k: v for k, v in self.prof.items() if k != "client"})

    def test_save_backs_up_audits_and_drafts_follow(self):
        p = self.edited(lambda p: p.update(terms="Net 15"))
        self.assertEqual(invoices.save_profile(self.con, p, self.path)["terms"], "Net 15")
        b, = os.listdir(os.path.join(self.dir, "backups"))
        with open(os.path.join(self.dir, "backups", b), encoding="utf-8") as f:
            self.assertEqual(json.load(f), self.prof)
        self.assertEqual(invoices.load_profile(self.path)["terms"], "Net 15")
        audit = self.con.execute("SELECT after FROM audit WHERE entity = 'invoice_profile' "
                                 "ORDER BY id DESC").fetchone()
        self.assertEqual(json.loads(audit[0])["terms"], "Net 15")
        # an unchanged save writes nothing
        invoices.save_profile(self.con, invoices.load_profile(self.path), self.path)
        self.assertEqual(len(os.listdir(os.path.join(self.dir, "backups"))), 1)

    def test_first_save_replaces_the_example(self):
        os.remove(self.path)
        p = self.edited(lambda p: p["issuer"].update(name="Pat Contractor"))
        invoices.save_profile(self.con, p)
        self.assertEqual(invoices.load_profile()["issuer"]["name"], "Pat Contractor")
        self.assertNotIn("example", invoices.profile_for_editor())
        self.assertFalse(os.path.exists(os.path.join(self.dir, "backups")))

    def test_refusals(self):
        def first_group(p):
            return p["sections"][0]["groups"][0]
        cases = {
            "in both": lambda p: first_group(p)["pairs"].append(["Support", ""]),
            "not a project and task": lambda p: first_group(p)["pairs"].append(
                ["Website", "Nope"]),
            "too long to fit": lambda p: p.update(agreement="Consulting Agreement 2026-01 " * 4),
            "cannot be printed": lambda p: p.update(terms="Net 15 − 2"),
            "only use {pct}": lambda p: p["credits"][0].update(label="Less {x}"),
            "no section with key": lambda p: p["credits"][1]["scope"].update(section="gone"),
            "room for 6 lines": lambda p: p["client"].update(address=["x"] * 7),
            "at least one section": lambda p: p.update(sections=[]),
            "pick one": lambda p: p["credits"][0].update(scope={}),
            "not in your clients": lambda p: p["client"].update(name="Contoso"),
        }
        for want, fn in cases.items():
            with self.assertRaises(core.Invalid, msg=want) as e:
                invoices.clean_profile(self.con, self.edited(fn), self.prof)
            self.assertIn(want, str(e.exception))

    def test_client_is_chosen_from_your_clients(self):
        clean = lambda fn: invoices.clean_profile(self.con, self.edited(fn), self.prof)
        self.assertEqual(clean(lambda p: p["client"].update(name="northwind TRADERS"))
                         ["client"]["name"], "Northwind Traders")
        self.assertEqual(clean(lambda p: p["client"].update(name=""))["client"]["name"], "")
        self.assertEqual(clean(lambda p: p["client"].pop("name"))["client"]["name"],
                         "Northwind Traders")       # not sent: kept

    def test_preview_uses_unsaved_settings(self):
        invoices.create(self.con, SEPT)
        p = self.edited(lambda p: p["credits"].pop())       # one credit fewer
        data = invoices.profile_preview(self.con, p)
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(invoices.load_profile(), self.prof)  # nothing saved

    def test_server_routes(self):
        call = self.serve()
        code, _, body = call("GET", "/api/invoice-profile")
        self.assertEqual(json.loads(body), self.prof)
        code, _, body = call("PUT", "/api/invoice-profile", {**self.prof, "rate": 0})
        self.assertEqual(code, 400)
        code, _, body = call("PUT", "/api/invoice-profile", {**self.prof, "terms": "Net 30"})
        self.assertEqual((code, invoices.load_profile(self.path)["terms"]), (200, "Net 30"))
        code, hdr, body = call("POST", "/api/invoice-profile/preview", self.prof)
        self.assertEqual((code, hdr["Content-Type"]), (200, "application/pdf"))
        code, _, body = call("POST", "/api/invoice-profile/preview", {**self.prof, "terms": "x" * 80})
        self.assertEqual(code, 400)
        self.assertIn("too long to fit", json.loads(body)["error"])


class Writer(unittest.TestCase):
    def test_widths_match_helvetica_metrics(self):
        self.assertEqual(pdf.width("Hello", "R", 1000), 722 + 556 + 222 + 222 + 556)
        self.assertEqual(pdf.width("ó", "B", 1000), pdf.width("o", "B", 1000))
        self.assertEqual(pdf.width("—", "R", 1000), 1000)

    def test_wrap_never_overflows(self):
        text = "Nightly orders load — " + "x" * 300 + " end"
        for ln in pdf.wrap(text, 200, "R", 8):
            self.assertLessEqual(pdf.width(ln, "R", 8), 200)

    def test_unprintable_character_is_an_error_not_a_question_mark(self):
        doc = pdf.Document()
        with self.assertRaises(ValueError):
            doc.page().text(0, 10, "− minus")

    def test_text_is_escaped(self):
        doc = pdf.Document()
        doc.page().text(0, 10, "a (b) \\ c")
        body = doc.render()
        stream = body.split(b"stream\n", 1)[1].split(b"\nendstream", 1)[0]
        self.assertIn(b"(a \\(b\\) \\\\ c) Tj", zlib.decompress(stream))


if __name__ == "__main__":
    unittest.main()
