"""sql-db · Query, explain, dry_run handlers."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import (
    chat, ActionResult, _api_post, require_user_id,
    resolve_connection, get_connection_by_id, build_conn_info,
)


# ─── Models ───────────────────────────────────────────────────────────── #

class RunQueryParams(BaseModel):
    """Run a SELECT query."""
    sql: str = Field(description="SQL SELECT query")
    limit: int = Field(default=100, description="Max rows to return")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class ExplainParams(BaseModel):
    """Explain a query."""
    sql: str = Field(description="SQL query to explain")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class DryRunParams(BaseModel):
    """Dry-run a DML statement."""
    sql: str = Field(description="DML statement to dry-run")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class GetSchemaParams(BaseModel):
    """Get database schema."""
    database: str = Field(default="", description="Database name (empty = default)")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


# ─── Helpers ──────────────────────────────────────────────────────────── #

async def _resolve(ctx, connection_id: str = "") -> tuple[dict | None, str]:
    """Resolve connection: by ID or active/fallback."""
    if connection_id:
        conn = await get_connection_by_id(ctx, connection_id)
        return conn, connection_id
    return await resolve_connection(ctx)


# ─── Handlers ─────────────────────────────────────────────────────────── #

_SELECT_VERBS = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}


@chat.function(
    "run_query", action_type="read",
    description=(
        "Run a read-only query (SELECT/WITH/SHOW/DESCRIBE/EXPLAIN). "
        "Use execute_sql for INSERT/UPDATE/DELETE/DDL."
    ),
)
async def fn_run_query(ctx, params: RunQueryParams) -> ActionResult:
    """Run a SELECT query on the database."""
    try:
        sql = (params.sql or "").strip().rstrip(";")
        if not sql or sql.lower() == "sql":
            return ActionResult.error(
                "run_query: sql parameter is empty or unresolved placeholder"
            )

        first = sql.split(None, 1)[0].upper() if sql else ""
        if first not in _SELECT_VERBS:
            return ActionResult.error(
                f"run_query accepts read-only verbs only (got '{first}'). "
                f"Use execute_sql for {first} and other DML/DDL statements."
            )

        conn_id_in = params.connection_id or ""
        if conn_id_in == "connection_id":
            conn_id_in = ""
        conn, conn_id = await _resolve(ctx, conn_id_in)
        if not conn:
            return ActionResult.error("No active connection. Use add_connection first.")

        result = await _api_post(f"/v1/connections/{conn_id}/query", {
            "user_id": require_user_id(ctx),
            "sql": sql,
            "limit": params.limit,
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
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
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "get_schema", action_type="read",
    description="Get database schema — tables, columns, indexes.",
)
async def fn_get_schema(ctx, params: GetSchemaParams) -> ActionResult:
    """Get database schema — tables, columns, indexes."""
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection. Use add_connection first.")

        database = params.database or conn.get("database", "")
        if not database:
            return ActionResult.error("No database specified. Provide database name.")

        result = await _api_post(f"/v1/connections/{conn_id}/schema", {
            "user_id": require_user_id(ctx),
            "database": database,
            "connection": build_conn_info(conn),
        })

        tables = result.get("tables", [])
        return ActionResult.success(
            data={"database": database, "tables": tables, "table_count": len(tables)},
            summary=f"Database '{database}': {len(tables)} table(s)",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "explain_query", action_type="read",
    description="Run EXPLAIN on a query to see execution plan.",
)
async def fn_explain_query(ctx, params: ExplainParams) -> ActionResult:
    """Run EXPLAIN on a query to see execution plan."""
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_post(f"/v1/connections/{conn_id}/explain", {
            "user_id": require_user_id(ctx),
            "sql": params.sql,
            "connection": build_conn_info(conn),
        })

        return ActionResult.success(
            data={"plan": result.get("plan", []), "sql": params.sql},
            summary="EXPLAIN plan for query",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "dry_run", action_type="read",
    description="Dry-run a DML statement: execute in transaction, count affected rows, then ROLLBACK.",
)
async def fn_dry_run(ctx, params: DryRunParams) -> ActionResult:
    """Dry-run a DML statement: execute in transaction, count affected rows, then ROLLBACK."""
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_post(f"/v1/connections/{conn_id}/dry_run", {
            "user_id": require_user_id(ctx),
            "sql": params.sql,
            "connection": build_conn_info(conn),
        })

        return ActionResult.success(
            data={
                "would_affect": result.get("would_affect", 0),
                "query_type": result.get("query_type", ""),
                "tables": result.get("tables", []),
                "exec_ms": result.get("exec_ms", 0),
            },
            summary=f"Would affect {result.get('would_affect', 0)} row(s)",
        )
    except Exception as e:
        return ActionResult.error(str(e))
