"""The MCP server: JSON-RPC handling, every kind of tool, and the stdio
transport end to end.

    python -m unittest tests.test_mcp -v

Each test uses an in-memory database (or one in a temporary folder) and a
temporary copy of examples/invoice_profile.json, so data/ is never touched.
"""
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from dutemps import core, db, importers, invoices, mcp  # noqa: E402
from dutemps.timeutil import zone  # noqa: E402

SAMPLE_CSV = os.path.join(HERE, "tests", "fixtures", "sample_month.csv")
SEPT = {"number": "NW-2026-09", "issued": "2026-10-05", "period_from": "2026-09-01",
        "period_to": "2026-09-30", "retainer": 500, "retainer_date": "2026-09-30",
        "credit_pcts": [10, 50]}
NET, NET_20_20 = 5605.0, 5685.0     # as in tests/test_invoices.py


def strict(line):
    """A reply parsed as strict JSON parsers do: a bare NaN or Infinity fails."""
    def refuse(name):
        raise AssertionError(f"bare {name} in the reply: {line}")
    return json.loads(line, parse_constant=refuse)


class McpCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        path = os.path.join(self.dir, "invoice_profile.json")
        shutil.copy(invoices.EXAMPLE_PROFILE, path)
        old = invoices.PROFILE_PATH
        invoices.PROFILE_PATH = path
        self.addCleanup(setattr, invoices, "PROFILE_PATH", old)
        self.con = db.connect(":memory:")
        self.addCleanup(self.con.close)
        core.set_settings(self.con, {"timezone": "Etc/GMT+4", "default_rate": "125"})
        self.srv = mcp.Server(self.con, verbose=False)
        self.next_id = 0

    def rpc(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        out = strict(self.srv.handle_line(json.dumps(msg)))
        self.assertEqual(out["jsonrpc"], "2.0")
        self.assertEqual(out["id"], self.next_id)
        return out

    def raw_call(self, tool, args):
        return self.rpc("tools/call", {"name": tool, "arguments": args})["result"]

    def call(self, tool, args=None, /, **kw):
        r = self.raw_call(tool, {**(args or {}), **kw})
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertEqual(r["content"][0]["type"], "text")
        self.assertEqual(json.loads(r["content"][0]["text"]), r["structuredContent"])
        return r["structuredContent"]

    def fails(self, tool, args=None, /, **kw):
        r = self.raw_call(tool, {**(args or {}), **kw})
        self.assertTrue(r["isError"], r)
        return r["content"][0]["text"]


class Protocol(McpCase):
    def test_initialize_echoes_a_known_version_and_offers_the_newest_otherwise(self):
        for asked, got in (("2025-03-26", "2025-03-26"), ("2024-11-05", "2024-11-05"),
                           ("2025-06-18", "2025-06-18"), ("1999-01-01", "2025-06-18"),
                           (None, "2025-06-18")):
            p = {"capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}
            if asked:
                p["protocolVersion"] = asked
            r = self.rpc("initialize", p)["result"]
            self.assertEqual(r["protocolVersion"], got)
        self.assertEqual(r["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(r["serverInfo"]["name"], "dutemps")
        self.assertTrue(r["serverInfo"]["version"])
        self.assertIn("time zone", r["instructions"])
        self.assertIn("issue_invoice and void_invoice", r["instructions"])

    def test_notifications_get_no_reply_and_ping_answers(self):
        for method in ("notifications/initialized", "notifications/cancelled", "nonsense"):
            self.assertIsNone(self.srv.handle_line(json.dumps(
                {"jsonrpc": "2.0", "method": method, "params": {}})))
        self.assertIsNone(self.srv.handle_line("   "))
        self.assertEqual(self.rpc("ping")["result"], {})

    def test_unknown_method_bad_json_and_bad_requests(self):
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)
        for line in ("{not json", '{"jsonrpc": "2.0", "id": 1, "method": "ping"',
                     "\xff\xfe"):
            out = json.loads(self.srv.handle_line(line))
            self.assertEqual(out["error"]["code"], -32700)
            self.assertIsNone(out["id"])
        out = json.loads(self.srv.handle_line('{"jsonrpc": "1.0", "id": 9, "method": "ping"}'))
        self.assertEqual((out["id"], out["error"]["code"]), (9, -32600))
        out = json.loads(self.srv.handle_line('[]'))
        self.assertEqual(out["error"]["code"], -32600)
        self.assertEqual(self.rpc("tools/call", {"name": "no_such_tool"})["error"]["code"],
                         -32602)
        self.assertEqual(self.rpc("tools/call", ["get_status"])["error"]["code"], -32602)

    def test_a_batch_answers_each_request(self):
        self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
        out = json.loads(self.srv.handle_line(json.dumps([
            {"jsonrpc": "2.0", "id": "a", "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": "b", "method": "nope"}])))
        self.assertEqual([r["id"] for r in out], ["a", "b"])
        self.assertEqual(out[1]["error"]["code"], -32601)

    def test_batches_are_refused_in_2025_06_18_and_before_initialize(self):
        batch = json.dumps([{"jsonrpc": "2.0", "id": "a", "method": "ping"}])
        out = json.loads(self.srv.handle_line(batch))
        self.assertEqual((out["id"], out["error"]["code"]), (None, -32600))
        self.assertIn("before initialize", out["error"]["message"])
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        out = json.loads(self.srv.handle_line(batch))
        self.assertEqual((out["id"], out["error"]["code"]), (None, -32600))
        self.assertIn("not supported in protocol 2025-06-18", out["error"]["message"])
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        self.assertEqual(json.loads(self.srv.handle_line(batch)),
                         [{"jsonrpc": "2.0", "id": "a", "result": {}}])

    def test_request_ids_are_strings_or_whole_numbers(self):
        for bad in ("1.5", "-0.25", "true", "null", "[1]", "{}"):
            out = json.loads(self.srv.handle_line(
                '{"jsonrpc": "2.0", "id": %s, "method": "ping"}' % bad))
            self.assertEqual((out["id"], out["error"]["code"]), (None, -32600), bad)
        for good, echo in (('"x"', "x"), ("7", 7), ("0", 0), ("-3", -3)):
            out = json.loads(self.srv.handle_line(
                '{"jsonrpc": "2.0", "id": %s, "method": "ping"}' % good))
            self.assertEqual((out["id"], out["result"]), (echo, {}))

    def test_nan_and_infinity_are_not_json(self):
        call = ('{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": '
                '"create_client", "arguments": {"name": "C", "hourly_rate": %s}}}')
        for lit in ("NaN", "Infinity", "-Infinity", "1e400", "-1e400"):
            out = json.loads(self.srv.handle_line(call % lit))
            self.assertEqual((out["id"], out["error"]["code"]), (None, -32700), lit)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM clients").fetchone()[0], 0)

    def test_a_reply_never_carries_a_bare_nan_or_infinity(self):
        self.srv.methods["ping"] = lambda p: {"x": float("nan")}
        out = strict(self.srv.handle_line('{"jsonrpc": "2.0", "id": 4, "method": "ping"}'))
        self.assertEqual((out["id"], out["error"]["code"]), (4, -32603))

    def test_tools_list_has_schemas_and_annotations(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(len(names), len(set(names)))
        for want in ("get_status", "start_timer", "stop_timer", "log_time", "list_entries",
                     "update_entry", "delete_entries", "restore_entry", "report_summary",
                     "invoice_lines", "preview_invoice", "create_invoice_draft",
                     "update_invoice_draft", "get_invoice", "list_invoices",
                     "duplicate_entry", "continue_entry", "discard_timer",
                     "recent_descriptions", "update_settings", "update_client",
                     "update_project", "update_task", "update_tag", "delete_client",
                     "delete_project", "delete_task", "delete_tag", "report_weekly",
                     "report_series", "get_dashboard", "export_report",
                     "save_invoice_pdf", "import_csv", "backup_database",
                     "delete_invoice_draft", "issue_invoice", "void_invoice",
                     "get_invoice_settings", "update_invoice_settings",
                     "preview_invoice_settings"):
            self.assertIn(want, names)
        for t in tools:
            self.assertTrue(t["description"])
            s = t["inputSchema"]
            self.assertEqual((s["type"], s["additionalProperties"]), ("object", False))
            for k in s.get("required", []):
                self.assertIn(k, s["properties"])
            for k, p in s["properties"].items():
                types = p["type"] if isinstance(p["type"], list) else [p["type"]]
                for ty in types:
                    self.assertIn(ty, ("string", "integer", "number", "boolean", "array",
                                       "object", "null"), f"{t['name']}.{k}")
                # a client that enforces the schema can send the null that clears it
                self.assertEqual("null for none" in p.get("description", ""),
                                 "null" in types, f"{t['name']}.{k}")
                self.assertTrue(p.get("description"), f"{t['name']}.{k}")
            a = t["annotations"]
            for k in ("title", "readOnlyHint", "destructiveHint", "idempotentHint"):
                self.assertIn(k, a)
            if a["readOnlyHint"]:
                self.assertFalse(a["destructiveHint"])
        hints = {t["name"]: t["annotations"] for t in tools}
        self.assertTrue(hints["report_summary"]["readOnlyHint"])
        self.assertTrue(hints["delete_entries"]["destructiveHint"])
        self.assertFalse(hints["log_time"]["readOnlyHint"])
        for name in ("create_invoice_draft", "update_invoice_draft"):
            d = next(t["description"] for t in tools if t["name"] == name)
            self.assertIn("never issues", d)
        for name in ("issue_invoice", "void_invoice", "delete_invoice_draft"):
            s = next(t["inputSchema"] for t in tools if t["name"] == name)
            self.assertIn("confirm", s["required"])
            self.assertTrue(hints[name]["destructiveHint"])

    def test_arguments_are_checked_before_anything_runs(self):
        self.assertIn("unknown argument colour", self.fails("list_tags", colour="red"))
        self.assertIn("missing required argument name", self.fails("create_tag"))
        self.assertIn("must be one of", self.fails("report_summary", period="fortnight"))
        self.assertIn("must be a whole number", self.fails("list_entries", limit="many"))
        self.assertIn("at most 500", self.fails("list_entries", limit=501))
        self.assertIn("YYYY-MM-DD", self.fails("list_entries", {"from": "Sept 1"}))
        self.assertIn("not both", self.fails(
            "report_summary", {"period": "this_week", "from": "2026-09-01"}))
        self.assertEqual(self.call("list_entries", limit="2", billable="true")["count"], 0)


class Entries(McpCase):
    def setUp(self):
        super().setUp()
        self.client = self.call("create_client", name="Client A", hourly_rate=100)["client"]
        self.call("create_project", name="Website", client="client a")
        self.call("create_project", name="Internal", billable=False)
        self.call("create_task", project="website", name="Design")
        self.call("create_tag", name="meeting")

    def test_start_status_stop_timer(self):
        st = self.call("get_status")
        self.assertIsNone(st["running"])
        self.assertEqual(st["timezone"], "Etc/GMT+4")
        r = self.call("start_timer", description="Homepage", project="WEBSITE",
                      tags=["meeting"])
        self.assertTrue(r["started"]["running"])
        self.assertIsNone(r["stopped"])
        self.assertEqual((r["started"]["project"], r["started"]["client"]),
                         ("Website", "Client A"))
        self.assertEqual(r["started"]["tags"], ["meeting"])
        st = self.call("get_status")
        self.assertEqual(st["running"]["id"], r["started"]["id"])
        self.assertEqual(st["today_total"]["entries"], 1)
        again = self.call("start_timer", description="Next thing", task="design")
        self.assertEqual(again["stopped"]["id"], r["started"]["id"])
        self.assertFalse(again["stopped"]["running"])
        self.assertEqual(again["started"]["task"], "Design")
        self.assertEqual(again["started"]["project"], "Website")
        done = self.call("stop_timer")["stopped"]
        self.assertFalse(done["running"])
        self.assertIsNone(self.call("get_status")["running"])
        self.assertIn("no timer is running", self.fails("stop_timer"))

    def test_log_time_by_project_name_then_list_entries_finds_it(self):
        e = self.call("log_time", date="2026-09-01", start="09:00", end="10:30",
                      description="Header layout", project="website", task="Design",
                      tags=["MEETING"])["entry"]
        self.assertEqual((e["start"], e["end"]), ("2026-09-01 09:00:00", "2026-09-01 10:30:00"))
        self.assertEqual((e["seconds"], e["duration"], e["hours"]), (5400, "01:30:00", 1.5))
        self.assertEqual((e["billable"], e["rate"], e["amount"]), (True, 100.0, 150.0))
        found = self.call("list_entries", {"from": "2026-09-01", "to": "2026-09-01"},
                          project="Website")
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["entries"][0]["id"], e["id"])
        self.assertEqual(found["entries"][0]["tags"], ["meeting"])
        self.assertEqual(self.call("list_entries", text="header")["count"], 1)
        self.assertEqual(self.call("list_entries", project="Internal")["count"], 0)
        # stored in UTC; Etc/GMT+4 is four hours behind
        self.assertEqual(core.get_entry(self.con, e["id"])["start_utc"], "2026-09-01T13:00:00Z")

    def test_log_time_shapes_and_mistakes(self):
        first = self.call("log_time", date="2026-09-02", duration="1:30",
                          project="Internal")["entry"]
        self.assertEqual(first["start"], "2026-09-02 09:00:00")       # empty day: 09:00
        self.assertFalse(first["billable"])                           # from the project
        nxt = self.call("log_time", date="2026-09-02", duration="45m")["entry"]
        self.assertEqual((nxt["start"], nxt["end"]),
                         ("2026-09-02 10:30:00", "2026-09-02 11:15:00"))
        e = self.call("log_time", date="2026-09-02", end="3:00pm", duration=2)["entry"]
        self.assertEqual(e["start"], "2026-09-02 13:00:00")
        late = self.call("log_time", date="2026-09-02", start="23:00",
                         end="2026-09-03 01:15")["entry"]
        self.assertEqual(late["seconds"], 8100)
        ov = self.call("log_time", date="2026-09-02", start="10:00", end="10:45")
        self.assertEqual(len(ov["overlaps"]), 2)
        self.assertIn("overlaps 2 other entries", ov["warning"])
        self.assertIn("not after start", self.fails("log_time", date="2026-09-02",
                                                    start="10:00", end="09:00"))
        self.assertIn("'90m'", self.fails("log_time", date="2026-09-02", duration="90"))
        self.assertIn("HH:MM", self.fails("log_time", start="25:00", end="26:00"))
        self.assertIn("not all three", self.fails("log_time", start="9:00", end="10:00",
                                                  duration="1h"))
        self.assertIn("just a duration", self.fails("log_time", start="9:00"))

    def test_ambiguous_and_unknown_names_list_candidates(self):
        self.call("create_client", name="Client B")
        self.call("create_project", name="Website", client="Client B")
        msg = self.fails("log_time", date="2026-09-01", duration="1h", project="website")
        self.assertIn("2 projects are named 'website'", msg)
        self.assertIn("client Client A", msg)
        self.assertIn("client Client B", msg)
        ids = [p["id"] for p in self.call("list_projects")["projects"]
               if p["name"] == "Website"]
        self.assertEqual(len(ids), 2)
        for pid in ids:
            self.assertIn(f"id {pid}", msg)
        # an id, or the client, settles it
        e = self.call("log_time", date="2026-09-01", duration="1h", project=ids[1])["entry"]
        self.assertEqual(e["project_id"], ids[1])
        self.assertEqual(self.call("list_entries", project="website",
                                   client="client b")["count"], 1)
        msg = self.fails("log_time", date="2026-09-01", duration="1h", project="Webiste")
        self.assertIn("no project named 'Webiste'", msg)
        self.assertIn("Did you mean", msg)
        self.assertIn("no tag named 'urgent'", self.fails("start_timer", tags=["urgent"]))
        self.assertIn("no project with id 999", self.fails("list_entries", project=999))

    def test_update_entry(self):
        e = self.call("log_time", date="2026-09-01", start="09:00", end="10:00",
                      project="Website", task="Design", description="x")["entry"]
        u = self.call("update_entry", id=e["id"], description="Header", duration="1:15",
                      tags=["meeting"])["entry"]
        self.assertEqual((u["description"], u["seconds"], u["tags"]),
                         ("Header", 4500, ["meeting"]))
        u = self.call("update_entry", id=e["id"], date="2026-09-03")["entry"]
        self.assertEqual((u["start"], u["seconds"]), ("2026-09-03 09:00:00", 4500))
        u = self.call("update_entry", id=e["id"], project="Internal")["entry"]
        self.assertEqual((u["project"], u["task"]), ("Internal", None))
        u = self.call("update_entry", id=e["id"], project="", billable=True)["entry"]
        self.assertEqual((u["project"], u["billable"]), (None, True))
        self.assertIn("nothing to change", self.fails("update_entry", id=e["id"]))
        self.assertIn("entry 999 not found", self.fails("update_entry", id=999, billable=False))

    def test_report_summary_totals_match_entries(self):
        self.call("log_time", date="2026-09-01", start="09:00", end="10:30", project="Website")
        self.call("log_time", date="2026-09-02", duration="2:15", project="Website",
                  task="Design")
        self.call("log_time", date="2026-09-03", duration="0:40", project="Internal")
        self.call("log_time", date="2026-09-03", duration="1h", description="no project")
        self.call("log_time", date="2026-10-01", duration="3h", project="Website")  # outside
        rng = {"from": "2026-09-01", "to": "2026-09-30"}
        es = self.call("list_entries", rng, limit=500)["entries"]
        r = self.call("report_summary", rng, group2="task")
        self.assertEqual(r["total"]["seconds"], sum(e["seconds"] for e in es))
        self.assertEqual(r["total"]["seconds"], 5400 + 8100 + 2400 + 3600)
        self.assertEqual(r["total"]["entries"], 4)
        self.assertEqual(r["total"]["amount"], round(sum(e["amount"] for e in es), 2))
        self.assertEqual(sum(g["seconds"] for g in r["groups"]), r["total"]["seconds"])
        web = next(g for g in r["groups"] if g["name"] == "Website")
        self.assertEqual((web["seconds"], web["hours"], web["amount"]), (13500, 3.75, 375.0))
        self.assertEqual({c["name"] for c in web["children"]}, {"Design", "(no task)"})
        b = self.call("report_summary", rng, group1="billable")
        self.assertEqual({g["name"]: g["seconds"] for g in b["groups"]},
                         {"Billable": 13500 + 3600, "Non-billable": 2400})
        self.assertIn("period", self.fails("report_summary"))
        lines = self.call("invoice_lines", rng, client="Client A")
        self.assertEqual((lines["count"], lines["hours"]), (2, 3.75))

    def test_filters_take_several_of_a_kind_as_the_app_does(self):
        self.call("log_time", date="2026-09-01", duration="1h", project="website",
                  task="design")
        self.call("log_time", date="2026-09-01", duration="2h", project="internal",
                  tags=["meeting"])
        self.call("log_time", date="2026-09-01", duration="4h")
        rng = {"from": "2026-09-01", "to": "2026-09-30"}
        both = self.call("report_summary", rng, project=["website", "Internal"])
        self.assertEqual(both["total"]["hours"], 3.0)
        self.assertEqual(self.call("report_summary", rng, project="internal")
                         ["total"]["hours"], 2.0)
        # several of one kind: any; different kinds: all
        self.assertEqual(self.call("list_entries", rng, project=["website", "internal"],
                                   tag=["meeting"])["count"], 1)
        self.assertEqual(self.call("list_entries", rng, project=["website", "internal"],
                                   task=["design"])["count"], 1)
        self.assertIn("in those projects", self.fails(
            "list_entries", project=["website", "internal"], task="nothing"))
        self.assertIn("for that client", self.fails(
            "list_entries", client=["client a"], project="internal"))
        x = self.call("export_report", rng, kind="summary", path=self.dir,
                      project=["website", "internal"])
        with open(x["path"], encoding="utf-8-sig") as f:
            text = f.read()
        self.assertIn("Website", text)
        self.assertIn("Internal", text)

    def test_delete_entries_then_restore(self):
        a = self.call("log_time", date="2026-09-01", duration="1h", project="Website",
                      tags=["meeting"], description="first")["entry"]
        b = self.call("log_time", date="2026-09-01", duration="2h")["entry"]
        r = self.call("delete_entries", ids=[a["id"], b["id"], 999])
        self.assertEqual([d["id"] for d in r["deleted"]], [a["id"], b["id"]])
        self.assertEqual(r["not_found"], [999])
        self.assertEqual(self.call("list_entries")["count"], 0)
        gone = self.call("list_deleted_entries")["deleted"]
        self.assertEqual({d["description"] for d in gone}, {"first", ""})
        audit = next(d["audit_id"] for d in r["deleted"] if d["id"] == a["id"])
        back = self.call("restore_entry", audit_id=audit)["entry"]
        self.assertEqual((back["description"], back["tags"], back["seconds"]),
                         ("first", ["meeting"], 3600))
        self.assertEqual(self.call("list_entries")["count"], 1)
        self.assertEqual(len(self.call("list_deleted_entries")["deleted"]), 1)
        self.assertIn("already restored", self.fails("restore_entry", audit_id=audit))

    def test_creates_refuse_duplicates_and_bad_values(self):
        self.assertIn("already exists", self.fails("create_tag", name="Meeting"))
        self.assertIn("#RRGGBB", self.fails("create_project", name="X", color="red"))
        self.assertIn("no client named", self.fails("create_project", name="X",
                                                    client="Nobody"))
        p = self.call("list_projects", client="Client A")["projects"]
        self.assertEqual([x["name"] for x in p], ["Website"])
        self.assertEqual((p[0]["effective_rate"], p[0]["tasks"][0]["name"]), (100, "Design"))
        self.assertEqual(self.call("list_clients")["clients"][0]["projects"], 1)

    def test_infinite_and_nan_numbers_are_refused_before_any_write(self):
        for bad in ("Infinity", "-inf", "NaN", "nan", "1e400"):
            self.assertIn("must be a finite number", self.fails(
                "create_client", name="Client Z", hourly_rate=bad), bad)
            self.assertIn("must be a finite number", self.fails(
                "create_project", name="Project Z", estimate_hours=bad), bad)
        self.assertIn("too large", self.fails("create_task", project="Website",
                                              name="T", hourly_rate=10 ** 30))
        names = {c["name"] for c in self.call("list_clients", include_archived=True)["clients"]}
        self.assertEqual(names, {"Client A"})
        self.assertEqual(self.call("list_projects", include_archived=True)["count"], 2)

    def test_a_non_finite_rate_in_the_database_is_an_error_not_bad_json(self):
        self.con.execute("UPDATE clients SET hourly_rate = 9e999 WHERE id = ?",
                         (self.client["id"],))
        for tool in ("list_clients", "list_projects"):
            r = self.raw_call(tool, {})     # rpc() parses strictly: no bare Infinity
            self.assertTrue(r["isError"], tool)
            self.assertIn("not finite", r["content"][0]["text"])

    def test_a_backdated_start_stops_the_running_timer_at_that_start(self):
        now = mcp._now(self.con)

        def ago(hours):
            return (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")

        a = self.call("start_timer", description="A", start=ago(2))["started"]
        r = self.call("start_timer", description="B", start=ago(1))
        b = r["started"]
        self.assertEqual(r["stopped"]["id"], a["id"])
        self.assertEqual(r["stopped"]["end"], b["start"])       # no overlap, no gap
        self.assertEqual(r["stopped"]["seconds"], 3600)
        self.assertNotIn("overlaps", r)
        # a start before the running timer's own start is refused, changing nothing
        msg = self.fails("start_timer", description="C", start=ago(3))
        self.assertIn("started at or after that time", msg)
        self.assertEqual(self.call("get_status")["running"]["id"], b["id"])
        self.assertEqual(self.call("list_entries")["count"], 2)
        self.assertEqual(core.get_entry(self.con, a["id"])["seconds"], 3600)

    def test_update_entry_builds_on_an_edit_made_meanwhile(self):
        e = self.call("log_time", date="2026-09-01", start="09:00", end="10:00")["entry"]
        real = core.update_entry_with

        def app_edits_first(con, id_, derive):
            # the app stretches the entry to two hours just before the tool's update
            real(con, id_, lambda cur: {"end_utc": "2026-09-01T15:00:00Z"})
            return real(con, id_, derive)

        with mock.patch.object(core, "update_entry_with", app_edits_first):
            u = self.call("update_entry", id=e["id"], date="2026-09-03")["entry"]
        self.assertEqual((u["start"], u["end"], u["seconds"]),
                         ("2026-09-03 09:00:00", "2026-09-03 11:00:00", 7200))

    def test_delete_entries_reports_what_its_own_transaction_deleted(self):
        a = self.call("log_time", date="2026-09-01", duration="1h", project="Website",
                      description="first")["entry"]
        made, real = {}, core.delete_entries

        def app_adds_first(con, ids):
            # the app saves a new entry, taking the next id, just before the delete
            made["e"] = core.create_entry(con, {
                "description": "made in the app", "start_utc": "2026-09-02T13:00:00Z",
                "end_utc": "2026-09-02T14:30:00Z"})
            return real(con, ids)

        with mock.patch.object(core, "delete_entries", app_adds_first):
            r = self.call("delete_entries", ids=[a["id"], a["id"] + 1])
        self.assertEqual(made["e"]["id"], a["id"] + 1)
        self.assertEqual(r["not_found"], [])
        first, second = r["deleted"]
        self.assertEqual((first["description"], first["project"], first["seconds"]),
                         ("first", "Website", 3600))
        self.assertEqual((second["id"], second["description"], second["seconds"],
                          second["start"]),
                         (a["id"] + 1, "made in the app", 5400, "2026-09-02 09:00:00"))
        back = self.call("restore_entry", audit_id=second["audit_id"])["entry"]
        self.assertEqual(back["description"], "made in the app")
        # the running timer too: its length runs to the moment it was deleted
        t = self.call("start_timer", description="running")["started"]
        gone = self.call("delete_entries", ids=[t["id"]])["deleted"][0]
        self.assertEqual((gone["description"], gone["end"]), ("running", None))
        self.assertLess(gone["seconds"], 60)

    def test_text_arguments_take_strings_and_names_or_ids_also_take_numbers(self):
        self.assertIn("name must be a string (in quotes), not a number",
                      self.fails("create_tag", name=123))
        self.assertIn("description must be a string", self.fails(
            "log_time", duration="1h", description=5))
        self.assertIn("start must be a string", self.fails("log_time", start=9, end="10:00"))
        self.assertIn("not an object", self.fails("create_client", name={"x": 1}))
        self.assertIn("whole-number id", self.fails("log_time", duration="1h", project=1.5))
        self.assertEqual(self.call("list_tags")["count"], 1)
        web = next(p for p in self.call("list_projects")["projects"]
                   if p["name"] == "Website")
        pid, task = web["id"], float(web["tasks"][0]["id"])      # 2.0 is id 2
        tag = self.call("list_tags")["tags"][0]["id"]
        e = self.call("log_time", date="2026-09-01", duration=1.5, project=pid,
                      tags=[tag], task=task)["entry"]
        self.assertEqual((e["project_id"], e["task"], e["tags"], e["seconds"]),
                         (pid, "Design", ["meeting"], 5400))


    def test_duplicate_continue_discard_and_recent_descriptions(self):
        e = self.call("log_time", date="2026-09-01", start="09:00", end="10:00",
                      description="Homepage", project="website", tags=["meeting"])["entry"]
        d = self.call("duplicate_entry", id=e["id"])
        self.assertNotEqual(d["entry"]["id"], e["id"])
        self.assertEqual((d["entry"]["start"], d["entry"]["tags"]), (e["start"], ["meeting"]))
        self.assertEqual(d["overlaps"][0]["id"], e["id"])
        c = self.call("continue_entry", id=e["id"])["started"]
        self.assertTrue(c["running"])
        self.assertEqual((c["description"], c["project"], c["tags"]),
                         ("Homepage", "Website", ["meeting"]))
        gone = self.call("discard_timer")
        self.assertEqual(gone["discarded"], [c["id"]])
        self.assertIsNone(self.call("get_status")["running"])
        self.assertIn("no timer is running", self.fails("discard_timer"))
        self.assertEqual(self.call("restore_entry", audit_id=gone["audit_ids"][0])
                         ["entry"]["id"], c["id"])
        r = self.call("recent_descriptions", text="home")["suggestions"]
        self.assertEqual([(x["description"], x["project"]) for x in r],
                         [("Homepage", "Website")])
        self.assertIn("not found", self.fails("duplicate_entry", id=999))

    def test_update_settings_changes_only_what_is_given(self):
        s = self.call("update_settings", currency="EUR", week_start=7)["settings"]
        self.assertEqual((s["currency"], str(s["week_start"]), s["timezone"]),
                         ("EUR", "7", "Etc/GMT+4"))
        self.assertIn("nothing to change", self.fails("update_settings"))
        self.assertIn("must be one of", self.fails("update_settings", time_format="13"))
        self.fails("update_settings", timezone="Mars/Olympus_Mons")
        self.assertEqual(self.call("get_status")["timezone"], "Etc/GMT+4")

    def test_update_and_delete_clients_projects_tasks_and_tags(self):
        self.call("log_time", date="2026-09-01", duration="1h", project="website",
                  task="design", tags=["meeting"])
        c = self.call("update_client", client="client a", name="Client B",
                      hourly_rate=None)["client"]
        self.assertEqual((c["name"], c["hourly_rate"]), ("Client B", None))
        p = self.call("update_project", project="website", client="",
                      estimate_hours=10)["project"]
        self.assertEqual((p["client"], p["estimate_hours"]), (None, 10))
        p = self.call("update_project", project="website", client="client b")["project"]
        self.assertEqual(p["client"], "Client B")
        t = self.call("update_task", task="design", name="Design work")["task"]
        self.assertEqual((t["name"], t["project"]), ("Design work", "Website"))
        self.assertEqual(self.call("update_tag", tag="meeting", name="call")["tag"]["name"],
                         "call")
        self.assertIn("nothing to change", self.fails("update_tag", tag="call"))
        self.assertIn("unknown argument colour", self.fails(
            "update_project", project="website", colour="#000000"))
        # in use: kept, archived (a task: done), so past entries keep their labels
        for tool, kw in (("delete_task", {"task": "design work"}),
                         ("delete_project", {"project": "website"}),
                         ("delete_tag", {"tag": "call"}),
                         ("delete_client", {"client": "client b"})):
            self.assertEqual(self.call(tool, **kw)["archived"], True, tool)
        e = self.call("list_entries")["entries"][0]
        self.assertEqual((e["project"], e["task"], e["tags"]), ("Website", "Design work",
                                                                ["call"]))
        # unused: gone
        self.assertTrue(self.call("delete_project", project="internal")["deleted"])
        self.assertIn("no project", self.fails("delete_project", project="internal"))

    def test_weekly_series_and_dashboard(self):
        self.call("log_time", date="2026-09-01", start="09:00", end="10:30",
                  project="website", task="design")
        self.call("log_time", date="2026-09-03", duration="2h", project="internal")
        w = self.call("report_weekly", week="2026-09-02")
        self.assertEqual((w["days"][0], w["days"][6]), ("2026-08-31", "2026-09-06"))
        rows = {(r["project"], r["task"]): r["day_hours"] for r in w["rows"]}
        self.assertEqual(rows[("Website", "Design")], [0, 1.5, 0, 0, 0, 0, 0])
        self.assertEqual(rows[("Internal", None)], [0, 0, 0, 2.0, 0, 0, 0])
        self.assertEqual((w["total"]["hours"], w["day_totals"][1]), (3.5, 1.5))
        self.assertEqual(self.call("report_weekly", week="2026-09-02",
                                   project="internal")["total"]["hours"], 2.0)
        s = self.call("report_series", {"from": "2026-09-01", "to": "2026-09-03"})
        self.assertEqual([b["total_hours"] for b in s["buckets"]], [1.5, 0, 2.0])
        self.assertEqual(s["buckets"][0]["hours"], {"Website": 1.5})
        wk = self.call("report_series", {"from": "2026-09-01", "to": "2026-09-03"}, by="week")
        self.assertEqual([(b["start"], b["total_hours"]) for b in wk["buckets"]],
                         [("2026-08-31", 3.5)])
        self.assertIn("give a period", self.fails("report_series"))
        self.call("log_time", date="today", duration="1h", project="internal")
        d = self.call("get_dashboard")
        self.assertEqual((d["today"]["hours"], d["today"]["entries"]), (1.0, 1))
        self.assertEqual(d["week"]["projects"][0]["name"], "Internal")
        self.assertEqual(len(d["recent"]), 3)


class Files(McpCase):
    """Exports, PDFs, imports and backups: written where asked, never over a file."""

    def count(self, con=None):
        return (con or self.con).execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    def test_export_report_writes_csv_and_never_overwrites(self):
        self.call("log_time", date="2026-09-01", start="09:00", end="10:30",
                  description="Homepage")
        sept = {"from": "2026-09-01", "to": "2026-09-30"}
        x = self.call("export_report", sept, path=self.dir)
        self.assertEqual(os.path.basename(x["path"]),
                         "DuTemps_detailed_2026-09-01_2026-09-30.csv")
        with open(x["path"], encoding="utf-8-sig") as f:
            self.assertIn("Homepage", f.read())
        self.assertEqual(x["rows"], 1)
        self.assertIn("already exists", self.fails("export_report", sept, path=x["path"]))
        w = self.call("export_report", kind="weekly", week="2026-09-03", path=self.dir)
        self.assertTrue(w["path"].endswith("DuTemps_weekly_week-of-2026-08-31.csv"))
        s = self.call("export_report", sept, kind="summary", group1="day",
                      path=os.path.join(self.dir, "out", "s.csv"))
        self.assertTrue(os.path.isfile(s["path"]))
        self.assertIn("in memory; give a path", self.fails("export_report", sept))

    def test_import_csv_dry_run_then_import_once(self):
        dry = self.call("import_csv", path=SAMPLE_CSV, dry_run=True)
        self.assertTrue(dry["dry_run"])
        self.assertGreater(dry["added"], 0)
        self.assertEqual(self.count(), 0)
        done = self.call("import_csv", path=SAMPLE_CSV)
        self.assertEqual((done["added"], self.count()), (dry["added"], dry["added"]))
        self.assertEqual(self.call("import_csv", path=SAMPLE_CSV)["added"], 0)
        self.assertIn("no file", self.fails("import_csv",
                                            path=os.path.join(self.dir, "none.csv")))

    def test_backup_database_and_default_folders_beside_the_database(self):
        self.call("log_time", date="2026-09-01", duration="1h")
        b = self.call("backup_database", path=os.path.join(self.dir, "copy.db"))
        with contextlib.closing(sqlite3.connect(b["path"])) as copy:
            self.assertEqual(self.count(copy), 1)
        self.assertIn("in memory; give a path", self.fails("backup_database"))
        con = db.connect(os.path.join(self.dir, "file.db"))
        self.addCleanup(con.close)
        self.srv = mcp.Server(con, verbose=False)
        b = self.call("backup_database", label="before import!")
        self.assertTrue(os.path.samefile(os.path.dirname(b["path"]),
                                         os.path.join(self.dir, "backups")))
        self.assertTrue(b["path"].endswith("-before-import.db"))
        x = self.call("export_report", period="this_month")
        self.assertTrue(os.path.samefile(os.path.dirname(x["path"]),
                                         os.path.join(self.dir, "exports")))


    def test_a_file_that_appears_after_the_check_is_never_overwritten(self):
        self.call("log_time", date="2026-09-01", duration="1h")
        dest = os.path.join(self.dir, "late.db")

        def taken_meanwhile(con, path, name):
            with open(dest, "wb") as f:
                f.write(b"someone else's")
            return dest
        with mock.patch.object(mcp, "_out_path", taken_meanwhile):
            self.assertIn("already exists", self.fails("backup_database", path=self.dir))
            self.assertIn("already exists", self.fails(
                "export_report", period="this_month", path=self.dir))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"someone else's")

    def test_a_failed_backup_closes_and_removes_its_file(self):
        dest, real = os.path.join(self.dir, "failed.db"), db.backup

        class Broken:
            def backup(self, out):
                raise sqlite3.OperationalError("disk full")
        with mock.patch.object(mcp, "_out_path", lambda con, path, name: dest), \
                mock.patch.object(mcp.db, "backup", lambda con, d: real(Broken(), d)):
            self.fails("backup_database", path=self.dir)
        self.assertFalse(os.path.exists(dest))     # Windows can't remove an open file


class Invoices(McpCase):
    def setUp(self):
        super().setUp()
        importers.import_csv(self.con, SAMPLE_CSV)

    def test_preview_create_and_update_a_draft_with_the_example_profile(self):
        p = self.call("preview_invoice", SEPT)
        self.assertFalse(p["saved"])
        self.assertEqual(p["invoice"]["net"], NET)
        self.assertEqual(self.call("list_invoices")["count"], 0)          # wrote nothing
        d = self.call("create_invoice_draft", SEPT)
        self.assertEqual((d["status"], d["editable"]), ("draft", True))
        self.assertEqual((d["invoice"]["hours"], d["invoice"]["net"]), (71.25, NET))
        u = self.call("update_invoice_draft", invoice="nw-2026-09", credit_pcts=[20, 20],
                      note="checked")
        self.assertEqual((u["id"], u["invoice"]["net"], u["note"]), (d["id"], NET_20_20,
                                                                      "checked"))
        g = self.call("get_invoice", invoice=str(d["id"]), include_lines=True)
        self.assertEqual(g["number"], "NW-2026-09")
        self.assertTrue(g["sections"][0]["groups"][0]["lines"])
        listed = self.call("list_invoices", status="draft")
        self.assertEqual([x["net"] for x in listed["invoices"]], [NET_20_20])
        self.assertEqual(listed["invoice_profile"], "saved")
        self.assertIn("already used", self.fails("create_invoice_draft", SEPT))
        self.assertIn("no invoice 'NW-1'", self.fails("get_invoice", invoice="NW-1"))
        # once issued, it is frozen
        invoices.issue(self.con, d["id"])
        msg = self.fails("update_invoice_draft", invoice="NW-2026-09", note="later")
        self.assertIn("only drafts can be edited", msg)
        self.assertIn("void_invoice", self.call(
            "get_invoice", invoice="NW-2026-09")["frozen"])

    def test_draft_defaults_to_today_and_shows_build_problems(self):
        d = self.call("create_invoice_draft", number="T-1", period_from="2026-09-01",
                      period_to="2026-09-30")
        self.assertEqual(d["issued"], mcp._today(self.con).isoformat())
        self.assertIn("needs the date it was paid", self.fails(
            "create_invoice_draft", number="T-2", period_from="2026-09-01",
            period_to="2026-09-30", retainer=100))

    def test_an_infinite_retainer_or_credit_is_refused_and_saves_no_draft(self):
        for bad in ({"retainer": "Infinity"}, {"retainer": "NaN"},
                    {"credit_pcts": [10, "inf"]}):
            self.assertIn("must be a finite number",
                          self.fails("create_invoice_draft", {**SEPT, **bad}))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 0)
        d = self.call("create_invoice_draft", SEPT)
        self.assertIn("must be a finite number", self.fails(
            "update_invoice_draft", invoice=d["id"], retainer="-Infinity"))
        self.assertEqual(self.call("get_invoice", invoice=d["id"])["retainer"], 500)

    def test_get_invoice_builds_its_totals_and_lines_once_in_one_read(self):
        d = self.call("create_invoice_draft", SEPT)
        seen, real = [], invoices._build_row

        def counting(con, *args):
            seen.append(con.in_transaction)
            return real(con, *args)

        with mock.patch.object(invoices, "_build_row", counting):
            g = self.call("get_invoice", invoice=d["id"], include_lines=True)
        self.assertEqual(seen, [True])          # one build, inside a read transaction
        self.assertFalse(self.con.in_transaction)
        lines = [ln for s in g["sections"] for grp in s["groups"] for ln in grp["lines"]]
        self.assertEqual(sum(ln["hours"] for ln in lines), g["invoice"]["hours"])
        self.assertEqual(g["invoice"]["net"], NET)


    def test_issue_void_and_delete_need_the_number_again(self):
        d = self.call("create_invoice_draft", SEPT)
        self.assertIn("confirm='NW-2026-09'", self.fails(
            "issue_invoice", invoice=d["id"], confirm="yes"))
        self.assertEqual(self.call("get_invoice", invoice=d["id"])["status"], "draft")
        pdf = self.call("save_invoice_pdf", invoice="NW-2026-09", path=self.dir)
        self.assertEqual(pdf["status"], "draft")
        with open(pdf["path"], "rb") as f:
            self.assertEqual(f.read(5), b"%PDF-")
        i = self.call("issue_invoice", invoice="nw-2026-09", confirm=" NW-2026-09 ")
        self.assertEqual((i["status"], i["editable"], i["invoice"]["net"]),
                         ("issued", False, NET))
        self.fails("delete_invoice_draft", invoice=d["id"], confirm="NW-2026-09")
        self.assertIn("already issued", self.fails(
            "issue_invoice", invoice=d["id"], confirm="NW-2026-09"))
        v = self.call("void_invoice", invoice=d["id"], confirm="NW-2026-09",
                      reason="wrong period")
        self.assertEqual(v["status"], "void")
        t = self.call("create_invoice_draft", {**SEPT, "number": "NW-2026-09b"})
        self.assertEqual(self.call("delete_invoice_draft", invoice=t["id"],
                                   confirm="NW-2026-09b")["deleted"], "NW-2026-09b")
        self.assertIn("no invoice", self.fails("get_invoice", invoice="NW-2026-09b"))

    def test_issue_checks_and_builds_inside_its_write_transaction(self):
        d = self.call("create_invoice_draft", SEPT)
        seen, real = [], invoices.render

        def render(*a, **k):
            seen.append(self.con.in_transaction)
            return real(*a, **k)
        with mock.patch.object(invoices, "render", render):
            self.call("issue_invoice", invoice=d["id"], confirm="NW-2026-09")
        self.assertEqual(seen, [True])

    def test_the_confirmed_number_is_checked_where_the_invoice_changes(self):
        d = self.call("create_invoice_draft", SEPT)
        real = mcp._find_invoice

        def renamed_meanwhile(con, v):
            con.execute("UPDATE invoices SET number = 'NW-2026-09' WHERE id = ?", (v,))
            r = dict(real(con, v))
            con.execute("UPDATE invoices SET number = 'NW-2026-09-B' WHERE id = ?",
                        (r["id"],))
            return r
        with mock.patch.object(mcp, "_find_invoice", renamed_meanwhile):
            for tool in ("issue_invoice", "delete_invoice_draft"):
                self.assertIn("is now NW-2026-09-B, not NW-2026-09", self.fails(
                    tool, invoice=d["id"], confirm="NW-2026-09"))
        self.assertEqual(self.call("get_invoice", invoice=d["id"])["status"], "draft")
        self.call("issue_invoice", invoice=d["id"], confirm="NW-2026-09-B")
        with self.assertRaises(core.Invalid):
            invoices.void(self.con, d["id"], "", number="NW-2026-09")
        self.assertEqual(self.call("get_invoice", invoice=d["id"])["status"], "issued")

    def test_an_invoice_number_never_makes_folders_in_a_file_name(self):
        d = self.call("create_invoice_draft", {**SEPT, "number": "x/../../NW:09"})
        out = os.path.join(self.dir, "out")
        os.makedirs(out)
        pdf = self.call("save_invoice_pdf", invoice=d["id"], path=out)
        self.assertTrue(os.path.samefile(os.path.dirname(pdf["path"]), out))
        self.assertEqual(os.path.basename(pdf["path"]), "Invoice_x-..-..-NW-09 (draft).pdf")
        self.assertEqual(pdf["number"], "x/../../NW:09")

    def test_a_settings_change_keeps_an_edit_saved_meanwhile(self):
        self.call("create_project", name="Training", client="Northwind Traders")
        self.call("create_task", project="Training", name="Workshops")
        real, in_tx = invoices.save_profile, []

        def app_saved_first(con, b, path=None):
            # the app saves a new email after the tool is called, before it saves
            p = invoices.load_profile()
            p["issuer"]["email"] = "new@example.com"
            with open(invoices.PROFILE_PATH, "w", encoding="utf-8") as f:
                json.dump(p, f)

            def patch(cur):
                in_tx.append(con.in_transaction)
                return b(cur)
            return real(con, patch, path)
        with mock.patch.object(invoices, "save_profile", app_saved_first):
            u = self.call("update_invoice_settings", max_hours=0)
        self.assertEqual((u["changed"], in_tx), (["max_hours"], [True]))
        p = invoices.load_profile()
        self.assertEqual((p["max_hours"], p["issuer"]["email"]), (0, "new@example.com"))

    def test_invoice_settings_read_preview_and_change(self):
        # every task in the example's sections must exist to save them
        self.call("create_project", name="Training", client="Northwind Traders")
        self.call("create_task", project="Training", name="Workshops")
        g = self.call("get_invoice_settings")["settings"]
        self.assertEqual((g["max_hours"], g["client"]["name"]), (40, "Northwind Traders"))
        pv = self.call("preview_invoice_settings", max_hours=0, path=self.dir)
        self.assertFalse(pv["saved"])
        with open(pv["path"], "rb") as f:
            self.assertEqual(f.read(5), b"%PDF-")
        self.assertEqual(self.call("get_invoice_settings")["settings"]["max_hours"], 40)
        u = self.call("update_invoice_settings", max_hours=0, issuer={"phone": "555-0199"})
        self.assertEqual(u["changed"], ["issuer", "max_hours"])
        self.assertEqual(u["settings"]["issuer"], {**g["issuer"], "phone": "555-0199"})
        self.assertEqual(invoices.load_profile()["max_hours"], 0)
        self.assertTrue(os.listdir(os.path.join(self.dir, "backups")))
        self.assertIn("must be an object", self.fails("update_invoice_settings",
                                                       client="Acme"))
        self.assertIn("nothing to change", self.fails("update_invoice_settings"))
        self.assertIn("at least 0", self.fails("update_invoice_settings", rate=-1))


class DaylightSaving(McpCase):
    """America/New_York: 2026-03-08 skips 02:00-03:00; 2026-11-01 repeats 01:00-02:00."""

    def setUp(self):
        super().setUp()
        core.set_settings(self.con, {"timezone": "America/New_York"})
        self.ny = zone("America/New_York")

    @staticmethod
    def at(now):
        """Pretend it is `now`, an aware time in the Settings timezone."""
        return mock.patch.multiple(mcp, _now=lambda con: now,
                                   _utcnow=lambda: now.astimezone(timezone.utc))

    def utc(self, entry):
        e = core.get_entry(self.con, entry["id"])
        return e["start_utc"], e["end_utc"]

    def test_a_skipped_local_time_is_refused(self):
        msg = self.fails("log_time", date="2026-03-08", start="02:30", end="04:00")
        self.assertIn("start 2026-03-08 02:30 doesn't exist in America/New_York", msg)
        self.assertIn("02:30", self.fails("log_time", start="2026-03-08 01:00",
                                          end="2026-03-08 2:30am"))
        self.assertIn("doesn't exist", self.fails("log_time", date="2026-03-08",
                                                  start="02:30-05:00", end="04:00"))
        self.assertEqual(self.call("list_entries")["count"], 0)
        e = self.call("log_time", date="2026-03-08", start="01:30", end="03:30")["entry"]
        self.assertEqual(e["seconds"], 3600)        # 01:30 EST to 03:30 EDT
        msg = self.fails("update_entry", id=e["id"], start="02:15")
        self.assertIn("doesn't exist", msg)
        self.assertEqual(self.utc(e), ("2026-03-08T06:30:00Z", "2026-03-08T07:30:00Z"))

    def test_a_repeated_local_time_needs_its_utc_offset(self):
        msg = self.fails("log_time", date="2026-11-01", start="01:30", end="02:30")
        self.assertIn("happens twice in America/New_York", msg)
        self.assertIn("'01:30-04:00' (the earlier) or '01:30-05:00' (the later)", msg)
        self.assertEqual(self.call("list_entries")["count"], 0)
        for start, utc, secs in (("01:30-04:00", "2026-11-01T05:30:00Z", 7200),
                                 ("2026-11-01 01:30 -0500", "2026-11-01T06:30:00Z", 3600)):
            e = self.call("log_time", date="2026-11-01", start=start, end="02:30")["entry"]
            self.assertEqual(self.utc(e), (utc, "2026-11-01T07:30:00Z"))
            self.assertEqual((e["start"], e["seconds"]), ("2026-11-01 01:30:00", secs))
        msg = self.fails("log_time", date="2026-11-01", start="01:30-07:00", end="02:30")
        self.assertIn("not at UTC-07:00", msg)
        self.assertIn("UTC-04:00 or UTC-05:00", msg)
        self.assertIn("bad UTC offset", self.fails("log_time", date="2026-11-01",
                                                   start="01:30-25:00", end="02:30"))
        # an offset that agrees with an ordinary time is fine; one that doesn't isn't
        e = self.call("log_time", start="2026-07-01 09:00-04:00", end="10:00")["entry"]
        self.assertEqual(self.utc(e), ("2026-07-01T13:00:00Z", "2026-07-01T14:00:00Z"))
        self.assertIn("not at UTC-05:00", self.fails(
            "log_time", start="2026-07-01 09:00-05:00", end="10:00"))
        # the timer's start, in last year's repeated hour
        self.assertIn("happens twice", self.fails("start_timer", start="2025-11-02 01:30"))
        t = self.call("start_timer", start="2025-11-02 01:30-05:00")["started"]
        self.assertEqual(self.utc(t), ("2025-11-02T06:30:00Z", None))

    def test_a_bare_time_not_yet_come_on_a_change_day_is_yesterdays(self):
        ny = zone("America/New_York")
        for now, said, want in (
                (datetime(2026, 3, 8, 1, 0, tzinfo=ny), "02:30", "2026-03-07T07:30:00Z"),
                (datetime(2026, 11, 1, 1, 15, tzinfo=ny), "01:30", "2026-10-31T05:30:00Z"),
                (datetime(2026, 11, 1, 3, 0, tzinfo=ny), "01:30-05:00",
                 "2026-11-01T06:30:00Z")):
            with self.at(now):
                self.assertEqual(mcp._recent(self.con, said, "start"), want)
        for now, said, err in (
                (datetime(2026, 3, 8, 3, 30, tzinfo=ny), "02:30", "doesn't exist"),
                (datetime(2026, 11, 1, 1, 45, fold=1, tzinfo=ny), "01:30", "happens twice")):
            with self.at(now):
                with self.assertRaises(mcp.ToolError) as cm:
                    mcp._recent(self.con, said, "start")
                self.assertIn(err, str(cm.exception))

    def test_the_repeated_hours_second_pass_is_not_in_the_future(self):
        # 01:45 EST, the second 01:45 of the night: 01:30-05:00 was 15 minutes ago
        with self.at(datetime(2026, 11, 1, 1, 45, fold=1, tzinfo=self.ny)):
            self.assertEqual(mcp._recent(self.con, "01:30-05:00", "end"),
                             "2026-11-01T06:30:00Z")
            t = self.call("start_timer", start="2026-11-01 01:00-04:00")["started"]
            e = self.call("update_entry", id=t["id"], start="01:20-05:00")["entry"]
            self.assertEqual(self.utc(e), ("2026-11-01T06:20:00Z", None))
            # the minute's grace is counted in UTC too, and no more than that
            self.assertEqual(mcp._recent(self.con, "today 01:46-05:00", "end"),
                             "2026-11-01T06:46:00Z")
            self.assertIn("is in the future", self.fails("stop_timer", end="today 01:47-05:00"))
            e = self.call("stop_timer", end="01:30-05:00")["stopped"]
            self.assertEqual(self.utc(e), ("2026-11-01T06:20:00Z", "2026-11-01T06:30:00Z"))
            self.assertEqual(e["seconds"], 600)

    def test_an_offset_is_checked_on_the_day_a_bare_time_falls_on(self):
        # just after each change, last night's 23:00 had the other offset
        for now, said, want in (
                (datetime(2026, 3, 8, 3, 30, tzinfo=self.ny), "23:00-05:00",
                 "2026-03-08T04:00:00Z"),
                (datetime(2026, 3, 8, 3, 30, tzinfo=self.ny), "23:00", "2026-03-08T04:00:00Z"),
                (datetime(2026, 11, 1, 3, 0, tzinfo=self.ny), "23:00-04:00",
                 "2026-11-01T03:00:00Z"),
                (datetime(2026, 11, 1, 3, 0, tzinfo=self.ny), "23:00", "2026-11-01T03:00:00Z")):
            with self.at(now):
                self.assertEqual(mcp._recent(self.con, said, "end"), want, said)
        # a time that gives its date is checked on that date, not moved to another
        with self.at(datetime(2026, 3, 8, 3, 30, tzinfo=self.ny)):
            for said in ("2026-03-08 23:00-05:00", "today 23:00-05:00"):
                with self.assertRaises(mcp.ToolError) as cm:
                    mcp._recent(self.con, said, "end")
                self.assertIn("2026-03-08 23:00 is not at UTC-05:00", str(cm.exception))
            with self.assertRaises(mcp.ToolError) as cm:
                mcp._recent(self.con, "2026-03-07 23:00-04:00", "end")
            self.assertIn("2026-03-07 23:00 is not at UTC-04:00", str(cm.exception))

    def test_time_arguments_document_the_offset(self):
        tools = {t["name"]: t for t in self.rpc("tools/list")["result"]["tools"]}
        for name, arg in (("start_timer", "start"), ("stop_timer", "end"),
                          ("log_time", "start"), ("log_time", "end"),
                          ("update_entry", "start"), ("update_entry", "end")):
            desc = tools[name]["inputSchema"]["properties"][arg]["description"]
            self.assertIn("UTC offset, e.g. '01:30-05:00'", desc, f"{name}.{arg}")


class Stdio(unittest.TestCase):
    def test_subprocess_speaks_only_json_rpc_on_stdout(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "t.db")
        env = {k: v for k, v in os.environ.items() if k != "DUTEMPS_DB"}
        env["PYTHONIOENCODING"] = "cp1252"      # the transport must not depend on it
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "unittest", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "create_client", "arguments": {"name": "Café – Test"}}},
        ]
        data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in msgs)
        p = subprocess.run([sys.executable, "-m", "dutemps", "--db", path, "mcp"],
                           input=data.encode("utf-8"), capture_output=True, cwd=HERE,
                           env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"\r\n", p.stdout)
        lines = p.stdout.decode("utf-8").splitlines()
        replies = [json.loads(ln) for ln in lines]
        for r in replies:
            self.assertEqual(r["jsonrpc"], "2.0")
            self.assertTrue("result" in r or "error" in r)
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertTrue(replies[1]["result"]["tools"])
        res = replies[2]["result"]
        self.assertFalse(res["isError"], res)
        self.assertEqual(res["structuredContent"]["client"]["name"], "Café – Test")
        self.assertIn(b"dutemps-mcp", p.stderr)
        con = db.connect(path)
        self.addCleanup(con.close)
        self.assertEqual([c["name"] for c in core.meta(con)["clients"]], ["Café – Test"])


if __name__ == "__main__":
    unittest.main()
