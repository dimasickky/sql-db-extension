"""sql-db · DML/DDL execution handler + universal editor runner."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import chat, ActionResult, _api_post, _user_id, build_conn_info
from handlers_query import _resolve, RunQueryParams, ExplainParams  # noqa: F401
from schema_guard import list_known_tables
from sql_parser import extract_target_tables


# ─── Models ───────────────────────────────────────────────────────────── #

class ExecuteSqlParams(BaseModel):
    """Execute a DML/DDL statement."""
    sql: str = Field(description="SQL statement (INSERT/UPDATE/DELETE/ALTER/CREATE/DROP)")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class RunEditorSqlParams(BaseModel):
    """Run any SQL from the editor — auto-detects type."""
    sql: str = Field(description="SQL statement")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


# ─── Handler ──────────────────────────────────────────────────────────── #

_WRITE_VERBS_AFFECTING_ROWS = {"INSERT", "UPDATE", "DELETE", "REPLACE"}


@chat.function(
    "execute_sql", action_type="destructive", event="sql.executed",
    description=(
        "Execute a write statement (INSERT, UPDATE, DELETE, REPLACE, ALTER, "
        "CREATE, DROP, TRUNCATE). Use this for all database mutations "
        "including automation-triggered inserts."
    ),
)
async def fn_execute_sql(ctx, params: ExecuteSqlParams) -> ActionResult:
    """Execute a DML/DDL statement.

    Automation `tool_call` steps flow through kernel's `_call_function_direct`
    (session 42 fix, I-AUTO-TOOL-CALL) and arrive bound to the Pydantic model
    like any chat tool use — no `_direct_params` fallback needed.
    """
    try:
        sql = (params.sql or "").strip().rstrip(";")
        conn_id_in = params.connection_id or ""

        if not sql:
            return ActionResult.error(
                "execute_sql: sql parameter is empty"
            )

        # Light schema gate: if skeleton knows a concrete list of tables and
        # we can extract a single target table that isn't in the list, fail
        # fast. Subqueries / CTEs / DDL ambiguity → extractor returns [] →
        # we skip the gate (don't over-reject).
        known = list_known_tables(ctx)
        if known:
            targets = extract_target_tables(sql)
            missing = [t for t in targets if t not in known]
            if missing:
                return ActionResult.error(
                    f"Unknown table(s) referenced: {', '.join(missing)}. "
                    f"Known tables: {', '.join(known)}."
                )

        conn, conn_id = await _resolve(ctx, conn_id_in)
        if not conn:
            return ActionResult.error(
                f"No connection resolved (connection_id='{params.connection_id}')."
            )

        result = await _api_post(f"/v1/connections/{conn_id}/execute", {
            "user_id": _user_id(ctx),
            "sql": sql,
            "confirmed": True,
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(result.get("detail", "Execution failed"))

        rows_affected = int(result.get("rows_affected", 0) or 0)
        query_type = (result.get("query_type") or "").upper()

        # Loud fail for automation-path zero-row writes: the kernel normalizes
        # ActionResult.success into status=ok and reports steps=1 failed=0 even
        # when INSERT/UPDATE/DELETE affected no rows. Surface that as error so
        # rules don't report phantom success.
        if rows_affected == 0 and query_type in _WRITE_VERBS_AFFECTING_ROWS:
            return ActionResult.error(
                f"{query_type} executed but 0 rows affected — "
                f"check VALUES list or WHERE clause"
            )

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
        return ActionResult.error(str(e))


@chat.function(
    "run_editor_sql", action_type="write", event="sql.executed",
    description="Run any SQL from the editor. Auto-detects: SELECT goes to query, DML/DDL goes to execute.",
)
async def fn_run_editor_sql(ctx, params: RunEditorSqlParams) -> ActionResult:
    """Universal SQL runner for the editor panel."""
    sql = params.sql.strip().rstrip(";")
    if not sql:
        return ActionResult.error("Empty SQL")

    first_word = sql.split()[0].upper()
    is_read = first_word in ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")

    conn, conn_id = await _resolve(ctx, params.connection_id)
    if not conn:
        return ActionResult.error("No active connection.")

    if first_word == "EXPLAIN":
        # Strip EXPLAIN prefix and run explain
        inner_sql = sql[len("EXPLAIN"):].strip()
        if not inner_sql:
            return ActionResult.error("EXPLAIN requires a query after it.")
        result = await _api_post(f"/v1/connections/{conn_id}/explain", {
            "user_id": _user_id(ctx),
            "sql": inner_sql,
            "connection": build_conn_info(conn),
        })
        if result.get("status") == "error":
            return ActionResult.error(result.get("detail", "EXPLAIN failed"))
        return ActionResult.success(
            data={"plan": result.get("plan", []), "sql": inner_sql},
            summary="EXPLAIN plan",
        )

    if is_read:
        result = await _api_post(f"/v1/connections/{conn_id}/query", {
            "user_id": _user_id(ctx),
            "sql": sql,
            "limit": 100,
            "connection": build_conn_info(conn),
        })
        if result.get("status") == "error":
            return ActionResult.error(result.get("detail", "Query failed"))
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
    result = await _api_post(f"/v1/connections/{conn_id}/execute", {
        "user_id": _user_id(ctx),
        "sql": sql,
        "confirmed": True,
        "connection": build_conn_info(conn),
    })
    if result.get("status") == "error":
        return ActionResult.error(result.get("detail", "Execution failed"))
    return ActionResult.success(
        data={
            "rows_affected": result.get("rows_affected", 0),
            "query_type": result.get("query_type", first_word),
            "tables": result.get("tables", []),
            "exec_ms": result.get("exec_ms", 0),
        },
        summary=f"{first_word} — {result.get('rows_affected', 0)} row(s) affected",
    )
