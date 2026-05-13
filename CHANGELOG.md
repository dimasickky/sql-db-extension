# Changelog

All notable changes to Imperal SQL DB are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [2.4.3] — 2026-05-13

### Changed

- SDK bumped `4.2.6 → 4.2.10` — picks up OAuth callback infrastructure + `ctx.webhook_url()` (4.2.7), `SecretDecl` in Manifest schema (4.2.8/4.2.9), and `chain_callable=True` default for read handlers (4.2.10). Read handlers (`get_schema`, `run_query`, `list_connections`, etc.) now dispatch typed directly — no longer routed through BYOLLM loop.

---

## [2.4.2] — 2026-05-13

### Changed

- SDK bumped `4.2.1 → 4.2.6` — picks up EXT-SECRETS-V1 (unconditional Secrets panel in right slot), validator synthetic-tool fix (4.2.5), and `ui.Password` primitive (4.2.6).
- **New Connection form**: password field switched from `ui.Input` to `ui.Password` — input is now masked while typing.

---

## [2.4.1] — 2026-05-12

### Changed

- SDK bumped `4.2.0 → 4.2.1` — fixes MANIFEST-SKELETON-1 false positive on `@ext.tool("skeleton_alert_*")`.

---

## [2.4.0] — 2026-05-11

### Changed

- **SDK bumped `4.1.3 → 4.2.0`** — no behavioral changes for this extension.

### Fixed

- **[I-MAGIC-UX] All 21 raw exception leaks eliminated** across all handler files. Raw `str(e)` and `f"Invalid JSON: {e}"` were reaching users in violation of I-MAGIC-UX-1/2. All sites now `log.error(...)` internally and return a stable safe message.
  - `handlers_connections.py` — 6 sites (add/list/resolve/test/select/delete connection)
  - `handlers_query.py` — 4 sites (run_query, get_schema, explain_query, dry_run)
  - `handlers_execute.py` — 2 sites (execute_sql, run_editor_sql)
  - `handlers_rows.py` — 4 sites (insert/update/delete row + `_parse_values` JSON error)
  - `handlers_history.py` — 5 sites (list_history, save/list/run/delete saved)
  - `handlers_nlq.py` — 1 site (nl_to_sql)
- **[Skeleton] `"error": str(e)` removed from degraded return in `skeleton.py`** — zero-value dict only on backend failure.
- **[V18] `from __future__ import annotations` removed** from all 6 handler files that define Pydantic `BaseModel` param classes.
- **[Logging] `import logging` + `log` added** to `handlers_connections.py`, `handlers_history.py`, `handlers_nlq.py`.
- **[Backend] `db-service/routes_query.py` — 4 raw exception leaks fixed** — `/query`, `/execute`, `/explain`, `/dry_run` no longer return `f"...{e}"` in HTTP 500 detail. Error logged via `log.error(...)` (operator-visible in journald/SigNoz); `_log_query` still records full error text in `query_history` for audit. Service restarted.

---

## [2.3.0] — 2026-05-07

### Fixed

- **[P0] Schema cap 50 tables removed** — `skeleton.py` and `fn_get_schema` previously truncated the schema mirror to the first 50 tables. Databases with >50 tables (WHMCS, large production DBs) got spurious "Unknown table" errors on tables 51+. Cap removed; all tables now cached.
- **[P0] Schema cache invalidated on connection switch** — `fn_select_connection` now calls `invalidate_schema_cache(ctx)` after switching. Previously, switching from connection A to B kept A's schema in `"schema:active"` for up to 5 minutes, causing write-time validators to reject valid tables/columns on the new connection.
- **[P1] `events.py` `patch_cache_on_dml` — `cache.get` argument order fixed** — args were reversed (`cache.get(ModelClass, key)` instead of `cache.get(key, model=ModelClass)`). Every call raised an exception that was silently caught, making `patch_cache_on_dml` a no-op since v2.0.0. Optimistic row-count delta and `"just now"` sidebar badge now work correctly.
- **[P1] `fn_run_editor_sql` — zero-row DML fail added** — `UPDATE`/`DELETE`/`INSERT`/`REPLACE` that affects 0 rows now returns `ActionResult.error(...)` from the editor panel, consistent with `fn_execute_sql` (chat path). Previously the panel showed `"UPDATE — 0 row(s) affected"` as success.
- **[P1] `fn_delete_connection` — stricter ownership check** — changed `if conn.get("user_id") and conn.get("user_id") != uid` to `if conn.get("user_id", "") != uid`. The old form skipped the check when `user_id` was absent from the document.

### Changed

- **SDK bumped `4.1.2 → 4.1.3`** — pure refactor release (`chat/handler.py` split), no API or behavioral changes.
- **`fn_run_editor_sql` sidebar liveness** — DML row_delta and summary now use `rows_affected_editor` variable consistently through the sidebar liveness block and ActionResult (was using stale `result.get("rows_affected")` after introducing the new variable).
- **`nl_to_sql` schema context cap `30 → 50` tables** — `_build_schema_description` now includes up to 50 tables in the LLM prompt, consistent with the schema cache no longer being truncated.

---

## [2.2.0] — 2026-05-05

### Added

- **`_translate_db_error` in `app.py`** — translates raw MySQL error tuples `(NNNN, 'text')` into human-readable messages for codes 1062 (duplicate key), 1064 (syntax error), 1054 (unknown column), 1146 (table not found), 1451 (FK delete violation), 1452 (FK insert violation), 1406 (data too long).

### Changed

- **`handlers_rows.py`** — `insert_row`, `update_row`, `delete_row` pass backend errors through `_translate_db_error`. E.g. FK 1451 on `delete_row` now reads "Cannot delete: this record is referenced by 'orders'. Remove or reassign the related records there first." instead of raw MySQL tuple.
- **`handlers_execute.py`** — `execute_sql` and `run_editor_sql` errors translated.
- **`handlers_query.py`** — `run_query` syntax errors (1064) translated.
- **`handlers_execute.py`** — EXPLAIN and SELECT branches in `run_editor_sql` also translated (missed in initial pass).

---

## [2.1.0] — 2026-05-05

### Changed

- **SDK upgraded to `imperal-sdk==4.1.2`** — picks up Pydantic feedback-loop (4.1.0), narration schema tightening (4.1.1), and `id_projection` chain dispatch (4.1.2).
- **`id_projection` added to compound-named chain functions** in `handlers_history.py`:
  - `save_query` → `id_projection="connection_id"` (heuristic: `query_id` ✗)
  - `delete_saved` → `id_projection="query_id"` (heuristic: `saved_id` ✗ — field is `query_id`)

---

## [1.5.8] — 2026-04-30 — SDK 3.5.0 pin + nl_to_sql import fix

### Changed

- **`requirements.txt`** — `imperal-sdk==3.4.1` → `imperal-sdk==3.5.0`. SDK 3.5.0
  routes `ExtensionsClient.emit()` through `imperal_kernel.audit.record_action`
  producing an `action_ledger` row per emit for federal observability. Emit signature
  is unchanged; no extension code changes required.

### Fixed (P0 — nl_to_sql broken since 1.5.5)

- **`handlers_nlq.py`** — missing `from schema_guard import load_schema_section`
  import. Every call to `nl_to_sql` raised `NameError: name 'load_schema_section'
  is not defined`, caught by the top-level `except Exception` and returned as
  `ActionResult.error("name 'load_schema_section' is not defined")` — the user
  saw a cryptic error instead of a generated SQL query. Root cause: the 1.5.5
  migration from `ctx.skeleton.get("db_schema")` (raises `SkeletonAccessForbidden`
  from `@chat.function` handlers) to `load_schema_section(ctx)` (reads `ctx.cache`)
  forgot to add the corresponding import.

---

## [1.5.7] — 2026-04-30 — *reconstructed from code*

> Session context was lost due to interrupted sessions (rate-limit + account switch
> during overnight work). This entry is reconstructed from code archaeology.

### Changed

- **`handlers_nlq.py`** — `nl_to_sql` migrated from `ctx.skeleton.get("db_schema")`
  (raises `SkeletonAccessForbidden` from `@chat.function` scope in SDK 1.6.0+) to
  `load_schema_section(ctx)` which reads the `DbSchemaSnapshot` from `ctx.cache`.
  Fallback to live `/schema` fetch remains when cache is cold. Import of
  `load_schema_section` was accidentally omitted — fixed in 1.5.8.

---

## [1.5.6] — 2026-04-30 — *reconstructed from code*

> Session context was lost. Reconstructed from code.

### Fixed

- **`handlers_rows.py`** — `insert_row`, `update_row`, `delete_row` now call
  `validate_table_exists(section, params.table)` before the round-trip to db-service.
  Previously only `validate_columns` was applied; an LLM hallucinating a table name
  would reach the backend and surface a raw MariaDB error instead of the friendly
  recovery hint.
- **`events.py`** — `patch_cache_on_dml` sets `item.last_touched_at = _now_iso()`
  on the matched row so the sidebar `_table_list_item` renders the `"just now"`
  `ui.Badge`. Previously the field was never set; the badge never appeared even
  after successful DML.

---

## [1.5.5] — 2026-04-30 — *reconstructed from code*

> Session context was lost. Reconstructed from code.

### Fixed

- **`panels.py`** — `_table_list_item`: `ui.ListItem` items for tables with a
  fresh `last_touched_at` now render a `ui.Badge("just now", color="blue")` on the
  `badge` slot. The field was introduced by `events.patch_cache_on_dml` in 1.5.6
  (ordering reflects code-archaeology uncertainty on the exact commit sequence).
- **`app.py`** — `TablesPageItem` model gains `last_touched_at: str | None = None`
  field, required by the optimistic-UI badge path in `_table_list_item` and the
  DML patcher in `events.py`.

---

## [1.5.4] — 2026-04-30 — sidebar liveness coverage on every write path

### Added

1.5.3 wired sidebar liveness only for `fn_run_editor_sql` (the editor
"Run" button). Chat-side writes (`execute_sql`) and panel row-form
writes (`insert_row` / `update_row` / `delete_row`) executed
successfully on the database but did NOT update the sidebar — the user
saw the table list go stale until the 5-min cache TTL expired.

This release wires the same optimistic-patch + emit step into all four
write entry points:

- **`handlers_execute.fn_execute_sql`** — chat-LLM-invoked execute. Same
  classify_event_kind branch as `run_editor_sql`: DDL → invalidate
  cache + emit `sql.ddl_executed`; DML → patch + emit `table.touched`.
- **`handlers_rows.{fn_insert_row, fn_update_row, fn_delete_row}`** —
  row CRUD via the panel form. Each calls a shared
  `_bump_sidebar_for_dml(ctx, conn, conn_id, table, kind, row_delta)`
  helper that runs `patch_cache_on_dml` + emits `table.touched`.

### Note

Chat write-path was never broken — `execute_sql` and the row CRUD
handlers continued to call db-service `/execute` and `/row` exactly as
before, the database side worked correctly. The visible defect was UI
freshness only: sidebar didn't reflect a chat-side write until cache
TTL expired. 1.5.4 closes that gap so the badge + row-count update lands
the moment the chat function returns success, regardless of which entry
point the user used.

---

## [1.5.3] — 2026-04-30 — kernel `@ext.on_event` ctx=None workaround

### Fixed (P0 — sidebar stuck on "Indexing schema…" forever)

The 1.5.x design relied on three `@ext.on_event` handlers
(`schema.refresh.requested`, `sql.ddl_executed`, `table.touched`) to do
the cache-mutation work. The Imperal kernel's
`imperal_kernel.services.rule_engine.evaluate_event` dispatches them
with literally `await handler_func(None, event_obj)` — i.e. the SDK
contract leaks no per-user `ctx` into event handlers on the live
platform. Every `ctx.cache.set` / `ctx.cache.delete` inside an event
handler raises `AttributeError: 'NoneType' object has no attribute
'cache'`, the kernel swallows + logs, and the cache never gets
populated. So the panel's cold-cache placeholder ("Indexing schema…")
stayed forever — small DBs, large DBs, every user.

### Changed

- **`panels.py`** — cold-cache populator moved INLINE into
  `_render_schema_block` (now: `_populate_inline`). On a cache miss the
  panel calls `_api_catalog` + `_api_tables_page` directly, writes both
  envelopes to `ctx.cache`, and renders with data on the same paint.
  Bounded by db-service's 5 s `MAX_STATEMENT_TIME` per session, so worst
  case the panel paints with a real "Schema unavailable" error in
  ~5–8 s rather than spinning forever. Warm-cache renders stay
  cache-only sub-millisecond.
- **`events.py`** — `@ext.on_event` decorators removed; the file now
  exports plain async helpers (`patch_cache_on_dml`,
  `invalidate_cache_on_ddl`). The module docstring documents the
  kernel-contract gap as the reason.
- **`handlers_execute.py`** — `fn_run_editor_sql` calls those helpers
  inline after a successful execute (live ctx is available there). The
  `ctx.events.emit("...")` calls remain — the panel's
  `refresh="on_event:..."` attribute hooks Redis pub/sub via the
  kernel's panel re-render dispatch, which works regardless of whether
  `@ext.on_event` Python handlers ran.

### Architectural note

The Phase 2 spec's optimistic-UI + DDL-invalidation contract is
preserved: same cache shapes, same emit names, same panel refresh
semantics. Only the implementation moved from `@ext.on_event` (broken
on this kernel) to inline call-site work. When the kernel grows a
ctx-aware on_event dispatch (`handler_func(ctx, event_obj)`), the
helpers in `events.py` can move back behind decorators with no
call-site change.

---

## [1.5.2] — 2026-04-30

### Fixed (P0 — Developer Portal validator caught these on 1.5.1 deploy)

- **`panels.py`** — `ui.List(... search=True)` → `searchable=True`. Wrong
  kwarg name; SDK 3.4.x `ui.List` accepts `searchable` (per
  `imperal_sdk.ui.data.List` signature: `bulk_actions, extra_info,
  grouped_by, items, on_end_reached, page_size, searchable, selectable,
  total_items`). The render call would raise `TypeError` on the first
  warm-cache paint.
- **`panels.py`** — `ui.ListItem(... className="pulse")` removed.
  `className` is not a valid `ui.ListItem` kwarg (the SDK whitelist is:
  `actions, avatar, badge, draggable, droppable, expandable,
  expanded_content, icon, id, meta, on_click, on_drop, selected,
  subtitle, title`). Replaced the would-be CSS pulse animation with a
  `ui.Badge("just now", color="blue")` on the `badge` slot — same
  semantic intent (signal a freshly-touched row), uses a real DUI
  primitive. Long-form follow-up: when SDK exposes a `className` /
  per-item style hook on `ListItem`, swap back to a fading CSS
  animation. For now the badge is the contract.

---

## [1.5.1] — 2026-04-30

### Fixed (P0)

- **`app.py`** — restore the three new `@ext.cache_model` envelopes and the
  cache-key builder helpers that 1.5.0 relied on but did not actually
  contain. A Nextcloud sync conflict overwrote the additions to `app.py`
  between local edit and `git push`, so 1.5.0 deployed with `events.py`
  importing names (`CatalogCache`, `TablesPageCache`, `TableDetailCache`,
  `CatalogDb`, `TablesPageItem`, `cache_key_catalog`, `cache_key_tables_page`,
  `cache_key_table_detail`, `CATALOG_CACHE_TTL`, `TABLES_PAGE_CACHE_TTL`,
  `SIDEBAR_PAGE_LIMIT`) that did not exist on the deployed `app.py`.
  Worker logged `cannot import name 'CatalogCache' from 'app'` on every
  load attempt — extension was effectively offline since deploy. Sidebar
  loaded nothing for everyone, including small-DB users (Дмитрий's own
  panel was the first to surface this).
- HTTP helpers (`_api_catalog`, `_api_tables_page`, `_api_table_detail`,
  `_api_exact_count`) survived the conflict; only the cache-models block
  was lost.

No functional change vs the 1.5.0 design — this restores the file to the
intended state. All py_compile + symbol-presence checks now pass.

---

## [1.5.0] — 2026-04-30 — sql-db-scale Phase 2 (sidebar liveness foundation)

Sidebar render time is now O(1) in target-DB size. The previous render path
synchronously fetched the full schema from db-service inside the panel
decorator on every event — a 10–30 s freeze on a WHMCS-snapshot (hundreds
of tables, multi-million-row activity logs). Phase 2 moves all schema data
behind a typed cache, splits the event taxonomy DDL-vs-DML, and introduces
optimistic-UI patching so a successful editor `INSERT`/`UPDATE`/`DELETE`
updates the sidebar without any HTTP round-trip.

Backend prerequisite (already deployed): db-service v1.3.0 with the four
new schema tiers (T0 catalog / T1 tables-page / T2 table-detail / T3
exact-count). Legacy `/v1/connections/{id}/schema` remains mounted as a
compat shim and no longer runs a `SELECT COUNT(*)` per table — it now
composes T0+T1 internally and returns row estimates from
`information_schema.TABLES.TABLE_ROWS`.

Architecture spec: `Dimasickky-Extensions/extensions/sql-db-scale.md`.

### Added

- **`events.py`** — three `@ext.on_event` handlers driving sidebar
  liveness. `schema.refresh.requested` populates `CatalogCache` +
  `TablesPageCache` via T0+T1 off the panel render path, then emits
  `schema.indexed` to re-render. `sql.ddl_executed` invalidates catalog +
  first-page caches and re-fires `schema.refresh.requested`.
  `table.touched` performs an **optimistic local patch** on the cached
  `TablesPageCache`: bumps `rows_estimate` by the affected delta, sets
  `last_touched_at` for the UI pulse — no HTTP fetch.
- **`app.py`** — three new `@ext.cache_model` envelopes alongside the
  existing `DbSchemaSnapshot`: `CatalogCache` (databases on a
  connection), `TablesPageCache` (paginated table list, ≤200 items per
  envelope to fit the SDK 64 KB cap), `TableDetailCache` (columns +
  indexes + FKs for one table). Cache-key builders (`cache_key_catalog`,
  `cache_key_tables_page`, `cache_key_table_detail`) live in `app.py` as
  the single source of truth — both `panels.py` and `events.py` use them.
- **`app.py`** — four HTTP helpers wrapping db-service v1.3.0 tiered
  routes: `_api_catalog`, `_api_tables_page`, `_api_table_detail`,
  `_api_exact_count`.
- **`sql_parser.py`** — `classify_event_kind(sql)` returns
  `(class, subkind, target_table)` where class ∈ {ddl, dml, read,
  explain, other}. Used by `fn_run_editor_sql` to pick the right event.

### Changed

- **`panels.py`** — full rewrite. The sidebar render path no longer awaits
  any HTTP call. Two `ctx.cache.get` reads (catalog + first tables page);
  on miss, render an "Indexing schema…" placeholder and emit
  `schema.refresh.requested`. The DDL/DML event split lets DML happen
  without re-running schema introspection — only structural changes
  trigger a refetch. The schema tree uses `ui.List(page_size=50,
  search=True)` for built-in pagination + filter, which stays smooth on
  50 k-table catalogs.
- **`panels.py`** — `refresh=` attribute pares down to the events that
  actually require a re-render: `connection.added`, `connection.deleted`,
  `connection.selected`, `sql.ddl_executed`, `table.touched`,
  `schema.indexed`. Removed `row.inserted`, `row.updated`, `row.deleted`,
  `sql.executed` — those classes of event do not change the schema and
  the sidebar handles them via the optimistic patch path instead of a
  full re-render.
- **`handlers_execute.py`** — `fn_run_editor_sql` now classifies the
  successfully-executed statement and emits `sql.ddl_executed` (DDL
  path) or `table.touched` (DML path) with `kind`, `target_table`,
  `row_delta`. Read paths emit nothing (they don't change anything the
  sidebar should react to). Failures in the emit path are logged and
  swallowed — they MUST NOT mask a successful execute from the user.
- **`main.py`** — imports `events` so the new `@ext.on_event` handlers
  register at boot.

### Removed

- The synchronous `_api_post("/v1/connections/{id}/schema", …)` call from
  the body of `@ext.panel("sidebar")`. This is the architectural
  invariant of the sql-db-scale spec: the panel render path NEVER awaits
  an HTTP call to db-service. (The legacy endpoint itself stays mounted
  for backwards-compat with any extension still on 1.4.x; this codebase
  no longer reaches for it.)

### Compatibility

- SDK pin unchanged (`imperal-sdk==3.4.1`). All used primitives
  (`ctx.cache`, `@ext.cache_model`, `ctx.events.emit`, `@ext.on_event`,
  `ui.List(page_size=, search=True)`) exist in 3.4.1; no kernel ask.
- Wire-contract change vs db-service is **additive**: the four new
  endpoints sit at `/v1/connections/{id}/{catalog,tables,tables/{n}/detail,tables/{n}/count}`.
  Legacy `/v1/connections/{id}/schema` continues to serve and is still
  the source of truth for the chat-handler skeleton mirror until the
  Phase 6 lazy-skeleton work lands.
- Existing `DbSchemaSnapshot` mirror (the cache snapshot read by
  `schema_guard.load_schema_section`) is unchanged on this release —
  chat-side write validation continues to work exactly as before.

---

## [1.4.3] — 2026-04-29

### Changed

- **`requirements.txt`** — bump `imperal-sdk==3.0.0` → `==3.4.1`. Pulls in the LLM-FU-1/FU-2 stack (gpt-5 / o-series `max_completion_tokens` rename + `temperature` drop) so chains routed through reasoning models stop falling over to `anthropic/haiku`. No source changes — extension code already complies with the 3.x surface (3.3.0 `ChatExtension(model=)` removal done in 1.4.2; 3.4.0 panel-slot whitelist already met by `panels.py` `slot="left"` + `panels_editor.py` `slot="center"`).

---

## [1.4.2] — 2026-04-29

Architecture audit pass: P0/P1 findings on top of the 1.4.1 LLM-input hardening.

### Fixed (P0)

- **`handlers_execute.py`** — `fn_run_editor_sql` body now wrapped in `try/except → ActionResult.error`. Previously any `httpx.ConnectError` / `KeyError` / unexpected backend payload from `_resolve` / `build_conn_info` propagated as an unhandled exception (every other handler in the file already had the wrapper).
- **`handlers_nlq.py`** — `fn_nl_to_sql` no longer calls `ctx.skeleton.get("db_schema")` from a chat-typed tool. SDK raises `SkeletonAccessForbidden` when `ctx.skeleton.get` is invoked outside an `@ext.skeleton` tool, so the path was effectively dead and always fell through to a fresh `/schema` round-trip. Replaced with `load_schema_section(ctx)` (cache-backed, same source the skeleton refresher writes to). One round-trip saved on every `nl_to_sql` call when schema is warm.

### Fixed (P1)

- **`handlers_connections.py`** — `fn_delete_connection` now calls `require_user_id(ctx)` and verifies `conn.user_id == uid` before delete. Previously the only ownership filter relied on `get_connection_by_id`'s scope; tightened in case that helper ever broadens.
- **`handlers_execute.py`** — `fn_run_editor_sql` status check standardised to `!= "ok"` (was `== "error"`). A backend reply like `{"status": "degraded", ...}` or one missing `status` would have been silently treated as success.

### Fixed (P2)

- **`app.py`** — bare `except: pass` in `resolve_connection` replaced with `log.warning(..., exc)` on both the active-flag query and the fallback query, per Dimasickky enterprise quality bar.
- **`app.py`** — `ChatExtension(model="claude-haiku-4-5-20251001")` removed (deprecated since SDK 3.3.0). LLM model resolution is now kernel ctx-injection (`ctx._llm_configs`); the param will hard-error in SDK 4.0.
- **`main.py`** — stale module docstring `"sql-db v1.2.1 · …"` replaced with `"sql-db · entrypoint."` so version is sourced from one place (`Extension(version=…)`).

### Compatibility

- SDK pin unchanged (`imperal-sdk==3.0.0`). 3.4.0 panel-slot validator (`slot="main"` → ValueError) does not affect this extension — both panels (`panels.py` sidebar `slot="left"`, `panels_editor.py` editor `slot="center"`) use explicit slot values that match the new whitelist.
- Wire contract with `db-service:8099` unchanged.

---

## [1.4.1] — 2026-04-29

LLM tool-input robustness: every `@chat.function` params model now accepts the synonyms an LLM is most likely to emit, so a user request like “Username: X, server: Y, db: Z” no longer trips `VALIDATION_MISSING_FIELD` from raw Pydantic into chat.

### Why

`AddConnectionParams` declared canonical names `db_user` / `host` / `database` only. Sonnet/Haiku tool-use generation often picks `username` / `server` / `db` instead, and the missing-field error leaked unwrapped into the chat (against the Dimasickky enterprise quality bar — no internal error should reach the user). Same risk existed across every other LLM-input model in the extension.

### Changed

- **`handlers_connections.py`** — `AddConnectionParams`, `UpdateConnectionParams`, `ConnectionIdParams`, `SelectConnectionParams`, `ResolveConnByDbParams`: `validation_alias=AliasChoices(...)` on every LLM-facing field, `model_config = ConfigDict(populate_by_name=True)`. `name` made optional with derive-on-empty fallback (`<host_short>_<db_or_user>`).
- **`handlers_query.py`** — `RunQueryParams`, `ExplainParams`, `DryRunParams`, `GetSchemaParams`: aliases on `sql`, `connection_id`, `database`.
- **`handlers_execute.py`** — `ExecuteSqlParams`, `RunEditorSqlParams`: aliases on `sql`, `connection_id`.
- **`handlers_rows.py`** — `InsertRowParams`, `UpdateRowParams`, `DeleteRowParams`: aliases on `table`, `values_json`, `pk_col`, `pk_value`, `connection_id`.
- **`handlers_history.py`** — `ListHistoryParams`, `SaveQueryParams`, `ListSavedParams`, `RunSavedParams`, `DeleteSavedParams`: aliases on `connection_id`, `query_id`, `sql_text`, `name`, `description`.
- **`handlers_nlq.py`** — `NlToSqlParams`: aliases on `question`, `connection_id`.
- **`imperal.json`**, **`app.py`** — version bump 1.4.0 → 1.4.1.

### Architecture note

`AliasChoices` is applied **only** to LLM-input models (those bound to `@chat.function` params). Internal models (`PulseParams`), wire contracts to `db-service:8099`, and storage payloads remain strict — the LLM-tolerance is contained at the chat boundary.

### Not changed

- SDK pin (`imperal-sdk==3.0.0`), backend wire contract, manifest tools list, panels, system_prompt, identity reads.

---

## [1.4.0] — 2026-04-27

SDK migration: `imperal-sdk==2.0.1` → `imperal-sdk==3.0.0` (Identity Contract Unification, W1).

### Why

SDK 3.0.0 deletes `imperal_sdk.auth.user.User`, makes `User`/`UserContext` frozen Pydantic v2 models with `extra="forbid"`, and renames `.id` → `.imperal_id` on user objects. `ctx.user.id` raises `AttributeError` on 3.x with no alias. Production worker venv was upgraded to 3.0.0 — any 2.x-pinned extension breaks on identity reads.

### Changed

- **`app.py`** — `_user_id(ctx)` reads `ctx.user.imperal_id` instead of `ctx.user.id`. `require_user_id` docstring updated.
- **`requirements.txt`** — `imperal-sdk==2.0.1` → `imperal-sdk==3.0.0`. Equality pin retained as the workspace invariant.

### Not changed

- All other Python source, manifest, system_prompt, panels, handlers — byte-for-byte identical to 1.3.5.

---

## [1.3.5] — 2026-04-26

Pin bump only: `imperal-sdk==1.6.2` → `imperal-sdk==2.0.1`. No source changes.

### Why

`imperal-sdk` 2.0.1 supersedes the rolled-back 2.0.0 with the v1.6.2 contract restored plus two kernel-internal ICNLI Action Authority hotfixes (`chat/guards.py` destructive `BLOCK` → `ESCALATE`, `core/intent.action_plan.args` JSON-encoded string for OpenAI strict mode). The SDK API surface remains identical to 1.6.2. Per Valentin's release note: *"v1.6.2 extensions upgrade by pin bump only."*

### Changed

- **`requirements.txt`** — `imperal-sdk==1.6.2` → `imperal-sdk==2.0.1`. Equality pin retained as the workspace invariant.

### Not changed

- All Python source — `app.py`, `handlers_*.py`, `schema_guard.py`, `skeleton.py`, `sql_parser.py`, `system_prompt.txt`, `imperal.json` tool definitions — byte-for-byte identical to 1.3.4. The 1.3.4 `cache_model` registration fix and the 1.3.3 schema-cache migration both stand.

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
