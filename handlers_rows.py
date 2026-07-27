"""sql-db · Row-level CRUD handlers (insert / update / delete).

All three call the parameterized backend endpoint `/v1/connections/{id}/row` —
values are NEVER interpolated into SQL on the client. Backend uses aiomysql
parameterized queries for safe escaping.
"""

import asyncio
import json as _json
import logging

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app import chat, ActionResult, _api_post, require_user_id, build_conn_info, _translate_db_error
from models_return import *  # noqa: F401,F403 — data_model DTOs
from handlers_query import _resolve as _query_resolve
from schema_guard import (
    load_schema_section,
    validate_columns,
    validate_table_exists,
)
from imperal_sdk.chat.error_codes import VALIDATION_MISSING_FIELD, VALIDATION_TYPE_ERROR, INTERNAL
from error_codes import DB_NO_ACTIVE_CONNECTION, DB_TABLE_NOT_FOUND, DB_COLUMN_NOT_FOUND, DB_QUERY_FAILED
# Bind at module load time (was a lazy import inside the handler); avoids a
# module re-import edge case so the optimistic sidebar update never silently skips.
from events import patch_cache_on_dml

log = logging.getLogger("sql-db")


async def _bump_sidebar_for_dml(
    ctx, *, conn, conn_id: str, table: str, kind: str, row_delta: int,
) -> None:
    """Optimistic-UI: patch the cached sidebar page + signal panel re-render.

    Best-effort. Same wiring fn_run_editor_sql / fn_execute_sql use; lifted
    here so insert_row/update_row/delete_row stay live too.
    """
    try:
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
    """Side-channel to make the platform publish `sql-db.sql.executed`.

    Called via `ctx.extensions.call(...)` from panel handlers that ran DML
    through `/execute` or `/row` directly — those bypass @chat.function, so
    the platform has no way to emit the event automatically. This tiny
    write-typed function gives it a hook.
    """
    kind: str = Field(default="dml", description="Origin marker for debugging")


@chat.function(
    "_pulse_sql_executed", action_type="write", chain_callable=True, effects=["execute:sql"], event="sql.executed",
    data_model=PulseSqlResult,
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
    except _json.JSONDecodeError:
        return {}, "values_json must be valid JSON (e.g. {\"col\": \"value\"})"


async def _resolve(ctx, connection_id: str = ""):
    """Thin wrapper — binding captured at import to survive sys.modules swaps."""
    return await _query_resolve(ctx, connection_id)


# ─── Handlers ─────────────────────────────────────────────────────────── #

@chat.function(
    "insert_row", action_type="write", chain_callable=True, effects=["create:row"], event="row.inserted",
    description="Insert a new row into a table. Values as JSON object of column -> value.",
    data_model=InsertRowResult,
)
async def fn_insert_row(ctx, params: InsertRowParams) -> ActionResult:
    """Insert a new row into a table. Values as JSON object of column -> value."""
    try:
        values, err = _parse_values(params.values_json)
        if err:
            return ActionResult.error(err, code=VALIDATION_TYPE_ERROR)
        if not values:
            return ActionResult.error("No values to insert", code=VALIDATION_MISSING_FIELD)

        # Cache-cheap gate: if we've cached the table's schema, reject
        # unknown columns before the round-trip. Silent no-op when cache
        # is cold (returns {}, validators skip).
        section = await load_schema_section(ctx)
        if (t_err := validate_table_exists(section, params.table)):
            return ActionResult.error(t_err, code=DB_TABLE_NOT_FOUND)
        if (c_err := validate_columns(section, params.table, list(values.keys()))):
            return ActionResult.error(c_err, code=DB_COLUMN_NOT_FOUND)

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection", code=DB_NO_ACTIVE_CONNECTION)

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/row", {
            "user_id": require_user_id(ctx),
            "operation": "insert",
            "table": params.table,
            "values": values,
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(_translate_db_error(result.get("detail", "Insert failed")), code=DB_QUERY_FAILED)

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
        log.error("insert_row: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True, code=INTERNAL)


@chat.function(
    "update_row", action_type="write", chain_callable=True, effects=["update:row"], event="row.updated",
    description="Update a single row identified by primary key. Changes as JSON object of column -> new value.",
    data_model=RowMutateResult,
)
async def fn_update_row(ctx, params: UpdateRowParams) -> ActionResult:
    """Update a single row identified by primary key. Changes as JSON object of column -> new value."""
    try:
        values, err = _parse_values(params.values_json)
        if err:
            return ActionResult.error(err, code=VALIDATION_TYPE_ERROR)
        if not values:
            return ActionResult.error("No changes to apply", code=VALIDATION_MISSING_FIELD)

        # Cache-cheap gate: unknown table / columns (including pk_col)
        # are rejected before the round-trip.
        section = await load_schema_section(ctx)
        if (t_err := validate_table_exists(section, params.table)):
            return ActionResult.error(t_err, code=DB_TABLE_NOT_FOUND)
        referenced = list(values.keys()) + [params.pk_col]
        if (c_err := validate_columns(section, params.table, referenced)):
            return ActionResult.error(c_err, code=DB_COLUMN_NOT_FOUND)

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection", code=DB_NO_ACTIVE_CONNECTION)

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/row", {
            "user_id": require_user_id(ctx),
            "operation": "update",
            "table": params.table,
            "values": values,
            "where": {params.pk_col: params.pk_value},
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(_translate_db_error(result.get("detail", "Update failed")), code=DB_QUERY_FAILED)

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
        log.error("update_row: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True, code=INTERNAL)


@chat.function(
    "delete_row", action_type="destructive", chain_callable=True, effects=["delete:row"], event="row.deleted",
    description="Delete a single row identified by primary key. Requires confirmation.",
    data_model=RowMutateResult,
)
async def fn_delete_row(ctx, params: DeleteRowParams) -> ActionResult:
    """Delete a single row identified by primary key. Requires confirmation."""
    try:
        section = await load_schema_section(ctx)
        if (t_err := validate_table_exists(section, params.table)):
            return ActionResult.error(t_err, code=DB_TABLE_NOT_FOUND)
        if (c_err := validate_columns(section, params.table, [params.pk_col])):
            return ActionResult.error(c_err, code=DB_COLUMN_NOT_FOUND)

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection", code=DB_NO_ACTIVE_CONNECTION)

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/row", {
            "user_id": require_user_id(ctx),
            "operation": "delete",
            "table": params.table,
            "where": {params.pk_col: params.pk_value},
            "connection": build_conn_info(conn),
        })

        if result.get("status") != "ok":
            return ActionResult.error(_translate_db_error(result.get("detail", "Delete failed")), code=DB_QUERY_FAILED)

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
        log.error("delete_row: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True, code=INTERNAL)


# ─── bulk delete ──────────────────────────────────────────────────────────── #
#
# Deliberately a fan-out over the SAME parameterized /row endpoint the single
# delete uses, and NOT a generated `DELETE ... WHERE pk IN (...)`.
#
# One IN-statement would be atomic and a single round trip, which is genuinely
# the nicer shape — but building it means interpolating the pk values into SQL
# text on the client, and the invariant at the top of this module is that
# values are NEVER interpolated client-side; the backend parameterizes them.
# Trading that for one round trip would put a SQL-injection seam in the one
# extension where it is least acceptable, so: N parameterized statements,
# bounded.
#
# The consequence is that this batch is NOT one transaction. That is stated in
# the tool description and reported per row, rather than quietly implied —
# a caller deleting 20 rows is entitled to know that 17 may be gone if 3 fail.
_BULK_CONCURRENCY = 8
MAX_BULK_ROWS = 100


class DeleteRowsParams(BaseModel):
    """Delete several rows by primary key."""
    model_config = ConfigDict(populate_by_name=True)

    table: str = Field(validation_alias=_TABLE_ALIASES, description="Table name")
    pk_col: str = Field(validation_alias=_PK_COL_ALIASES, description="Primary key column name")
    pk_values: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("pk_values", "pk_value", "ids", "values"),
        description="Primary key values of the rows to delete, e.g. ['12', '15', '18'].",
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Set true on a second call to actually delete. First call (default) only "
            "previews which primary keys would be deleted."
        ),
    )
    connection_id: str = Field(
        default="", validation_alias=_CONN_ALIASES,
        description="Connection ID (empty = active)",
    )


@chat.function(
    "delete_rows", action_type="destructive", chain_callable=True,
    effects=["delete:row"], event="rows.deleted",
    description=(
        "Delete SEVERAL rows at once by primary key — the batch version of delete_row. "
        "Pass table, pk_col and pk_values (list). Requires an explicit confirm=true on a "
        "second call; the first call only previews which keys would be deleted. Each row is "
        "deleted as its own parameterized statement, so this is NOT one transaction: if some "
        "fail, the others still go through and each is reported."
    ),
    data_model=BulkRowResult,
)
async def fn_delete_rows(ctx, params: DeleteRowsParams) -> ActionResult:
    """Delete a set of rows by primary key, reported per key.

    Cleaning up a handful of rows was one call each before this. The preview
    gate is not ceremony: unlike a note or a task, a deleted SQL row has no
    trash to come back from, and the realistic accident is not "I didn't want
    to delete rows", it is "I didn't realise id 42 was in that list".
    """
    try:
        values = [str(v).strip() for v in (params.pk_values or []) if str(v).strip()]
        if not values:
            return ActionResult.error("No pk_values given.", code=VALIDATION_MISSING_FIELD)
        if len(values) > MAX_BULK_ROWS:
            return ActionResult.error(
                f"That's {len(values)} rows in one call — the limit is {MAX_BULK_ROWS}. "
                "Split it into smaller batches, or use execute_sql with a WHERE clause.",
                code=VALIDATION_TYPE_ERROR,
            )

        # Schema validation happens ONCE for the batch: the table and pk column
        # are the same for every row, so re-checking per row would only add
        # cost without being able to change the answer.
        section = await load_schema_section(ctx)
        if (t_err := validate_table_exists(section, params.table)):
            return ActionResult.error(t_err, code=DB_TABLE_NOT_FOUND)
        if (c_err := validate_columns(section, params.table, [params.pk_col])):
            return ActionResult.error(c_err, code=DB_COLUMN_NOT_FOUND)

        conn, conn_id = await _resolve(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("No active connection", code=DB_NO_ACTIVE_CONNECTION)

        if not params.confirm:
            shown = ", ".join(values[:20]) + ("…" if len(values) > 20 else "")
            return ActionResult.success(
                data={
                    "table": params.table,
                    "pk_col": params.pk_col,
                    "deleted_count": 0,
                    "failed_count": 0,
                    "results": [],
                    "needs_confirmation": True,
                    "preview": values,
                },
                summary=(
                    f"About to delete {len(values)} row(s) from {params.table} "
                    f"where {params.pk_col} in [{shown}]. Deleted rows cannot be recovered. "
                    "Call again with confirm=true to proceed."
                ),
            )

        sem = asyncio.Semaphore(_BULK_CONCURRENCY)
        done = 0

        async def _delete_one(pk_value: str) -> dict:
            nonlocal done
            err: str | None = None
            affected = 0
            async with sem:
                try:
                    result = await _api_post(ctx, f"/v1/connections/{conn_id}/row", {
                        "user_id": require_user_id(ctx),
                        "operation": "delete",
                        "table": params.table,
                        "where": {params.pk_col: pk_value},
                        "connection": build_conn_info(conn),
                    })
                    if result.get("status") != "ok":
                        err = _translate_db_error(result.get("detail", "Delete failed"))
                    else:
                        affected = int(result.get("rows_affected", 0) or 0)
                except Exception as e:  # noqa: BLE001 — one bad row must not kill the batch
                    log.error("delete_rows %s=%s: %s", params.pk_col, pk_value, e)
                    err = "unexpected error"
            done += 1
            if hasattr(ctx, "progress"):
                try:
                    await ctx.progress(done / len(values), f"{done}/{len(values)}")
                except Exception:  # noqa: BLE001 — progress is cosmetic
                    pass
            return {"pk_value": pk_value, "ok": err is None,
                    "rows_affected": affected, "error": err}

        rows = list(await asyncio.gather(*(_delete_one(v) for v in values)))

        deleted = sum(1 for r in rows if r["ok"])
        failed = len(rows) - deleted
        total_affected = sum(r["rows_affected"] for r in rows)

        # One sidebar bump for the whole batch rather than N: the optimistic
        # update only needs the net row delta, and N patches would make the
        # panel flicker through intermediate counts.
        await _bump_sidebar_for_dml(
            ctx, conn=conn, conn_id=conn_id, table=params.table,
            kind="delete", row_delta=total_affected,
        )

        summary = f"Deleted {deleted} of {len(rows)} row(s) from {params.table}"
        if failed:
            broken = ", ".join(f"{r['pk_value']} ({r['error']})" for r in rows if not r["ok"])
            summary += f" — {failed} failed: {broken}"

        return ActionResult.success(
            data={
                "table": params.table,
                "pk_col": params.pk_col,
                "deleted_count": deleted,
                "failed_count": failed,
                "rows_affected": total_affected,
                "results": rows,
                "needs_confirmation": False,
                "preview": [],
            },
            summary=summary,
        )
    except Exception as e:
        log.error("delete_rows: %s", e)
        return ActionResult.error("An unexpected error occurred. Please try again.", retryable=True, code=INTERNAL)
