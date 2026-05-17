"""sql-db · History & saved queries handlers."""

import logging

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app import chat, ActionResult, _api_get, _api_post, _api_delete, _api_patch, require_user_id, build_conn_info
from models_return import *  # noqa: F401,F403 — data_model DTOs
from handlers_query import _resolve

log = logging.getLogger("sql-db")


# ─── Models ───────────────────────────────────────────────────────────── #
# LLM-input — see handlers_connections.py for AliasChoices rationale.

_CONN_ALIASES = AliasChoices("connection_id", "conn_id", "connection")
_QUERY_ID_ALIASES = AliasChoices("query_id", "id", "qid", "saved_id", "saved_query_id")
_SQL_TEXT_ALIASES = AliasChoices("sql_text", "sql", "query", "statement", "text")
_NAME_ALIASES = AliasChoices("name", "title", "label", "query_name")


class ListHistoryParams(BaseModel):
    """List query history."""
    model_config = ConfigDict(populate_by_name=True)

    limit: int = Field(default=20, description="Max entries")
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class SaveQueryParams(BaseModel):
    """Save a query."""
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(validation_alias=_NAME_ALIASES, description="Query name")
    sql_text: str = Field(validation_alias=_SQL_TEXT_ALIASES, description="SQL text")
    description: str = Field(
        default="",
        validation_alias=AliasChoices("description", "desc", "note", "comment"),
        description="Optional description",
    )
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class ListSavedParams(BaseModel):
    """List saved queries."""
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class RunSavedParams(BaseModel):
    """Run a saved query."""
    model_config = ConfigDict(populate_by_name=True)

    query_id: str = Field(validation_alias=_QUERY_ID_ALIASES, description="Saved query ID")
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class DeleteSavedParams(BaseModel):
    """Delete a saved query."""
    model_config = ConfigDict(populate_by_name=True)

    query_id: str = Field(validation_alias=_QUERY_ID_ALIASES, description="Saved query ID")
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


# ─── Handlers ─────────────────────────────────────────────────────────── #

@chat.function(
    "list_history", action_type="read",
    description="List recent query history for the active connection.",
    data_model=ListHistoryResult,
)
async def fn_list_history(ctx, params: ListHistoryParams) -> ActionResult:
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_get(ctx,
            f"/v1/connections/{conn_id}/history",
            {"user_id": require_user_id(ctx), "limit": params.limit},
        )

        return ActionResult.success(
            data={"history": result.get("history", []), "total": result.get("total", 0)},
            summary=f"{result.get('total', 0)} queries in history",
        )
    except Exception as e:
        log.error("list_history: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


@chat.function(
    "save_query", action_type="write", chain_callable=True, id_projection="connection_id",
    effects=["create:saved_query"], event="query.saved",
    description="Save a query for later use.",
    data_model=SaveQueryResult,
)
async def fn_save_query(ctx, params: SaveQueryParams) -> ActionResult:
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/saved", {
            "user_id": require_user_id(ctx),
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
        log.error("save_query: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


@chat.function(
    "list_saved", action_type="read",
    description="List saved queries for the active connection.",
    data_model=ListSavedResult,
)
async def fn_list_saved(ctx, params: ListSavedParams) -> ActionResult:
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        result = await _api_get(ctx,
            f"/v1/connections/{conn_id}/saved",
            {"user_id": require_user_id(ctx)},
        )

        queries = result.get("saved_queries", [])
        return ActionResult.success(
            data={"saved_queries": queries, "total": len(queries)},
            summary=f"{len(queries)} saved query(ies)",
        )
    except Exception as e:
        log.error("list_saved: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


@chat.function(
    "run_saved", action_type="read",
    description="Run a previously saved query.",
    data_model=RunSavedResult,
)
async def fn_run_saved(ctx, params: RunSavedParams) -> ActionResult:
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        saved_list = await _api_get(ctx,
            f"/v1/connections/{conn_id}/saved",
            {"user_id": require_user_id(ctx)},
        )
        target = None
        for q in saved_list.get("saved_queries", []):
            if q["id"] == params.query_id:
                target = q
                break
        if not target:
            return ActionResult.error("Saved query not found")

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/query", {
            "user_id": require_user_id(ctx),
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
        log.error("run_saved: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)


@chat.function(
    "delete_saved", action_type="destructive", chain_callable=True, id_projection="query_id",
    effects=["delete:saved_query"], event="query.deleted",
    description="Delete a saved query.",
    data_model=DeleteSavedResult,
)
async def fn_delete_saved(ctx, params: DeleteSavedParams) -> ActionResult:
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection.")

        await _api_delete(ctx,
            f"/v1/connections/{conn_id}/saved/{params.query_id}",
            {"user_id": require_user_id(ctx)},
        )

        return ActionResult.success(
            data={"query_id": params.query_id},
            summary="Saved query deleted",
        )
    except Exception as e:
        log.error("delete_saved: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True)
