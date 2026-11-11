# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-11-10

### Added
- Say that nobody asked for an entry, even on a project with a usual requester: tag it with a tag named like the time nobody is named for (Invoice settings, default "Not specified"; for example "Routine upkeep"). That entry's time is listed under that name instead of going to the project's usual requester. An entry with that tag and a requester tag stops the invoice and is named, as two requester tags do.

## [0.4.0] - 2026-11-10

### Added
- Your own wording for requesters on Invoice settings: the heading over who asked (default "Requested by"), for example "Requested or reported by" when the list also holds people who report problems, and the name for time nobody is named for (default "Not specified"), for example "Routine upkeep". A long heading takes two lines over the detail column. Issued invoices keep the wording they were issued with. The MCP settings tools take `requester_labels: {heading, none}`, changing only the keys given.

### Changed
- On page one, hours by requester are titled with that heading. In the detail, lines nobody is named for show the name for that time instead of a blank. Invoices issued with 0.3.0 print as they did.

## [0.3.0] - 2026-11-10

### Added
- Requesters on invoices: name the people who ask you for work on Invoice settings ("Requested by"). Each requester is a tag; tag an entry to say who asked for it, and give a requester the projects they usually ask for, so untagged time there counts as theirs. With requesters, invoice lines are split by who asked, the detail pages get a Requested by column, page one shows hours by requester under hours by project, and the invoice page lists hours by requester. An entry with two requester tags stops the invoice and is named. Invoices without requesters are unchanged.
- The MCP settings tools read and change requesters (`requesters: [{tag, projects}]`; `[]` removes them). An issued invoice compares its hours with the live entries split the same way.

### Changed
- On page one, the hours-by-project rows close up when they would push the billing summary off the page.

## [0.2.0] - 2026-11-10

### Added
- A credit can apply to one group inside a section, as well as to a project or a whole section. Give the group a key (Invoice settings sets one when you pick the group under "Applies to"), and the credit is sized from that group's hours alone. The MCP settings tools take `{group: key}` as a scope.
- The MCP server has a tool for every action in the app, 50 in all: editing and deleting clients, projects, tasks and tags, settings, weekly and series reports, the dashboard, exports, CSV import, backups, invoice settings, and issuing, voiding and deleting invoices. Issuing, voiding and deleting need the invoice number again as `confirm`, checked in the same transaction that makes the change. Report filters take several clients, projects, tasks or tags, as the app's do.

### Changed
- The monthly maximum is optional: a maximum of 0 leaves the maximum and additional-amount rows off the invoice.
- Issuing, voiding and deleting an invoice read, check and write in one transaction, so two processes can't both bill the same period. Invoice settings changes are merged and saved under one lock, so an edit saved meanwhile is kept.

### Fixed
- Files are never overwritten, even in a race; file names made from invoice numbers can't leave the folder given; a failed backup closes and removes its file.
- Two tests that preview invoice settings no longer depend on today's date.
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

[Unreleased]: https://github.com/jibarix/dutemps/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/jibarix/dutemps/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/jibarix/dutemps/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/jibarix/dutemps/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jibarix/dutemps/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/jibarix/dutemps/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jibarix/dutemps/releases/tag/v0.1.0
