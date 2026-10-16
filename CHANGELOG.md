# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- Long descriptions can be read and edited in full. In the Time Tracker the description now sits on its own line across the row, wrapped in full, with the project and tags underneath; clicking it opens a box that grows as you type. The Edit dialog has the same box. In both, Enter saves and Shift+Enter starts a new line.
- Entries of one day with the same description, project, task, tags and billable setting show as one row with a count, their time span and total. Editing the row's description, project, tags or billable setting changes every entry in it; the count opens the entries one by one.
- Descriptions with several lines keep their line breaks when edited. Before, the one-line field in the entry list and the Edit dialog joined the lines on save. A running entry with several lines opens the Edit dialog from the timer bar.
- Reports, the dashboard, invoice lines and recently deleted entries show line breaks in descriptions.

## [0.1.1] - 2026-10-14

### Added
- Screenshots of the app and the invoice PDF in the README, made from demo data.
- `check_private.py` accepts PNG screenshots in `docs/images/` when they hold only pixels, with no text or metadata chunks.

### Fixed
- Bar charts on the dashboard and in reports fill their card instead of floating in empty space.
- Short calendar blocks no longer cut their text off halfway through a line.
- Project labels in the entry list line up whether or not a row has tags.

## [0.1.0] - 2026-10-14

First public release. Tested with Python 3.11.9 on Windows 11.

### Added
- Time tracking in a local web app: live timer, manual entries, timesheet, week calendar, projects with tasks, clients, tags, billable rates and soft delete with undo.
- Dashboard and reports (summary, detailed, weekly, invoice lines) with filters and CSV or Excel export.
- Invoices: drafts built from billable time, credits and a retainer, issued as a frozen snapshot and exported as a PDF with charts and an hours appendix. Pure Python, no build step and no third-party services.
- An MCP server (`python -m dutemps --db PATH mcp`) so Claude Code, Codex and other agents can read status and reports, run the timer, log and edit time, and draft invoices. Issuing and voiding stay with the user in the app.
- Import from CSV, SQLite backups, and a pre-commit check that keeps private terms out of the repository.

[Unreleased]: https://github.com/jibarix/dutemps/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/jibarix/dutemps/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jibarix/dutemps/releases/tag/v0.1.0
