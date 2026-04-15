# Changelog

All notable changes to Imperal SQL DB are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

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
