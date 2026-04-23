"""sql-db · Row form submit — collect col__* params and dispatch.

The form submits to action="__panel__editor" with tab="row_form_submit".
The dispatcher in panels_editor.py routes that tab here for processing,
then re-renders row_form tab with a success/error Alert.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import _api_post, build_conn_info
from _row_form_inputs import append_back_button

log = logging.getLogger("sql-db")


def _collect_values(form_params: dict, mode: str) -> dict:
    """Strip col__ prefix; skip empty strings on insert (let DB use DEFAULT)."""
    values: dict = {}
    for k, v in form_params.items():
        if not k.startswith("col__"):
            continue
        col_name = k[len("col__"):]
        if mode == "insert" and v == "":
            continue
        values[col_name] = v
    return values


async def _pulse_sidebar(ctx, mode: str) -> None:
    """Emit sql.executed via internal ctx.extensions.call so sidebar refreshes.

    Row-form DML bypasses @chat.function → kernel doesn't publish events.
    Failure is swallowed — this is a UX nicety, never load-bearing.
    """
    try:
        if hasattr(ctx, "extensions") and ctx.extensions is not None:
            await ctx.extensions.call(
                "sql-db", "_pulse_sql_executed",
                {"kind": f"row_form_{mode}"},
            )
    except Exception as e:
        log.debug("sql.executed pulse skipped: %s", e)


async def process_row_form_submit(
    children: list, ctx, uid: str, conn_id: str, conn_data: dict,
    table: str, mode: str, pk_col: str, pk_value: str, form_params: dict,
) -> None:
    """Collect col__* params from form, dispatch to insert_row/update_row handler."""
    values = _collect_values(form_params, mode)

    if not values:
        children.append(ui.Alert(
            title="No changes",
            message="Form is empty — nothing to save.", type="warning",
        ))
        append_back_button(children, conn_id, table)
        return

    conn_info = build_conn_info(conn_data)

    if mode == "insert":
        payload = {
            "user_id": uid, "operation": "insert",
            "table": table, "values": values, "connection": conn_info,
        }
    else:
        if not pk_col or not pk_value:
            children.append(ui.Alert(
                title="Missing PK",
                message="Cannot update: primary key not provided.", type="error",
            ))
            append_back_button(children, conn_id, table)
            return
        payload = {
            "user_id": uid, "operation": "update",
            "table": table, "values": values,
            "where": {pk_col: pk_value},
            "connection": conn_info,
        }

    try:
        result = await _api_post(f"/v1/connections/{conn_id}/row", payload)
    except Exception as e:
        children.append(ui.Alert(title="Request failed", message=str(e), type="error"))
        append_back_button(children, conn_id, table)
        return

    if result.get("status") != "ok":
        children.append(ui.Alert(
            title="Save failed",
            message=result.get("detail", "Unknown error"), type="error",
        ))
        append_back_button(children, conn_id, table)
        return

    affected = result.get("rows_affected", 0)
    if mode == "insert":
        inserted_id = result.get("inserted_id")
        msg = "Inserted row" + (f" (id={inserted_id})" if inserted_id else "")
    else:
        msg = f"Updated {affected} row(s)"

    children.append(ui.Alert(title="Saved", message=msg, type="success"))
    await _pulse_sidebar(ctx, mode)
    append_back_button(children, conn_id, table)
