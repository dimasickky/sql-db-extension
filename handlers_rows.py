"""sql-db · Row-level CRUD handlers (insert / update / delete).

All three call the parameterized backend endpoint `/v1/connections/{id}/row` —
values are NEVER interpolated into SQL on the client. Backend uses aiomysql
parameterized queries for safe escaping.
"""
from __future__ import annotations

import json as _json
import logging

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app import chat, ActionResult, _api_post, require_user_id, build_conn_info, _translate_db_error
from handlers_query import _resolve as _query_resolve
from schema_guard import (
    load_schema_section,
    validate_columns,
    validate_table_exists,
)

log = logging.getLogger("sql-db")


async def _bump_sidebar_for_dml(
    ctx, *, conn, conn_id: str, table: str, kind: str, row_delta: int,
) -> None:
    """Optimistic-UI: patch the cached sidebar page + signal panel re-render.

    Best-effort. Same wiring fn_run_editor_sql / fn_execute_sql use; lifted
    here so insert_row/update_row/delete_row stay live too.
    """
    try:
        from events import patch_cache_on_dml
        database = (conn or {}).get("database", "")
        await patch_cache_on_dml(
            ctx, conn_id=conn_id, database=database, table=table,
            kind=kind, row_delta=row_delta,
        )
        await ctx.events.emit("table.touched", {
            "conn_id": conn_id, "database": database,
            "table": table, "kind": kind, "row_delta": row_delta,
        })
    except Exception as exc:
        log.warning("sidebar liveness step failed (non-fatal): %s", exc)


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
    "_pulse_sql_executed", action_type="write", chain_callable=True, effects=["execute:sql"], event="sql.executed",
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

# LLM-input — see handlers_connections.py for AliasChoices rationale.
_TABLE_ALIASES = AliasChoices("table", "table_name", "tbl", "tablename")
_VALUES_ALIASES = AliasChoices("values_json", "values", "data", "row", "fields")
_PK_COL_ALIASES = AliasChoices("pk_col", "pk_column", "primary_key", "key_column", "pk")
_PK_VAL_ALIASES = AliasChoices("pk_value", "key_value", "pk_val", "id_value", "value")
_CONN_ALIASES = AliasChoices("connection_id", "conn_id", "connection")


class InsertRowParams(BaseModel):
    """Insert a new row into a table."""
    model_config = ConfigDict(populate_by_name=True)

    table: str = Field(validation_alias=_TABLE_ALIASES, description="Table name")
    values_json: str = Field(
        validation_alias=_VALUES_ALIASES,
        description="JSON object of column -> value to insert",
    )
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class UpdateRowParams(BaseModel):
    """Update a single row by primary key."""
    model_config = ConfigDict(populate_by_name=True)

    table: str = Field(validation_alias=_TABLE_ALIASES, description="Table name")
    pk_col: str = Field(validation_alias=_PK_COL_ALIASES, description="Primary key column name")
    pk_value: str = Field(
        validation_alias=_PK_VAL_ALIASES,
        description="Primary key value of the row to update",
    )
    values_json: str = Field(
        validation_alias=_VALUES_ALIASES,
        description="JSON object of column -> new value",
    )
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


class DeleteRowParams(BaseModel):
    """Delete a single row by primary key."""
    model_config = ConfigDict(populate_by_name=True)

    table: str = Field(validation_alias=_TABLE_ALIASES, description="Table name")
    pk_col: str = Field(validation_alias=_PK_COL_ALIASES, description="Primary key column name")
    pk_value: str = Field(
        validation_alias=_PK_VAL_ALIASES,
        description="Primary key value of the row to delete",
    )
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


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
    """Thin wrapper — binding captured at import to survive sys.modules swaps."""
    return await _query_resolve(ctx, connection_id)


# ─── Handlers ─────────────────────────────────────────────────────────── #

@chat.function(
    "insert_row", action_type="write", chain_callable=True, effects=["create:row"], event="row.inserted",
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

        # Cache-cheap gate: if we've cached the table's schema, reject
        # unknown columns before the round-trip. Silent no-op when cache
        # is cold (returns {}, validators skip).
        section = await load_schema_section(ctx)
        if (t_err := validate_table_exists(section, params.table)):
            return ActionResult.error(t_err)
        if (c_err := validate_columns(section, params.table, list(values.keys()))):
            return ActionResult.error(c_err)

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection")

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/row", {
            "user_id": require_user_id(ctx),
            "operation": "insert",
            "table": params.table,
            "values": values,
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(_translate_db_error(result.get("detail", "Insert failed")))

        affected = int(result.get("rows_affected", 0) or 0)
        await _bump_sidebar_for_dml(
            ctx, conn=conn, conn_id=conn_id, table=params.table,
            kind="insert", row_delta=affected,
        )

        return ActionResult.success(
            data={
                "rows_affected": affected,
                "inserted_id": result.get("inserted_id"),
                "table": params.table,
            },
            summary=f"Inserted row into {params.table}",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "update_row", action_type="write", chain_callable=True, effects=["update:row"], event="row.updated",
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

        # Cache-cheap gate: unknown table / columns (including pk_col)
        # are rejected before the round-trip.
        section = await load_schema_section(ctx)
        if (t_err := validate_table_exists(section, params.table)):
            return ActionResult.error(t_err)
        referenced = list(values.keys()) + [params.pk_col]
        if (c_err := validate_columns(section, params.table, referenced)):
            return ActionResult.error(c_err)

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection")

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/row", {
            "user_id": require_user_id(ctx),
            "operation": "update",
            "table": params.table,
            "values": values,
            "where": {params.pk_col: params.pk_value},
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(_translate_db_error(result.get("detail", "Update failed")))

        affected = int(result.get("rows_affected", 0) or 0)
        await _bump_sidebar_for_dml(
            ctx, conn=conn, conn_id=conn_id, table=params.table,
            kind="update", row_delta=affected,
        )

        return ActionResult.success(
            data={
                "rows_affected": affected,
                "table": params.table,
                "pk": {params.pk_col: params.pk_value},
            },
            summary=f"Updated {affected} row(s) in {params.table}",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "delete_row", action_type="destructive", chain_callable=True, effects=["delete:row"], event="row.deleted",
    description="Delete a single row identified by primary key. Requires confirmation.",
)
async def fn_delete_row(ctx, params: DeleteRowParams) -> ActionResult:
    """Delete a single row identified by primary key. Requires confirmation."""
    try:
        section = await load_schema_section(ctx)
        if (t_err := validate_table_exists(section, params.table)):
            return ActionResult.error(t_err)
        if (c_err := validate_columns(section, params.table, [params.pk_col])):
            return ActionResult.error(c_err)

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection")

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/row", {
            "user_id": require_user_id(ctx),
            "operation": "delete",
            "table": params.table,
            "where": {params.pk_col: params.pk_value},
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(_translate_db_error(result.get("detail", "Delete failed")))

        affected = int(result.get("rows_affected", 0) or 0)
        await _bump_sidebar_for_dml(
            ctx, conn=conn, conn_id=conn_id, table=params.table,
            kind="delete", row_delta=affected,
        )

        return ActionResult.success(
            data={
                "rows_affected": affected,
                "table": params.table,
                "pk": {params.pk_col: params.pk_value},
            },
            summary=f"Deleted row from {params.table}",
        )
    except Exception as e:
        return ActionResult.error(str(e))
