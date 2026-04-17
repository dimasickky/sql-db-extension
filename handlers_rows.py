"""sql-db · Row-level CRUD handlers (insert / update / delete).

All three call the parameterized backend endpoint `/v1/connections/{id}/row` —
values are NEVER interpolated into SQL on the client. Backend uses aiomysql
parameterized queries for safe escaping.
"""
from __future__ import annotations

import json as _json

from pydantic import BaseModel, Field

from app import chat, ActionResult, _api_post, _user_id, build_conn_info


# ─── Event pulse (internal) ───────────────────────────────────────────── #

class PulseParams(BaseModel):
    """Side-channel to make the kernel publish `sql-db.sql.executed`.

    Called via `ctx.extensions.call(...)` from panel handlers that ran DML
    through `/execute` or `/row` directly — those bypass @chat.function, so
    the kernel has no way to emit the event automatically. This tiny
    write-typed function gives it a hook.
    """
    kind: str = Field(default="dml", description="Origin marker for debugging")


@chat.function(
    "_pulse_sql_executed", action_type="write", event="sql.executed",
    description=(
        "Internal side-channel: emit sql.executed after a panel-direct DML "
        "so the sidebar schema refreshes. Do not call from chat — for "
        "extension-internal use only."
    ),
)
async def fn_pulse_sql_executed(ctx, params: PulseParams) -> ActionResult:
    """Internal side-channel: emit sql.executed after panel-direct DML so the sidebar refreshes."""
    return ActionResult.success(data={"kind": params.kind}, summary="")


# ─── Models ───────────────────────────────────────────────────────────── #

class InsertRowParams(BaseModel):
    """Insert a new row into a table."""
    table: str = Field(description="Table name")
    values_json: str = Field(description="JSON object of column -> value to insert")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class UpdateRowParams(BaseModel):
    """Update a single row by primary key."""
    table: str = Field(description="Table name")
    pk_col: str = Field(description="Primary key column name")
    pk_value: str = Field(description="Primary key value of the row to update")
    values_json: str = Field(description="JSON object of column -> new value")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


class DeleteRowParams(BaseModel):
    """Delete a single row by primary key."""
    table: str = Field(description="Table name")
    pk_col: str = Field(description="Primary key column name")
    pk_value: str = Field(description="Primary key value of the row to delete")
    connection_id: str = Field(default="", description="Connection ID (empty = active)")


# ─── Helpers ──────────────────────────────────────────────────────────── #

def _parse_values(values_json: str) -> tuple[dict, str | None]:
    """Parse values_json, return (dict, error_msg or None)."""
    if not values_json or not values_json.strip():
        return {}, "No values provided"
    try:
        data = _json.loads(values_json)
        if not isinstance(data, dict):
            return {}, "values_json must be a JSON object"
        return data, None
    except _json.JSONDecodeError as e:
        return {}, f"Invalid JSON: {e}"


async def _resolve(ctx, connection_id: str = ""):
    """Shared connection resolver (avoids circular import with handlers_query)."""
    from handlers_query import _resolve as _r
    return await _r(ctx, connection_id)


# ─── Handlers ─────────────────────────────────────────────────────────── #

@chat.function(
    "insert_row", action_type="write", event="row.inserted",
    description="Insert a new row into a table. Values as JSON object of column -> value.",
)
async def fn_insert_row(ctx, params: InsertRowParams) -> ActionResult:
    """Insert a new row into a table. Values as JSON object of column -> value."""
    try:
        values, err = _parse_values(params.values_json)
        if err:
            return ActionResult.error(err)
        if not values:
            return ActionResult.error("No values to insert")

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection")

        result = await _api_post(f"/v1/connections/{conn_id}/row", {
            "user_id": _user_id(ctx),
            "operation": "insert",
            "table": params.table,
            "values": values,
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(result.get("detail", "Insert failed"))

        return ActionResult.success(
            data={
                "rows_affected": result.get("rows_affected", 0),
                "inserted_id": result.get("inserted_id"),
                "table": params.table,
            },
            summary=f"Inserted row into {params.table}",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "update_row", action_type="write", event="row.updated",
    description="Update a single row identified by primary key. Changes as JSON object of column -> new value.",
)
async def fn_update_row(ctx, params: UpdateRowParams) -> ActionResult:
    """Update a single row identified by primary key. Changes as JSON object of column -> new value."""
    try:
        values, err = _parse_values(params.values_json)
        if err:
            return ActionResult.error(err)
        if not values:
            return ActionResult.error("No changes to apply")

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection")

        result = await _api_post(f"/v1/connections/{conn_id}/row", {
            "user_id": _user_id(ctx),
            "operation": "update",
            "table": params.table,
            "values": values,
            "where": {params.pk_col: params.pk_value},
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(result.get("detail", "Update failed"))

        return ActionResult.success(
            data={
                "rows_affected": result.get("rows_affected", 0),
                "table": params.table,
                "pk": {params.pk_col: params.pk_value},
            },
            summary=f"Updated {result.get('rows_affected', 0)} row(s) in {params.table}",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "delete_row", action_type="destructive", event="row.deleted",
    description="Delete a single row identified by primary key. Requires confirmation.",
)
async def fn_delete_row(ctx, params: DeleteRowParams) -> ActionResult:
    """Delete a single row identified by primary key. Requires confirmation."""
    try:
        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection")

        result = await _api_post(f"/v1/connections/{conn_id}/row", {
            "user_id": _user_id(ctx),
            "operation": "delete",
            "table": params.table,
            "where": {params.pk_col: params.pk_value},
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(result.get("detail", "Delete failed"))

        return ActionResult.success(
            data={
                "rows_affected": result.get("rows_affected", 0),
                "table": params.table,
                "pk": {params.pk_col: params.pk_value},
            },
            summary=f"Deleted row from {params.table}",
        )
    except Exception as e:
        return ActionResult.error(str(e))
