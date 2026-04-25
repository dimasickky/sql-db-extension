# Changelog

All notable changes to Imperal SQL DB are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [1.3.4] — 2026-04-26

Hotfix on top of 1.3.3 — schema cache mirror was silently failing in production with `I-CACHE-MODEL-REGISTRATION-REQUIRED`, leaving the column-level validator permanently cold.

### Fixed

- **`@ext.cache_model("db_schema_snapshot")` now decorates `DbSchemaSnapshot` directly** instead of an empty subclass `_DbSchemaSnapshotCache`. SDK 1.6.x reverse-lookup in `extension._resolve_cache_model_name` uses class identity (`registered_cls is cls`), not `isinstance`. The 1.3.3 wrapper class registered an object distinct from the one passed at `ctx.cache.set(..., model=DbSchemaSnapshot)` / `ctx.cache.get(..., model=DbSchemaSnapshot)` call sites, so the registry never matched and every mirror attempt fell back to the warning-and-noop path.

### Why this matters

In 1.3.3 production, every skeleton refresh logged `WARNING sql-db — schema cache mirror failed: cache model 'DbSchemaSnapshot' is not registered`. The cache stayed empty, `load_schema_section(ctx)` returned `{}` on every read, both `validate_table_exists` and `validate_columns` short-circuited to `None`, and the column-hallucination guard never fired — `INSERT INTO orders (..., total_amount, ...)` reached the backend and got `1054 Unknown column 'total_amount'` from MariaDB instead of the friendly recovery hint. With the registration fixed, the same INSERT now hits the in-process gate first and the LLM gets `Unknown column(s) for table 'orders': total_amount. Valid columns: ... . Call get_schema('orders') and retry.` — which the `system_prompt.txt` worked example trains it to recover from.

### No code-shape changes

`schema_guard.py`, `skeleton.py`, `handlers_*.py`, `sql_parser.py`, `system_prompt.txt` — all unchanged from 1.3.3. The fix is one decorator move.

---

## [1.3.3] — 2026-04-26

Fix the 1054 column-hallucination class. Pre-write validation moves off the dead `ctx.skeleton_data` path (gone since SDK 1.6.0) onto the supported `ctx.cache` channel, with the `@ext.skeleton('db_schema')` refresher mirroring its payload to a Pydantic-typed cache entry that `@chat.function` handlers can read.

The same skeleton snapshot is now visible from both surfaces — read-only LLM envelope (classifier) and read/write cache (write-time guard) — without violating the v1.6.0 `SkeletonAccessForbidden` boundary.

### Added

- **`@ext.cache_model("db_schema_snapshot")`** in `app.py` — Pydantic models `DbSchemaSnapshot`, `DbSchemaTable`, `DbSchemaColumn`, plus constants `SCHEMA_CACHE_KEY` and `SCHEMA_CACHE_TTL`.
- **`load_schema_section(ctx)`** + **`invalidate(ctx)`** in `schema_guard.py` — async accessors over `ctx.cache.get/delete` with `model=DbSchemaSnapshot`. Cold cache returns `{}`; transport / model-mismatch errors are caught and treated as cold.
- **Column-level guard on `execute_sql`** — INSERT/UPDATE column lists are extracted from the SQL and validated against the cached schema before round-tripping. Recovery hint is appended ("Call get_schema('<table>') and retry") so the LLM has a clear next-tool-use to call.
- **DDL cache invalidation** — successful `CREATE / DROP / ALTER / TRUNCATE / RENAME` drops the cached snapshot via `invalidate(ctx)`. The next write either sees a fresh skeleton refresh or cold-cache-skips validation, never a stale shape.
- **`extract_insert_columns` / `extract_update_columns`** in `sql_parser.py` — depth- and quote-aware top-level splitter; conservative on shapes the parser can't isolate (returns `[]` → caller skips).
- **System prompt — worked examples for column hallucination.** Three BAD / GOOD pairs covering (a) suffix-drop (`category` vs `category_id`), (b) inventing a column on a table the assistant just created, (c) tool-error recovery loop. Plus an explicit rule: after `CREATE TABLE` in the current turn, always `get_schema()` before the first `INSERT` into that table.

### Changed

- **`schema_guard.py`** — public surface refactored from `(ctx, ...)` to `(section: dict, ...)`. Callers load the section once via `await load_schema_section(ctx)`, then run synchronous validators against it. Reduces per-call cache reads in handlers that validate both a table and its columns.
- **`skeleton.py`** — every successful and partial-failure return path now mirrors its payload to `ctx.cache` via `_mirror_to_cache(ctx, payload)`. Mirror failures are logged at `WARNING` and never break the skeleton refresh itself.
- **`handlers_rows.py`** — three `validate_table_exists` / `validate_columns` call sites updated to load the section once per handler.
- **`app.py`** — version bumped to `1.3.3`.

### Why this matters

In production logs from 2026-04-25, `gpt-4.1-mini` running inside `tool_sql_db_chat` issued `INSERT INTO products (name, category, price, stock) VALUES (...)` — but the real schema is `(id, name, category_id, price, stock)`. MariaDB returned `1054 Unknown column 'category'`, the LLM did not engage the SCHEMA-FIRST recovery pattern from the system prompt, and a second hallucinated INSERT into a freshly-created `employees` table with a phantom `department` column failed the same way.

Three structural causes:

1. The existing `schema_guard` reached for `ctx.skeleton_data`, which SDK 1.6.0 removed — it was a silent no-op on the 1.6.2 baseline. Validation that should have caught the unknown column never ran.
2. `fn_execute_sql` called only `list_known_tables` (table-level) and never invoked `validate_columns` even though the helper existed.
3. Schema cache had a 300 s TTL with no invalidation on DDL, so a `CREATE TABLE` followed by an immediate `INSERT` against the new table ran against a stale snapshot.

This release closes all three: the cache channel works under 1.6.x's permission model, `execute_sql` now runs both gates, and successful DDL drops the cache so the next refresh repopulates with the new shape.

---

## [1.3.2] — 2026-04-25

Pin `imperal-sdk==1.6.2` after rolling back the v2.0.0 / SDK v2.0 / Webbee Single Voice rebuild. Code unchanged from 1.3.1; only the SDK constraint moves from `>=1.5.26,<1.6` to the exact runtime version in production. The v2.0 work is preserved on the `sdk-v2-migration` branch (and tagged `pre-1.6.2-rebuild-2026-04-25` on main pre-reset) for incremental re-roll.

### Changed

- **`requirements.txt`** — `imperal-sdk>=1.5.26,<1.6` → `imperal-sdk==1.6.2`. Hard pin is required because PyPI `imperal-sdk==2.0.0` is immutable and resolver picks it without an explicit constraint.

---

## [1.3.1] — 2026-04-23

Symmetry patch bringing sql-db onto the same fail-fast ctx contract as notes 2.4.1. No behaviour changes except: a chain step arriving without `ctx.user` populated now produces a loud `ActionResult.error("No authenticated user…")` instead of silently scoping every `ctx.store` / db-service query to `user_id=""` and returning empty collections (indistinguishable from a real empty list).

### Added

- **`require_user_id(ctx)`** in `app.py` — raises `RuntimeError` when `ctx.user` is missing. Handlers' existing `except Exception` converts it to a clean `ActionResult.error`. Tolerant `_user_id(ctx)` kept for panel / skeleton renderers that must survive anonymous sessions.

### Changed

- All `@chat.function` handlers migrated to `require_user_id`: `handlers_connections.py`, `handlers_query.py`, `handlers_execute.py`, `handlers_rows.py`, `handlers_history.py`, `handlers_nlq.py`. `panels.py` / `skeleton.py` / `panels_editor.py` keep tolerant `_user_id()` — renderers must still render on anonymous ctx.
- Version bump 1.3.0 → 1.3.1 in `imperal.json` + `app.py`.

---

## [1.3.0] — 2026-04-23

Fundamental hygiene pass after deep audit against SDK 1.5.26. No behaviour changes for the LLM, but the extension now obeys all platform conventions and removes two workarounds for kernel bugs that have since been fixed upstream.

### Added

- **`schema_guard.py`** — programmatic column-name validation against the skeleton cache before every `insert_row` / `update_row` / `delete_row`. Unknown columns are rejected with a structured `Unknown columns [...]. Valid: [...]` message so the LLM can self-correct in one turn instead of chasing raw MySQL 1054 errors across retries.
- **`execute_sql` table gate** — extracts target table from parsed SQL and fails fast with an "available tables" hint when the table is absent from the skeleton.
- **`resolve_connection` fallback logging** — when no connection is marked active and we pick the first one available, we now emit a `log.warning` with the connection name + id. Helps support trace "wrong database" UX when a user has prod + staging saved.

### Changed

- **Raw `httpx.AsyncClient` → SDK `HTTPClient`** (`app.py`). Typed `HTTPResponse` (`.status_code` / `.ok` / `.body` / `.json()`) replaces ad-hoc response handling. Same wrapper `ctx.http` uses under the hood; chosen at module level because `_api_*` helpers are called from panel renderers that don't thread `ctx`.
- **Manifest hygiene** (`imperal.json`):
  - Dropped legacy `scopes: ["*"]` wildcard on the ChatExtension entry.
  - Dropped manually-declared `skeleton_refresh_db_schema` / `skeleton_alert_db_schema` — these are auto-derived from the `@ext.skeleton` decorator since SDK 1.5.22 and were causing Registry sync drift.
  - `required_scopes` normalized to colon-form (`sql-db:read`, `sql-db:write`); removed the `"*"` umbrella.
- **`Extension(...)` capabilities** — now declares `capabilities=["sql-db:read", "sql-db:write"]` explicitly at construction time.
- **Panel god-files split** — `panels_editor_results.py` (was 410 lines) → extracted `_editor_result_renderers.py`. `panels_editor_row_form.py` (was 347 lines) → extracted `_row_form_inputs.py` + `_row_form_submit.py`. Every file now ≤280 lines, enforcing the 300-line rule.
- **`nl_to_sql` prefers skeleton** — `handlers_nlq.py` reads the cached schema from `ctx.skeleton.get("db_schema")` before making a cold-path `/v1/connections/{id}/schema` call. Cuts a round-trip on the hot path.
- **SDK pin** — `imperal-sdk>=1.5.26,<1.6` (from `v1.5.24` git URL). Absorbs narration guardrail, `@ext.skeleton` decorator, structural contradiction guard, `check_write_arg_bleed`.

### Removed

- **`_direct_params(ctx)` fallback** in `handlers_execute.py` — the kernel session-42 automation-path fix (I-AUTO-TOOL-CALL, SDK 1.5.21+) is rolled out and Pydantic params now bind normally. The workaround is dead code.

### Known limitations / deferred

- **`ActionResult.error(error_code=...)` not yet adopted.** SDK 1.5.26's `ActionResult.error` signature is `(error: str, retryable: bool = False)` — no `error_code` kwarg. The `ERROR_TAXONOMY` guard (`imperal_sdk.chat.guards.check_write_arg_bleed`) currently reads `error_code` from raw-dict results, not `ActionResult`. Deferred pending SDK API. When it lands, migrate `str(e)` → `error_code="SQL_UNKNOWN_COLUMN" | "SQL_CONNECTION_NOT_FOUND" | "BACKEND_UNAVAILABLE"` and push raw details into `data={"detail": ...}`.
- **`_pulse_sql_executed` self-IPC** (from 1.2.1) still in place — documented anti-pattern, rewrite to `ctx.events.publish` when panel handlers get direct event access.

### Why this release matters

Two bugs the user kept hitting in chat — LLM hallucinating column names (`status`, `payment_method` not in table) and `{name}` literal leaking into error messages — were half us, half platform. This release closes the extension half:

- Column hallucination: one-turn structured correction instead of opaque MySQL 1054.
- Scope/manifest drift: nothing in `imperal.json` can now confuse the Hub's tool resolver.

The `{name}` / function-not-found side is 100% kernel (un-interpolated f-string in the tool dispatcher) — reported to the platform team separately.

---

## [1.2.1] — 2026-04-17

Sidebar row counts refresh after panel-direct DML.

### Added

- `_pulse_sql_executed` internal chat function (`event="sql.executed"`, `action_type="write"`) — does nothing, exists only so the kernel publishes the event.
- `run_and_show` (SQL Editor Execute) calls it via `ctx.extensions.call("sql-db", "_pulse_sql_executed", ...)` after successful DML.
- `process_row_form_submit` (Row Form insert/update) does the same.

### Why

Both the editor Form and the row_form submit go to their panel handlers which call `/v1/connections/{id}/execute` and `/row` directly via httpx — bypassing `@chat.function`. Kernel auto-event-publishing only fires when a `@chat.function` with `event=` returns `ActionResult.success`. Without this pulse, `sql.executed` / `row.*` never fire for panel-driven DML → sidebar's `refresh="on_event:..."` subscription never triggers → the schema row count stays stale.

### Known remaining limitation

InnoDB's `INFORMATION_SCHEMA.TABLES.TABLE_ROWS` is an estimate, not a live count — even when the sidebar refreshes, the shown number can lag reality for a few seconds until MariaDB refreshes internal stats. For a live count we would need a per-table `SELECT COUNT(*)` during sidebar render (N extra queries, rejected for MVP).

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
  - Render a page-size `ui.Select` (10 / 25 / 50 / 100 / 200 / 500) — switching resets page to 0
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

- `list_history` — recent queries per connection (stored server-side in the hosted backend DB)
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
