"""sql-db · Natural language to SQL handler."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import chat, ActionResult, _api_post, require_user_id, get_active_connection, build_conn_info
from handlers_query import _resolve


# ─── Models ───────────────────────────────────────────────────────────── #

class NlToSqlParams(BaseModel):
    """Convert natural language question to SQL."""
    question: str = Field(description="Natural language question about the data")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


# ─── Handler ──────────────────────────────────────────────────────────── #

@chat.function(
    "nl_to_sql", action_type="read",
    description="Convert a natural language question to SQL using the database schema.",
)
async def fn_nl_to_sql(ctx, params: NlToSqlParams) -> ActionResult:
    """Convert a natural language question to SQL using the database schema."""
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        database = conn.get("database", "")
        if not database:
            return ActionResult.error("No database selected.")

        # Get schema from skeleton (cached) or fetch fresh
        schema_data = None
        if ctx.skeleton:
            schema_data = await ctx.skeleton.get("db_schema")

        if not schema_data or not schema_data.get("tables"):
            # Fetch fresh schema
            result = await _api_post(f"/v1/connections/{conn_id}/schema", {
                "user_id": require_user_id(ctx),
                "database": database,
                "connection": build_conn_info(conn),
            })
            schema_data = {"database": database, "tables": result.get("tables", [])}

        # Build schema description for LLM
        schema_desc = _build_schema_description(schema_data)
        if not schema_desc:
            return ActionResult.error("No schema available. Run get_schema first.")

        # Use ctx.ai to generate SQL
        prompt = (
            f"Given the following MySQL database schema:\n\n{schema_desc}\n\n"
            f"Write a SQL SELECT query that answers: {params.question}\n\n"
            f"Return ONLY the SQL query, no explanation."
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
        return ActionResult.error(str(e))


def _build_schema_description(schema_data: dict) -> str:
    """Build concise schema description for LLM context."""
    lines = []
    db = schema_data.get("database", "")
    lines.append(f"Database: {db}")

    for table in schema_data.get("tables", [])[:30]:
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
