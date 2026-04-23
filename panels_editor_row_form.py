"""sql-db · Editor panel — Row Form tab (insert / edit).

Rendered as a tab inside the existing editor panel. Fetches schema for the
given table, detects primary key, and builds a type-aware Form.

This file is the orchestrator. Input rendering lives in
`_row_form_inputs.py`; submit handling lives in `_row_form_submit.py`.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import _api_post, build_conn_info
from _row_form_inputs import render_input, append_back_button

log = logging.getLogger("sql-db")


# ─── Tab renderer ─────────────────────────────────────────────────────── #

async def append_row_form(
    children: list, ctx, uid: str, conn_id: str, conn_data: dict,
    table: str, mode: str, pk_col: str, pk_value: str,
) -> None:
    """Render the row_form tab: type-aware Form for insert/edit."""
    if not table:
        children.append(ui.Empty(
            message="No table specified. Open a table from the sidebar first.",
            icon="Table",
        ))
        return

    database = conn_data.get("database", "")
    conn_info = build_conn_info(conn_data)

    # ── Fetch schema to know columns + PK ─────────────────────────────
    try:
        schema_res = await _api_post(f"/v1/connections/{conn_id}/schema", {
            "user_id": uid, "database": database, "connection": conn_info,
        })
    except Exception as e:
        children.append(ui.Alert(title="Schema error", message=str(e), type="error"))
        return

    tables = schema_res.get("tables", [])
    target = next((t for t in tables if t.get("name") == table), None)
    if not target:
        children.append(ui.Alert(
            title="Table not found",
            message=f"Table '{table}' not found in database '{database}'.",
            type="error",
        ))
        return

    columns = target.get("columns", [])
    if not columns:
        children.append(ui.Alert(
            title="No columns",
            message=f"Table '{table}' has no columns (or schema fetch incomplete).",
            type="warning",
        ))
        return

    # Detect PK (first PRI column) unless pk_col explicitly provided
    detected_pk = next((c.get("COLUMN_NAME", "")
                        for c in columns if c.get("COLUMN_KEY") == "PRI"), "")
    effective_pk = pk_col or detected_pk

    if mode == "edit" and not effective_pk:
        children.append(ui.Alert(
            title="No primary key",
            message=(f"Table '{table}' has no single primary key column — edit and delete "
                     "are disabled to avoid affecting multiple rows. Use the SQL Editor."),
            type="warning",
        ))
        append_back_button(children, conn_id, table)
        return

    # ── Fetch current row for edit mode ───────────────────────────────
    current_row: dict = {}

    if mode == "edit" and effective_pk and pk_value:
        try:
            safe_value = pk_value.replace("'", "''")
            query_sql = (f"SELECT * FROM `{table}` "
                         f"WHERE `{effective_pk}` = '{safe_value}' LIMIT 1")
            query_res = await _api_post(f"/v1/connections/{conn_id}/query", {
                "user_id": uid, "sql": query_sql, "limit": 1,
                "connection": conn_info,
            })
            rows = query_res.get("rows", [])
            if rows:
                current_row = rows[0]
            else:
                children.append(ui.Alert(
                    title="Row not found",
                    message=f"No row with {effective_pk}={pk_value} in {table}.",
                    type="warning",
                ))
                append_back_button(children, conn_id, table)
                return
        except Exception as e:
            children.append(ui.Alert(title="Row fetch failed", message=str(e), type="error"))
            return

    # ── Build the form ────────────────────────────────────────────────
    title = f"Edit row in {table}" if mode == "edit" else f"Insert new row into {table}"
    children.append(ui.Text(title, variant="subheading"))

    if mode == "edit":
        children.append(ui.Text(
            f"Primary key: {effective_pk} = {pk_value}", variant="caption",
        ))

    form_children: list = []
    for col in columns:
        col_name = col.get("COLUMN_NAME", "")
        raw_val = current_row.get(col_name, "") if mode == "edit" else ""
        form_children.extend(render_input(col, "" if raw_val is None else str(raw_val)))

    # Routing params + column defaults. Column values go into defaults
    # because FormContext only registers a child after the user edits it —
    # pre-filled TextArea/Input/Toggle `value=` props don't travel on
    # submit otherwise.
    form_defaults = {
        "note_id": conn_id,
        "table": table,
        "mode": mode,
        "tab": "row_form_submit",
    }
    if mode == "edit":
        form_defaults["pk_col"] = effective_pk
        form_defaults["pk_value"] = pk_value
        for col in columns:
            col_name = col.get("COLUMN_NAME", "")
            extra = col.get("EXTRA", "")
            if "auto_increment" in extra.lower():
                continue
            raw_val = current_row.get(col_name)
            form_defaults[f"col__{col_name}"] = (
                "" if raw_val is None else str(raw_val)
            )

    children.append(ui.Form(
        action="__panel__editor",
        submit_label="Save" if mode == "edit" else "Insert",
        defaults=form_defaults,
        children=form_children,
    ))

    # Delete button (edit mode only)
    if mode == "edit":
        children.append(ui.Divider("Danger zone"))
        children.append(ui.Button(
            "Delete this row", icon="Trash2", variant="danger", size="sm",
            on_click=ui.Call(
                "delete_row",
                table=table, pk_col=effective_pk, pk_value=pk_value,
                connection_id=conn_id,
            ),
        ))

    append_back_button(children, conn_id, table)


# ─── Submit handler re-export ─────────────────────────────────────────── #
#
# panels_editor.py imports `process_row_form_submit` from this module.
# Keep the name stable by re-exporting from the split submit module.

from _row_form_submit import process_row_form_submit  # noqa: E402, F401
