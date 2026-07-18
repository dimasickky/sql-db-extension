"""sql-db · Natural language to SQL handler."""

import logging

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app import chat, ActionResult, _api_post, require_user_id, get_active_connection, build_conn_info
from models_return import *  # noqa: F401,F403 — data_model DTOs

log = logging.getLogger("sql-db")
from handlers_query import _resolve
from schema_guard import load_schema_section
from imperal_sdk.chat.error_codes import VALIDATION_MISSING_FIELD, INTERNAL
from error_codes import DB_NO_ACTIVE_CONNECTION, DB_SCHEMA_NOT_CACHED


# ─── Models ───────────────────────────────────────────────────────────── #
# LLM-input — see handlers_connections.py for AliasChoices rationale.

class NlToSqlParams(BaseModel):
    """Convert natural language question to SQL."""
    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(
        validation_alias=AliasChoices("question", "query", "prompt", "text", "q", "ask"),
        description="Natural language question about the data",
    )
    connection_id: str = Field(
        default="",
        validation_alias=AliasChoices("connection_id", "conn_id", "connection"),
        description="Connection ID (empty = active)",
    )


# ─── Handler ──────────────────────────────────────────────────────────── #

@chat.function(
    "nl_to_sql", action_type="read",
    description=(
        "Convert a natural language question to SQL. "
        "Automatically fetches schema if needed — use this when user asks a question "
        "about data in natural language and you need to generate the SQL query. "
        "Then call run_query() with the generated SQL."
    ),
    data_model=NlToSqlResult,
)
async def fn_nl_to_sql(ctx, params: NlToSqlParams) -> ActionResult:
    """Convert a natural language question to SQL using the database schema."""
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.", code=DB_NO_ACTIVE_CONNECTION)

        database = conn.get("database", "")
        if not database:
            return ActionResult.error("No database selected.", code=VALIDATION_MISSING_FIELD)

        # Get schema from cache (populated by @ext.skeleton refresh tick).
        # ctx.skeleton.get() raises SkeletonAccessForbidden outside a
        # skeleton-typed tool, so we use the cache layer directly instead.
        schema_data = await load_schema_section(ctx)

        if not schema_data or not schema_data.get("tables"):
            # Fetch fresh schema
            result = await _api_post(ctx, f"/v1/connections/{conn_id}/schema", {
                "user_id": require_user_id(ctx),
                "database": database,
                "connection": build_conn_info(conn),
            })
            schema_data = {"database": database, "tables": result.get("tables", [])}

        # Build schema description for LLM
        schema_desc = _build_schema_description(schema_data)
        if not schema_desc:
            return ActionResult.error("No schema available. Run get_schema first.", code=DB_SCHEMA_NOT_CACHED)

        # ctx.ai.complete() — correct SDK API, returns CompletionResult.text (str).
        # Calls Imperal Gateway which resolves BYOLLM or platform default.
        prompt = (
            f"Database schema:\n\n{schema_desc}\n\n"
            f"Write a SQL SELECT query that answers: {params.question}\n\n"
            "Return ONLY the SQL query, no explanation, no markdown fences."
        )
        completion = await ctx.ai.complete(prompt)
        sql = completion.text.strip().strip("`").strip()
        if sql.lower().startswith("sql"):
            sql = sql[3:].strip()

        return ActionResult.success(
            data={"sql": sql, "question": params.question, "database": database},
            summary=f"Generated SQL for: {params.question}",
        )
    except Exception as e:
        log.error("nl_to_sql: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True, code=INTERNAL)


def _build_schema_description(schema_data: dict) -> str:
    """Build concise schema description for LLM context."""
    lines = []
    db = schema_data.get("database", "")
    lines.append(f"Database: {db}")

    for table in schema_data.get("tables", [])[:50]:
        cols = []
        for c in table.get("columns", []):
            name = c.get("COLUMN_NAME", c.get("name", ""))
            ctype = c.get("COLUMN_TYPE", c.get("type", ""))
            key = c.get("COLUMN_KEY", "")
            marker = " PK" if key == "PRI" else " FK" if key == "MUL" else ""
            cols.append(f"  {name} {ctype}{marker}")

        lines.append(f"\n{table['name']} ({table.get('rows', '?')} rows):")
        lines.extend(cols)

    return "\n".join(lines)
