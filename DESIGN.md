# DuTemps: design decisions

DuTemps is a local time tracker for one person, with PDF invoices.

## Scope

In:

- timer, manual entries, duplicate and bulk delete;
- projects (client, colour, billable, rate, estimate, archive), tasks,
  clients and tags;
- timesheet, calendar, dashboard, and summary, detailed and weekly reports;
- CSV and XLSX export;
- CSV import;
- PDF invoices;
- an audit log with restore;
- an MCP server, so coding agents can track time, read reports and draft
  invoices.

Out:

- team features: members, approvals, time off, scheduling, shared reports,
  webhooks;
- expenses;
- any network service or login. The single user is whoever runs it.

## Constraints

- Python 3.11+ standard library. openpyxl is optional and used only for the
  XLSX export. On Windows, `tzdata` provides the timezone database.
- Times are stored in UTC (`YYYY-MM-DDTHH:MM:SSZ`). They are displayed and
  grouped in the timezone on Settings.
- CSV import times are local, in that same timezone.
- Localhost safety: the server binds to 127.0.0.1 and checks the Host header
  (against DNS rebinding). Every write must carry `X-DuTemps: 1`, so a page
  in another tab can't forge a cross-site write.

## Decisions

- **D1. Standard library over a web framework.** It has one user on
  localhost, with a few thousand rows. Flask, FastAPI or React would each add
  a dependency to vet and upgrade, without changing the outcome. Reconsider
  if a second user or remote access is ever needed.
- **D2. SQLite, one file at `data/dutemps.db`.** A plain file copy backs it
  up. `python -m dutemps backup` makes a consistent copy with the SQLite
  backup API.
- **D3. Computed rates.** An entry stores a rate only when it overrides the
  chain (task, project, client, default). Imported entries keep their CSV
  rate as an override, so later rate changes don't rewrite past invoices.
- **D4. Invoice lines round per (local date, project, task)** to the nearest
  0.25 h, with a 0.25 h minimum. Invoice totals can therefore differ from the
  per-entry amounts in Summary. A test checks the rule against a calculation
  made straight from the sample CSV.
- **D5. Imports are idempotent.**
  - CSV rows are keyed on start|end|project|task.
  - Re-running an import adds nothing.
  - Each import runs in one transaction and has a `--dry-run`.
- **D6. Backups go beside the open database**, in a `backups/` folder next to
  it. Scratch runs therefore never write into the real backup folder.
- **D7. Invoices are PDFs that DuTemps writes itself.**
  - `dutemps/pdf.py` writes them with the standard PDF fonts and no package.
  - Page 1 is a summary with charts. The line detail is Appendix A.
  - The standing text (parties, sections, credits, notes) is in
    `invoice_profile.json` beside the database (`data/` by default). It is
    edited on Invoices → Invoice settings.
  - An invoice bills only the client chosen in the profile (only time with
    no client, when none is chosen). The rest is shown as left out, for
    its own invoice, and two clients' invoices may cover the same days.
    The profile keeps the client's id too, so renaming a client can't bill
    the same work twice. Profiles saved before that find the id by name,
    renames included (the audit keeps old names); an issued invoice whose
    name fits no client, or several, blocks overlapping invoices.
  - Until the first save, the app shows `examples/invoice_profile.json` and
    refuses to issue.
  - Amounts are calculated, never typed. Each credit is rounded to the cent
    from the hours in its scope.
  - A draft stores only its parameters: number, dates, retainer and credit
    percentages.
  - Issuing freezes the built invoice, the profile it used and the exact PDF
    bytes. Database triggers keep them unchanged, and void is the only later
    change.
  - Saving the profile checks that every string fits where the PDF prints
    it, keeps the old file in `data/backups/`, and logs before and after in
    the audit table.
- **D8. The MCP server is hand-written on the standard library**
  (`dutemps/mcp.py`, `python -m dutemps mcp`).
  - MCP's stdio transport is newline-delimited JSON-RPC 2.0, small enough to
    write without the SDK, so D1 holds. The agent starts the process; no port
    is opened.
  - Tools call the same functions as the HTTP API, so validation, the audit
    log and the invoice triggers apply unchanged.
  - Tools take names (case-insensitive) or ids, and local dates and times. An
    ambiguous or unknown name is an error listing the candidates, never a
    guess.
  - Everything the app does has a tool, so an agent can do whatever the
    user asks (changed 2026-10-20; until then issuing, voiding and the
    invoice settings were left to the app). Issuing, voiding and deleting an
    invoice can't be undone, so their tools take the invoice number a second
    time as `confirm`. Files an agent writes go to the path it gives or
    beside the database, and never replace an existing file.
  - It can share the database with the running app: WAL and a 5 s busy
    timeout let the two processes take turns. A change that depends on the
    current row (moving an entry and keeping its length, switching timers)
    reads and writes in one transaction, so it can't undo an edit made in the
    app meanwhile. A back-dated `start_timer` ends the running timer at that
    start.
  - Local times the clocks skip are refused. Ones they repeat need a UTC
    offset to say which. Infinite and NaN numbers are refused on the way in
    and never written on the way out.

## Acceptance examples

| Need | Behaviour | Evidence |
|---|---|---|
| CSV import | The sample month gives the hours per date/project/task computed straight from the CSV (71.25 h) | `tests/test_core.py` |
| Idempotent import | A second import adds 0 entries | `tests/test_core.py` |
| One running timer | Starting a second timer stops the first at the same instant | test, plus a database constraint |
| Detailed export | The same 18 columns in the same order every time | `tests/test_core.py` |
| Recovery | A delete can be undone from the audit log | `tests/test_core.py` |
| Invoice | The sample month with the example profile: 71.25 h, credits −$332.50 and −$687.50, net $5,605.00 after a $500 retainer; the PDF renders byte-identically twice | `tests/test_invoices.py` |
| Invoice lifecycle | A draft follows the entries; issuing stores the snapshot and PDF, which the database refuses to change or delete; an issued period can't be invoiced twice; the example profile can't be issued | `tests/test_invoices.py` |
| MCP server | The handshake and errors follow JSON-RPC; every app action has a tool; issue, void and delete need the number as confirm; files never overwrite; stdout carries only protocol lines | `tests/test_mcp.py` |
