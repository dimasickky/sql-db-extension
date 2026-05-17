"""sql-db · Query, explain, dry_run handlers."""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

import logging

from app import (
    chat, ActionResult, _api_post, require_user_id,
    resolve_connection, get_connection_by_id, build_conn_info,
    DbSchemaSnapshot, SCHEMA_CACHE_KEY, SCHEMA_CACHE_TTL,
    _translate_db_error,
)
from models_return import *  # noqa: F401,F403 — data_model DTOs


log = logging.getLogger("sql-db")


# ─── Models ───────────────────────────────────────────────────────────── #
# LLM-input models — see handlers_connections.py for the AliasChoices rationale.

_SQL_ALIASES = AliasChoices("sql", "query", "statement", "sql_text", "text")
_CONN_ALIASES = AliasChoices("connection_id", "conn_id", "connection")
_DB_ALIASES = AliasChoices("database", "db", "db_name", "database_name", "schema")


class RunQueryParams(BaseModel):
    """Run a SELECT query."""
    model_config = ConfigDict(populate_by_name=True)

    sql: str = Field(validation_alias=_SQL_ALIASES, description="SQL SELECT query")
    limit: int = Field(default=100, description="Max rows to return")
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class ExplainParams(BaseModel):
    """Explain a query."""
    model_config = ConfigDict(populate_by_name=True)

    sql: str = Field(validation_alias=_SQL_ALIASES, description="SQL query to explain")
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class DryRunParams(BaseModel):
    """Dry-run a DML statement."""
    model_config = ConfigDict(populate_by_name=True)

    sql: str = Field(validation_alias=_SQL_ALIASES, description="DML statement to dry-run")
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class GetSchemaParams(BaseModel):
    """Get database schema."""
    model_config = ConfigDict(populate_by_name=True)

    database: str = Field(
        default="", validation_alias=_DB_ALIASES,
        description="Database name (empty = default)",
    )
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


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
    data_model=QueryResult,
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

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/query", {
            "user_id": require_user_id(ctx),
            "sql": sql,
            "limit": params.limit,
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
    except Exception as e:
        log.error("run_query: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


@chat.function(
    "get_schema", action_type="read",
    data_model=GetSchemaResult,
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

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/schema", {
            "user_id": require_user_id(ctx),
            "database": database,
            "connection": build_conn_info(conn),
        })

        tables = result.get("tables", [])

        # Mirror into ctx.cache so schema guard in execute_sql / insert_row
        # has up-to-date column data immediately — even when the skeleton
        # hasn't ticked yet (cold cache on fresh sessions).
        try:
            compact = []
            for t in tables:
                cols = [
                    {"name": c.get("COLUMN_NAME", ""), "type": c.get("COLUMN_TYPE", ""),
                     "key": c.get("COLUMN_KEY", "")}
                    for c in t.get("columns", [])
                ]
                compact.append({"name": t["name"], "rows": t.get("rows", 0), "columns": cols})
            snap = DbSchemaSnapshot.model_validate({
                "database": database,
                "connection": conn.get("name", ""),
                "table_count": len(compact),
                "tables": compact,
            })
            await ctx.cache.set(SCHEMA_CACHE_KEY, snap, ttl_seconds=SCHEMA_CACHE_TTL)
        except Exception as exc:
            log.debug("get_schema: cache mirror failed (non-fatal): %s", exc)

        return ActionResult.success(
            data={"database": database, "tables": tables, "table_count": len(tables)},
            summary=f"Database '{database}': {len(tables)} table(s)",
        )
    except Exception as e:
        log.error("get_schema: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


@chat.function(
    "explain_query", action_type="read",
    description="Run EXPLAIN on a query to see execution plan.",
    data_model=ExplainResult,
)
async def fn_explain_query(ctx, params: ExplainParams) -> ActionResult:
    """Run EXPLAIN on a query to see execution plan."""
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/explain", {
            "user_id": require_user_id(ctx),
            "sql": params.sql,
            "connection": build_conn_info(conn),
        })

        return ActionResult.success(
            data={"plan": result.get("plan", []), "sql": params.sql},
            summary="EXPLAIN plan for query",
        )
    except Exception as e:
        log.error("explain_query: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


@chat.function(
    "dry_run", action_type="read",
    description="Dry-run a DML statement: execute in transaction, count affected rows, then ROLLBACK.",
    data_model=DryRunResult,
)
async def fn_dry_run(ctx, params: DryRunParams) -> ActionResult:
    """Dry-run a DML statement: execute in transaction, count affected rows, then ROLLBACK."""
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/dry_run", {
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
        log.error("dry_run: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


class CountTableParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    table: str = Field(
        ...,
        validation_alias=AliasChoices("table", "table_name"),
        description="Table name to count rows in. Runs a real SELECT COUNT(*) — not an estimate.",
    )
    database: str = Field(
        default="",
        description="Database name. Omit to use the active connection's default database.",
    )
    connection_id: str = Field(
        default="",
        validation_alias=AliasChoices("connection_id", "conn_id", "connection"),
        description="Connection ID. Omit to use the active connection.",
    )


@chat.function(
    "count_table",
    action_type="read",
    description=(
        "Get the EXACT row count of a table via SELECT COUNT(*). "
        "Use this when the user asks how many rows are in a table — "
        "get_schema() returns INFORMATION_SCHEMA estimates which can be wrong. "
        "This is the only call that returns a guaranteed-accurate row count."
    ),
    data_model=CountTableResult,
)
async def fn_count_table(ctx, params: CountTableParams) -> ActionResult:
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection. Use add_connection first.")

        database = params.database or conn.get("database", "")
        if not database:
            return ActionResult.error("No database specified. Provide database name.")

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/tables/{params.table}/count", {
            "user_id": require_user_id(ctx),
            "database": database,
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(
                f"Row count failed: {result.get('error') or result.get('detail', 'unknown error')}"
            )

        count = result.get("count", 0)
        exec_ms = result.get("exec_ms", 0)
        return ActionResult.success(
            summary=f"Table '{params.table}' has {count:,} rows ({exec_ms}ms).",
            data={
                "database": database,
                "table":    params.table,
                "count":    count,
                "exec_ms":  exec_ms,
            },
        )
    except Exception as e:
        log.error("count_table: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)
