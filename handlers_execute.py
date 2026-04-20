"""sql-db · DML/DDL execution handler + universal editor runner."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import chat, ActionResult, _api_post, _user_id, build_conn_info
from handlers_query import _resolve, RunQueryParams, ExplainParams  # noqa: F401


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

@chat.function(
    "execute_sql", action_type="destructive", event="sql.executed",
    description="Execute a DML/DDL statement (INSERT, UPDATE, DELETE, ALTER, CREATE, DROP). Requires confirmation.",
)
async def fn_execute_sql(ctx, params: ExecuteSqlParams) -> ActionResult:
    """Execute a DML/DDL statement (INSERT, UPDATE, DELETE, ALTER, CREATE, DROP). Requires confirmation."""
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_post(f"/v1/connections/{conn_id}/execute", {
            "user_id": _user_id(ctx),
            "sql": params.sql,
            "confirmed": True,
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(result.get("detail", "Execution failed"))

        return ActionResult.success(
            data={
                "rows_affected": result.get("rows_affected", 0),
                "query_type": result.get("query_type", ""),
                "tables": result.get("tables", []),
                "exec_ms": result.get("exec_ms", 0),
            },
            summary=f"{result.get('query_type', 'SQL')} — {result.get('rows_affected', 0)} row(s) affected",
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
