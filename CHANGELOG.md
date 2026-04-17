# Changelog

All notable changes to Imperal SQL DB are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [1.2.0] — 2026-04-17

Real pagination on browse — use case: tables with thousands of rows.

### Added

- `page` and `page_size` params on `__panel__editor` (default `0` and `50`).
- When the executed SQL is a simple single-table SELECT and `paginate=True`:
  - Strip any trailing `LIMIT … OFFSET …` from the SQL
  - Append `LIMIT page_size OFFSET page*page_size` server-side
  - Run a separate `SELECT COUNT(*)` to know the total
  - Render an Alert "Showing rows X-Y of N"
  - Render Previous / Next buttons + "Page N of M · N row(s) total" caption
- Page size capped 5..500. Multi-statement runs skip pagination (each statement gets default 200).

### Changed

- Sidebar table click no longer hard-codes `LIMIT 200`. Sends a bare `SELECT * FROM \`table\`` and lets the paginator handle slicing per page.
- `run_and_show()` signature gains `page`, `page_size`, `paginate` kwargs.

### Notes

- Pagination is OFFSET-based (server-side) using the existing `/v1/connections/{id}/query` endpoint. Keyset cursor would scale better for >100k rows but adds backend complexity — deferred.
- COUNT(*) cost is one extra query per render. For huge tables, consider INFORMATION_SCHEMA `TABLE_ROWS` (approximate, free) — future optimisation.

---

## [1.1.0] — 2026-04-16

Row-level CRUD in the panel UI. No more raw-SQL-only for simple edits.

### Added

#### Clickable schema (sidebar)

- Schema `ui.Tree` replaced with expandable `ui.List` — each table is a clickable `ListItem` (`expandable=True`, columns in `expanded_content`)
- Click a table → `SELECT * FROM \`table\` LIMIT 200` runs in the editor's results tab (zero typing)
- Secondary "Open in Editor" action per table — loads the SELECT into the editor tab without executing
- Primary-key columns highlighted with `"Key"` icon + yellow `"PK"` badge in the column list

#### Row Form tab (`tab=row_form`)

- New `__panel__editor` tab `row_form` — type-aware Insert/Edit form rendered from `/schema` introspection
- Mode `insert` — empty form, all columns editable, auto-increment PKs auto-skipped
- Mode `edit` — current row fetched (SELECT by PK), values pre-filled via `ui.Form(defaults=...)`
- Input type per column: `ui.Toggle` for boolean/`tinyint(1)`, `ui.TextArea` for TEXT/BLOB/JSON, `ui.Input` otherwise
- Column labels carry `(type · PK · NOT NULL · auto)` hints
- Composite / no-PK tables → `ui.Alert` "no primary key — edit and delete disabled"
- Delete button (edit mode) with confirm → dispatches to `delete_row` chat handler
- Back-to-Browse button returns to results tab with the original `SELECT *`

#### Row click interactivity (results tab)

- `ui.DataTable` in results gets `on_row_click` when the SQL is a simple single-table SELECT and a PK is detected
- Click a row → opens `row_form` in edit mode for that PK value
- Detection: regex `^SELECT .+? FROM <ident>` + rejection of `JOIN` / `UNION` anywhere in the statement
- "Insert new row into `<table>`" button rendered above the DataTable for every single-table SELECT
- Row `id` = PK value when detectable — so `on_row_click` delivers the correct row via the `row` dict convention

#### Chat functions (`handlers_rows.py`)

- `insert_row` (action_type=write, event=`row.inserted`) — parameterized INSERT via `/v1/connections/{id}/row`
- `update_row` (action_type=write, event=`row.updated`) — parameterized UPDATE with WHERE pk=value, `LIMIT 1`
- `delete_row` (action_type=destructive, event=`row.deleted`) — parameterized DELETE with `LIMIT 1`
- Values travel as JSON strings (`values_json`) and are parsed server-side; no SQL-string assembly anywhere

#### Backend endpoint (db-service v1.1.0)

- `POST /v1/connections/{conn_id}/row` — single endpoint for all three row operations
- Identifiers (table + column names) validated against `^[A-Za-z_][A-Za-z0-9_]*$` and backtick-escaped
- Values bound via `aiomysql` `%s` placeholders — never interpolated
- UPDATE/DELETE refuse empty WHERE (explicit guard, not just a missing clause)
- `LIMIT 1` on UPDATE/DELETE — defence in depth against PK collisions
- Audit row in `query_history` per call (`sql_text="[row.insert] table"`, truncated)

### Changed

- `panels_editor.py` split for maintainability:
  - `panels_editor.py` — tab dispatcher + SQL form (was 440L, now ~150L)
  - `panels_editor_results.py` — `run_and_show` (execute + render for run/explain/dry_run)
  - `panels_editor_tabs.py` — History + Saved renderers
  - `panels_editor_row_form.py` — row_form tab + form submit processor
  - `sql_parser.py` — pure `split_statements` + `classify_sql` (no UI, no I/O)
- `main.py` — cleanup list + imports extended for new modules
- Sidebar `refresh="on_event:..."` now subscribes to `row.inserted,row.updated,row.deleted,sql.executed` in addition to connection events
- Editor panel `refresh="on_event:row.inserted,row.updated,row.deleted"` — so the results tab reloads after saves

### Fixed

- Schema `ui.Tree` nodes were not clickable (SDK `ui.Tree` exposes no `on_click`) — replaced with `ui.List` of `ui.ListItem` which supports `on_click`. This closes one of the P0 known issues in the extension doc.

### Security

- `/row` endpoint — full parameterization, identifier whitelist, WHERE-required guard. SQL injection attempts via `table`/`column` names rejected at 400 with explicit error
- Smoke-tested 2026-04-16: `table="users; DROP TABLE x; --"` → 400, `operation=update` with empty `where` → 400, `column="name; DROP"` → 400

---

## [1.0.0] — 2026-04-15

Initial release — production-ready end-to-end SQL workbench.

### Added

#### Core

- `ChatExtension` pattern — single `tool_sql_db_chat` entry point with LLM internal routing
- 17 chat functions across 5 domains: connections, schema/query, execute, history, NLQ
- Fernet password encryption — plaintext passwords never touch the Store
- Backend HTTP client with error-detail preservation (no `raise_for_status()`)
- `@ext.health_check` probe returning backend reachability
- `@ext.on_install` lifecycle hook

#### Connections (`handlers_connections.py`)

- `add_connection` — test-then-save flow with Fernet encryption
- `list_connections`, `test_connection`, `select_connection`, `delete_connection`
- Stored in Auth Gateway `ctx.store` collection `db_connections`
- Active-connection fallback in `resolve_connection()`

#### Queries (`handlers_query.py`, `handlers_execute.py`)

- `run_query` — SELECT with auto-LIMIT (default 100)
- `execute_sql` — DML/DDL via 2-Step Confirmation
- `get_schema` — tables, columns, indexes
- `explain_query` — MySQL EXPLAIN plan
- `dry_run` — transaction + ROLLBACK preview
- `run_editor_sql` — universal editor-side runner (auto-routes read/write/explain)

#### Natural Language (`handlers_nlq.py`)

- `nl_to_sql` — uses `ctx.ai.complete` with schema context to generate SELECT

#### History & Saved (`handlers_history.py`)

- `list_history` — recent queries per connection (stored in internal Galera DB)
- `save_query`, `list_saved`, `run_saved`, `delete_saved`

#### Skeleton (`skeleton.py`)

- `skeleton_refresh_db_schema` — background compact schema snapshot
- `skeleton_alert_db` — tables-added/removed diff

#### UI Panels

- `panels.py` — sidebar (left): connection list + New Connection form (`ui.Card`, `ui.Input` with `param_name`) + schema tree (`ui.Tree`)
- `panels_editor.py` — editor (center): `ui.Form` with Action dropdown + TextArea → inline DataTable/Alert results
- **Self-contained editor** — panel calls backend directly and renders results in-place (no chat round-trip)
- Multi-statement splitter (quote-aware) with per-statement results and dividers
- Smart SQL classifier — first-word + WITH/CTE detection + comment stripping
- Auto-fallback — tries `/query` for read, `/execute` for write, retries on backend mismatch
- Explain and Dry Run are first-class modes (not shoehorned into Run)

### Platform-level lessons baked in

- `ui.Form` is the only reliable way to collect `param_name` values for submission
- `ui.Alert` uses `title=`, `message=`, `type=` (not `variant=`)
- Backend validator needs first-word fallback for sqlglot "Command" statements (ALTER DATABASE, SET GLOBAL, …)
- `raise_for_status()` destroys HTTP error detail — extract `.detail` manually on 4xx/5xx
- After writing extension files, `touch main.py` to trigger kernel mtime hot-reload

### Stress-tested (2026-04-15)

Passed all 11 scenarios: aggregations, subqueries, UPDATE+SELECT, DELETE+COUNT, quotes + emoji UTF-8 roundtrip, NULL/COALESCE, 10-row batch INSERT, unknown-column error surface, UNION, dry-run, EXPLAIN.
