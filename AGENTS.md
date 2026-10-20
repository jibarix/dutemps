# DuTemps: notes for coding agents

`README.md` says what the app does. `DESIGN.md` has the decisions behind it.
If `local/AGENTS.md` exists, read it too. It holds the owner's private notes
for their own setup and is never committed.

- Standard library only (DESIGN D1). openpyxl is optional and used only for
  the XLSX export. Don't add packages.
- `data/` is the user's real record of billable hours. Never commit anything
  from it, and never write to it from tests. Make a backup first
  (`python -m dutemps backup`), and try changes on a scratch database
  (`python -m dutemps --db <scratch> ...`).
- Every write goes through `dutemps/core.py` (invoices go through
  `dutemps/invoices.py`), so validation and the audit log can't be skipped.
- An issued invoice is final. Its snapshot and PDF are stored, and database
  triggers refuse to change or delete them. A wrong invoice is voided and a
  new one issued. Never work around the triggers.
- Invoice lines group time by (local date, project, task), rounded to the
  nearest 0.25 h with a 0.25 h minimum. `tests/test_core.py` checks the sample
  month against a calculation made straight from the CSV.
- `dutemps/mcp.py` is the MCP server (`python -m dutemps mcp`, DESIGN D8): a
  hand-written JSON-RPC stdio loop whose tools call core, reports, exports,
  importers and invoices. stdout carries protocol messages only; log to
  stderr. Everything the app can do has a tool: a new feature gets its tool
  in the same change. Issuing, voiding and deleting an invoice need the
  invoice number again as `confirm`. `tests/test_mcp.py` covers it.
- Tests: `python -m unittest discover -s tests` from this folder. They use
  `tests/fixtures/` and the example profile. Any new fixture must be made up:
  no real names, clients, addresses or amounts.
- Before committing, `python scripts/check_private.py` must pass. The
  `--install` option sets it up as a pre-commit hook.
