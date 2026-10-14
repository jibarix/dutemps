"""python -m dutemps <command>

  serve [--port 8765] [--no-browser]       run the app
  import-csv CSV... [--dry-run]            import entries from CSV (columns in README.md)
  invoice-lines FROM TO                    billed lines, rounded to the quarter hour
  invoice-pdf NUMBER DATE FROM TO OUT      render an invoice PDF (not stored)
  backup [DEST]                            consistent copy of the database
  mcp                                      MCP server on stdin/stdout, for agents
"""
import argparse
import json
import os
import sys
from datetime import datetime

from . import core, db, importers, invoices, mcp, reports, server


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m dutemps", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="database path (default data/dutemps.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s = sub.add_parser("import-csv")
    s.add_argument("csv", nargs="+")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("invoice-lines")
    s.add_argument("date_from")
    s.add_argument("date_to")
    s = sub.add_parser("invoice-pdf")
    s.add_argument("number")
    s.add_argument("issued", help="invoice date YYYY-MM-DD")
    s.add_argument("date_from")
    s.add_argument("date_to")
    s.add_argument("out")
    s.add_argument("--retainer", type=float, default=0)
    s.add_argument("--retainer-label", default="")
    s.add_argument("--credits", help="percentages in profile order, e.g. 10,50")
    s = sub.add_parser("backup")
    s.add_argument("dest", nargs="?")
    sub.add_parser("mcp")
    a = ap.parse_args(argv)

    con = db.connect(a.db)
    invoices.PROFILE_PATH = invoices.profile_path(con)
    show = lambda x: print(json.dumps(x, indent=2, ensure_ascii=False))
    try:
        if a.cmd == "serve":
            server.serve(con, a.port, not a.no_browser)
        elif a.cmd == "import-csv":
            for p in a.csv:
                show(importers.import_csv(con, p, dry_run=a.dry_run))
        elif a.cmd == "invoice-lines":
            r = reports.invoice_lines(con, {"from": a.date_from, "to": a.date_to})
            for l in r["lines"]:
                print(f"{l['date']}  {l['hours']:6.2f}  {l['raw_hours']:8.4f}  "
                      f"{l['project']} / {l['task']}")
            print(f"{len(r['lines'])} lines, {r['hours']:.2f} h billed "
                  f"({r['raw_hours']:.4f} h raw), ${r['amount']:,.2f}")
        elif a.cmd == "invoice-pdf":
            prof = invoices.load_profile()
            pcts = [float(x) for x in a.credits.split(",")] if a.credits else None
            inv = invoices.build(con, prof, a.number, a.issued, a.date_from, a.date_to,
                                 a.retainer, a.retainer_label, pcts)
            with open(a.out, "wb") as f:
                f.write(invoices.render(inv, prof))
            print(f"{a.out}: {inv['hours']:.2f} h, total ${inv['total']:,.2f}, "
                  f"net ${inv['net']:,.2f}")
        elif a.cmd == "backup":
            dest = a.dest or os.path.join(db.db_dir(con), "backups",
                                          f"dutemps-{datetime.now():%Y%m%d-%H%M%S}.db")
            print(db.backup(con, dest))
        elif a.cmd == "mcp":
            mcp.serve(con)
    except core.Invalid as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
