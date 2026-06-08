"""sql-db · DML/DDL execution handler + universal editor runner."""

import logging

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

log = logging.getLogger("sql-db")

from app import chat, ActionResult, _api_post, require_user_id, build_conn_info, _translate_db_error
from models_return import *  # noqa: F401,F403 — data_model DTOs
from handlers_query import _resolve, RunQueryParams, ExplainParams  # noqa: F401
from schema_guard import (
    load_schema_section,
    list_known_tables,
    validate_columns,
    invalidate as invalidate_schema_cache,
)
from sql_parser import (
    extract_target_tables,
    extract_insert_columns,
    extract_update_columns,
    classify_event_kind,
)
# Bind sidebar-liveness helpers at module load time. They were previously
# imported lazily inside the handler; load-time binding avoids a module
# re-import edge case so the optimistic sidebar update never silently skips.
from events import patch_cache_on_dml, invalidate_cache_on_ddl


# ─── Models ───────────────────────────────────────────────────────────── #
# LLM-input — see handlers_connections.py for AliasChoices rationale.

_SQL_ALIASES = AliasChoices("sql", "query", "statement", "sql_text", "text")
_CONN_ALIASES = AliasChoices("connection_id", "conn_id", "connection")


class ExecuteSqlParams(BaseModel):
    """Execute a DML/DDL statement."""
    model_config = ConfigDict(populate_by_name=True)

    sql: str = Field(
        validation_alias=_SQL_ALIASES,
        description="SQL statement (INSERT/UPDATE/DELETE/ALTER/CREATE/DROP)",
    )
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class RunEditorSqlParams(BaseModel):
    """Run any SQL from the editor — auto-detects type."""
    model_config = ConfigDict(populate_by_name=True)

    sql: str = Field(validation_alias=_SQL_ALIASES, description="SQL statement")
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


# ─── Handler ──────────────────────────────────────────────────────────── #

_WRITE_VERBS_AFFECTING_ROWS = {"INSERT", "UPDATE", "DELETE", "REPLACE"}
_DDL_VERBS = {"CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME"}


def _first_word(sql: str) -> str:
    return sql.split()[0].upper() if sql else ""


@chat.function(
    "execute_sql", action_type="destructive", chain_callable=True,
    effects=["execute:sql"], event="sql.executed",
    data_model=SqlExecuteResult,
    description=(
        "Execute a write statement (INSERT, UPDATE, DELETE, REPLACE, ALTER, "
        "CREATE, DROP, TRUNCATE). Use this for all database mutations "
        "including automation-triggered inserts."
    ),
)
async def fn_execute_sql(ctx, params: ExecuteSqlParams) -> ActionResult:
    """Execute a DML/DDL statement.

    Automation `tool_call` steps flow through platform's `the direct-call path`
    and arrive bound to the Pydantic model
    like any chat tool use — no `_direct_params` fallback needed.
    """
    try:
        sql = (params.sql or "").strip().rstrip(";")
        conn_id_in = params.connection_id or ""

        if not sql:
            return ActionResult.error(
                "execute_sql: sql parameter is empty"
            )

        # Schema gate (table + column level). Snapshot is sourced from
        # ctx.cache (mirrored by skeleton.py); cold cache → all validators
        # return None → we defer to the backend without rejecting.
        section = await load_schema_section(ctx)
        known = list_known_tables(section)
        verb = _first_word(sql)

        # 1) Table-level: reject obvious typos before round-trip. DDL is
        #    table-creating, so we DO NOT reject CREATE/ALTER on unknown
        #    tables — the whole point is they are about to exist.
        if known and verb not in _DDL_VERBS:
            targets = extract_target_tables(sql)
            missing = [t for t in targets if t not in known]
            if missing:
                return ActionResult.error(
                    f"Unknown table(s) referenced: {', '.join(missing)}. "
                    f"Known tables: {', '.join(known)}. "
                    "Call get_schema() to refresh, then retry."
                )

        # 2) Column-level for INSERT / UPDATE — closes the 1054 hallucination
        #    path. Skipped on positional INSERT (no col list) and on shapes
        #    the parser can't isolate cleanly (extractor returns []).
        if verb in ("INSERT", "UPDATE") and known:
            targets = extract_target_tables(sql)
            if targets:
                table = targets[0]
                cols = (
                    extract_insert_columns(sql) if verb == "INSERT"
                    else extract_update_columns(sql)
                )
                if cols:
                    if (c_err := validate_columns(section, table, cols)):
                        return ActionResult.error(
                            f"{c_err} Call get_schema('{table}') and retry "
                            "with the correct columns."
                        )

        conn, conn_id = await _resolve(ctx, conn_id_in)
        if not conn:
            return ActionResult.error(
                f"No connection resolved (connection_id='{params.connection_id}')."
            )

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/execute", {
            "user_id": require_user_id(ctx),
            "sql": sql,
            "confirmed": True,
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(_translate_db_error(result.get("detail", "Execution failed")))

        rows_affected = int(result.get("rows_affected", 0) or 0)
        query_type = (result.get("query_type") or "").upper()

        # Loud fail for automation-path zero-row writes: the platform normalizes
        # ActionResult.success into status=ok and reports steps=1 failed=0 even
        # when INSERT/UPDATE/DELETE affected no rows. Surface that as error so
        # rules don't report phantom success.
        if rows_affected == 0 and query_type in _WRITE_VERBS_AFFECTING_ROWS:
            return ActionResult.error(
                f"{query_type} executed but 0 rows affected — "
                f"check VALUES list or WHERE clause"
            )

        # DDL changed the schema shape — the cached snapshot is now stale.
        # Drop it so the next write either sees a fresh skeleton refresh or
        # cold-cache-skips validation (vs. rejecting on stale columns). The
        # next @ext.skeleton tick will repopulate cache with the new shape.
        if query_type in _DDL_VERBS:
            await invalidate_schema_cache(ctx)

        # Phase 2 sidebar liveness — same wiring as fn_run_editor_sql.
        # Inline cache mutation (platform @ext.on_event has ctx=None on this
        # platform) + emit() for panel re-render. Best-effort — never
        # mask a successful execute.
        try:
            klass, subkind, target = classify_event_kind(sql)
            database = conn.get("database", "")
            if klass == "ddl":
                await invalidate_cache_on_ddl(
                    ctx, conn_id=conn_id, database=database, target_table=target,
                )
                await ctx.events.emit("sql.ddl_executed", {
                    "conn_id": conn_id, "database": database,
                    "kind": subkind, "target_table": target,
                })
            elif klass == "dml" and target:
                await patch_cache_on_dml(
                    ctx, conn_id=conn_id, database=database, table=target,
                    kind=subkind, row_delta=rows_affected,
                )
                await ctx.events.emit("table.touched", {
                    "conn_id": conn_id, "database": database,
                    "table": target, "kind": subkind, "row_delta": rows_affected,
                })
        except Exception as exc:
            log.warning("sidebar liveness step failed (non-fatal): %s", exc)

        return ActionResult.success(
            data={
                "rows_affected": rows_affected,
                "query_type": result.get("query_type", ""),
                "tables": result.get("tables", []),
                "exec_ms": result.get("exec_ms", 0),
            },
            summary=f"{result.get('query_type', 'SQL')} — {rows_affected} row(s) affected",
        )
    except Exception as e:
        log.error("execute_sql: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


@chat.function(
    "run_editor_sql", action_type="write", chain_callable=True,
    effects=["execute:sql"], event="sql.executed",
    data_model=RunEditorSqlResult,
    description="Run any SQL from the editor. Auto-detects: SELECT goes to query, DML/DDL goes to execute.",
)
async def fn_run_editor_sql(ctx, params: RunEditorSqlParams) -> ActionResult:
    """Universal SQL runner for the editor panel."""
    try:
        sql = params.sql.strip().rstrip(";")
        if not sql:
            return ActionResult.error("Empty SQL")

        first_word = sql.split()[0].upper()
        is_read = first_word in ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        if first_word == "EXPLAIN":
            inner_sql = sql[len("EXPLAIN"):].strip()
            if not inner_sql:
                return ActionResult.error("EXPLAIN requires a query after it.")
            result = await _api_post(ctx, f"/v1/connections/{conn_id}/explain", {
                "user_id": require_user_id(ctx),
                "sql": inner_sql,
                "connection": build_conn_info(conn),
            })
            if result.get("status") != "ok":
                return ActionResult.error(_translate_db_error(result.get("detail", "EXPLAIN failed")))
            return ActionResult.success(
                data={"plan": result.get("plan", []), "sql": inner_sql},
                summary="EXPLAIN plan",
            )

        if is_read:
            result = await _api_post(ctx, f"/v1/connections/{conn_id}/query", {
                "user_id": require_user_id(ctx),
                "sql": sql,
                "limit": 100,
                "connection": build_conn_info(conn),
            })
            if result.get("status") != "ok":
                return ActionResult.error(_translate_db_error(result.get("detail", "Query failed")))
            return ActionResult.success(
                data={
                    "columns": result.get("columns", []),
                    "rows": result.get("rows", []),
                    "total_rows": result.get("total_rows", 0),
                    "exec_ms": result.get("exec_ms", 0),
                },
                summary=f"{result.get('total_rows', 0)} row(s) in {result.get('exec_ms', 0)}ms",
            )

        # DML/DDL
        result = await _api_post(ctx, f"/v1/connections/{conn_id}/execute", {
            "user_id": require_user_id(ctx),
            "sql": sql,
            "confirmed": True,
            "connection": build_conn_info(conn),
        })
        if result.get("status") != "ok":
            return ActionResult.error(_translate_db_error(result.get("detail", "Execution failed")))

        rows_affected_editor = int(result.get("rows_affected", 0) or 0)
        query_type_editor = (result.get("query_type") or first_word).upper()
        if rows_affected_editor == 0 and query_type_editor in _WRITE_VERBS_AFFECTING_ROWS:
            return ActionResult.error(
                f"{query_type_editor} executed but 0 rows affected — "
                "check VALUES list or WHERE clause"
            )

        # Phase 2 sidebar liveness — classify the executed statement, then:
        #   - mutate ctx.cache HERE inline (we have the live ctx; the
        #     platform's @ext.on_event dispatch passes ctx=None so cache
        #     writes from event handlers don't work on this platform),
        #   - fire the corresponding ctx.events.emit so the panel's
        #     refresh="on_event:..." re-renders. The Redis pub/sub →
        #     panel re-render path is independent of @ext.on_event Python
        #     handlers, so the panel still updates after this completes.
        # All best-effort: never mask a successful execute.
        try:
            klass, subkind, target = classify_event_kind(sql)
            affected = int(result.get("rows_affected") or 0)
            database = conn.get("database", "")
            if klass == "ddl":
                await invalidate_cache_on_ddl(
                    ctx, conn_id=conn_id, database=database,
                    target_table=target,
                )
                await ctx.events.emit("sql.ddl_executed", {
                    "conn_id": conn_id, "database": database,
                    "kind": subkind, "target_table": target,
                })
            elif klass == "dml" and target:
                await patch_cache_on_dml(
                    ctx, conn_id=conn_id, database=database, table=target,
                    kind=subkind, row_delta=rows_affected_editor,
                )
                await ctx.events.emit("table.touched", {
                    "conn_id": conn_id, "database": database,
                    "table": target, "kind": subkind, "row_delta": rows_affected_editor,
                })
        except Exception as exc:
            log.warning("sidebar liveness step failed (non-fatal): %s", exc)

        return ActionResult.success(
            data={
                "rows_affected": rows_affected_editor,
                "query_type": result.get("query_type", first_word),
                "tables": result.get("tables", []),
                "exec_ms": result.get("exec_ms", 0),
            },
            summary=f"{first_word} — {rows_affected_editor} row(s) affected",
        )
    except Exception as e:
        log.error("run_editor_sql: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


# ─── execute_batch — multiple statements in ONE transaction ──────────────── #

class ExecuteBatchParams(BaseModel):
    """Run MULTIPLE SQL statements together (e.g. CREATE a table AND seed it)."""
    model_config = ConfigDict(populate_by_name=True)

    sql: str = Field(
        validation_alias=_SQL_ALIASES,
        description=(
            "A SQL script with MULTIPLE statements separated by ';' "
            "(e.g. 'CREATE TABLE ...; INSERT ...; CREATE TABLE ...;'). "
            "Runs sequentially in one transaction."
        ),
    )
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


@chat.function(
    "execute_batch", action_type="destructive", chain_callable=True,
    effects=["execute:sql"], event="sql.executed",
    data_model=BatchExecuteResult,
    description=(
        "Run MULTIPLE SQL statements at once, sequentially, in ONE transaction. "
        "Use this when the user asks to create a table AND fill it, create several "
        "tables, or run a multi-statement script — instead of multiple execute_sql "
        "calls (execute_sql rejects multi-statement). Pass the whole script in `sql` "
        "(statements separated by ';'). Note: DDL (CREATE/ALTER/DROP) auto-commits "
        "and cannot be rolled back; DML is transactional."
    ),
)
async def fn_execute_batch(ctx, params: ExecuteBatchParams) -> ActionResult:
    """Split a multi-statement script and run it as one transactional batch."""
    try:
        sql = (params.sql or "").strip()
        if not sql:
            return ActionResult.error("execute_batch: sql parameter is empty")

        conn, conn_id = await _resolve(ctx, params.connection_id or "")
        if not conn:
            return ActionResult.error(
                f"No connection resolved (connection_id='{params.connection_id}')."
            )

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/execute_batch", {
            "user_id": require_user_id(ctx),
            "sql": sql,
            "confirmed": True,
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(_translate_db_error(result.get("detail", "Batch execution failed")))

        stmts = result.get("statements", []) or []

        # DDL changed the schema shape — drop the cached snapshot so the next
        # write re-reads a fresh skeleton (same rationale as execute_sql).
        if any((s.get("query_type") or "").upper() in _DDL_VERBS for s in stmts):
            await invalidate_schema_cache(ctx)

        # Best-effort sidebar liveness — a batch touched schema/data.
        try:
            await ctx.events.emit("sql.ddl_executed", {
                "conn_id": conn_id, "database": conn.get("database", ""),
                "kind": "batch", "target_table": None,
            })
        except Exception as exc:
            log.warning("execute_batch sidebar emit failed (non-fatal): %s", exc)

        n = result.get("statements_executed", len(stmts))
        rows = result.get("rows_affected", 0)
        return ActionResult.success(
            data={
                "statements_executed": n,
                "rows_affected": rows,
                "exec_ms": result.get("exec_ms", 0),
                "statements": stmts,
            },
            summary=f"Batch: {n} statement(s) executed, {rows} row(s) affected.",
        )
    except Exception as e:
        log.error("execute_batch: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)
