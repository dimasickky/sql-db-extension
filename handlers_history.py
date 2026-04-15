"""sql-db · History & saved queries handlers."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import chat, ActionResult, _api_get, _api_post, _api_delete, _api_patch, _user_id, build_conn_info


# ─── Models ───────────────────────────────────────────────────────────── #

class ListHistoryParams(BaseModel):
    """List query history."""
    limit: int = Field(default=20, description="Max entries")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class SaveQueryParams(BaseModel):
    """Save a query."""
    name: str = Field(description="Query name")
    sql_text: str = Field(description="SQL text")
    description: str = Field(default="", description="Optional description")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class ListSavedParams(BaseModel):
    """List saved queries."""
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class RunSavedParams(BaseModel):
    """Run a saved query."""
    query_id: str = Field(description="Saved query ID")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class DeleteSavedParams(BaseModel):
    """Delete a saved query."""
    query_id: str = Field(description="Saved query ID")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


# ─── Handlers ─────────────────────────────────────────────────────────── #

@chat.function(
    "list_history", action_type="read",
    description="List recent query history for the active connection.",
)
async def fn_list_history(ctx, params: ListHistoryParams) -> ActionResult:
    try:
        from handlers_query import _resolve
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_get(
            f"/v1/connections/{conn_id}/history",
            {"user_id": _user_id(ctx), "limit": params.limit},
        )

        return ActionResult.success(
            data={"history": result.get("history", []), "total": result.get("total", 0)},
            summary=f"{result.get('total', 0)} queries in history",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "save_query", action_type="write", event="query.saved",
    description="Save a query for later use.",
)
async def fn_save_query(ctx, params: SaveQueryParams) -> ActionResult:
    try:
        from handlers_query import _resolve
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_post(f"/v1/connections/{conn_id}/saved", {
            "user_id": _user_id(ctx),
            "conn_id": conn_id,
            "name": params.name,
            "sql_text": params.sql_text,
            "description": params.description,
        })

        return ActionResult.success(
            data={"query_id": result.get("id"), "name": params.name},
            summary=f"Query saved: {params.name}",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "list_saved", action_type="read",
    description="List saved queries for the active connection.",
)
async def fn_list_saved(ctx, params: ListSavedParams) -> ActionResult:
    try:
        from handlers_query import _resolve
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_get(
            f"/v1/connections/{conn_id}/saved",
            {"user_id": _user_id(ctx)},
        )

        queries = result.get("saved_queries", [])
        return ActionResult.success(
            data={"saved_queries": queries, "total": len(queries)},
            summary=f"{len(queries)} saved query(ies)",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "run_saved", action_type="read",
    description="Run a previously saved query.",
)
async def fn_run_saved(ctx, params: RunSavedParams) -> ActionResult:
    try:
        from handlers_query import _resolve
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        # Get the saved query
        saved_list = await _api_get(
            f"/v1/connections/{conn_id}/saved",
            {"user_id": _user_id(ctx)},
        )
        target = None
        for q in saved_list.get("saved_queries", []):
            if q["id"] == params.query_id:
                target = q
                break
        if not target:
            return ActionResult.error("Saved query not found")

        # Execute it
        result = await _api_post(f"/v1/connections/{conn_id}/query", {
            "user_id": _user_id(ctx),
            "sql": target["sql_text"],
            "limit": 100,
            "connection": build_conn_info(conn),
        })

        return ActionResult.success(
            data={
                "name": target["name"],
                "sql": target["sql_text"],
                "columns": result.get("columns", []),
                "rows": result.get("rows", []),
                "total_rows": result.get("total_rows", 0),
                "exec_ms": result.get("exec_ms", 0),
            },
            summary=f"'{target['name']}': {result.get('total_rows', 0)} row(s) in {result.get('exec_ms', 0)}ms",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "delete_saved", action_type="destructive", event="query.deleted",
    description="Delete a saved query.",
)
async def fn_delete_saved(ctx, params: DeleteSavedParams) -> ActionResult:
    try:
        from handlers_query import _resolve
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        await _api_delete(
            f"/v1/connections/{conn_id}/saved/{params.query_id}",
            {"user_id": _user_id(ctx)},
        )

        return ActionResult.success(
            data={"query_id": params.query_id},
            summary="Saved query deleted",
        )
    except Exception as e:
        return ActionResult.error(str(e))
