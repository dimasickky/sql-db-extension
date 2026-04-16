"""sql-db · Editor panel — Row Form tab (insert / edit).

Rendered as a tab inside the existing editor panel. Fetches schema for the
given table, detects primary key, and builds a type-aware Form.
"""
from __future__ import annotations

import json as _json
import logging

from imperal_sdk import ui

from app import _api_post, _user_id, build_conn_info

log = logging.getLogger("sql-db")


# ─── Column → Input mapper ────────────────────────────────────────────── #

_BOOLEAN_TYPES = ("tinyint(1)", "bit(1)", "boolean", "bool")
_NUMERIC_PREFIXES = ("int", "bigint", "smallint", "mediumint", "tinyint",
                     "decimal", "numeric", "float", "double", "real")
_LONG_TEXT_TYPES = ("text", "mediumtext", "longtext", "json", "blob",
                    "mediumblob", "longblob")


def _is_boolean(col_type: str) -> bool:
    t = col_type.lower().strip()
    return any(t == b or t.startswith(b) for b in _BOOLEAN_TYPES)


def _is_numeric(col_type: str) -> bool:
    t = col_type.lower().strip()
    return any(t.startswith(p) for p in _NUMERIC_PREFIXES)


def _is_long_text(col_type: str) -> bool:
    t = col_type.lower().strip()
    return any(t.startswith(p) for p in _LONG_TEXT_TYPES)


def _render_input(col: dict, default_value: str) -> list:
    """Return (label, input) components for one column."""
    name = col.get("COLUMN_NAME", "")
    ctype = col.get("COLUMN_TYPE", "")
    nullable = col.get("IS_NULLABLE", "YES") == "YES"
    key = col.get("COLUMN_KEY", "")
    extra = col.get("EXTRA", "")
    is_auto = "auto_increment" in extra.lower()

    hint_parts = [ctype]
    if key == "PRI":
        hint_parts.append("PK")
    if is_auto:
        hint_parts.append("auto")
    if not nullable:
        hint_parts.append("NOT NULL")
    hint = " · ".join(hint_parts)

    label = ui.Text(f"{name}  ({hint})", variant="caption")

    # Auto-increment PKs — skip from the form; DB generates
    if is_auto:
        return [label, ui.Text(
            "(auto-generated on insert — skipped)",
            variant="caption",
        )]

    if _is_boolean(ctype):
        input_el = ui.Toggle(
            label=name,
            value=default_value in ("1", "true", "True"),
            param_name=f"col__{name}",
        )
    elif _is_long_text(ctype):
        input_el = ui.TextArea(
            placeholder=f"NULL" if nullable and not default_value else "",
            value=default_value,
            rows=4,
            param_name=f"col__{name}",
        )
    else:
        placeholder = "NULL" if nullable and not default_value else ctype
        input_el = ui.Input(
            placeholder=placeholder,
            value=default_value,
            param_name=f"col__{name}",
        )

    return [label, input_el]


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
        _append_back_button(children, conn_id, table)
        return

    # ── Fetch current row for edit mode ───────────────────────────────
    defaults = {"table": table, "note_id": conn_id, "tab": "row_form", "mode": mode}
    current_row: dict = {}

    if mode == "edit" and effective_pk and pk_value:
        try:
            sql = (f"SELECT * FROM `{table}` "
                   f"WHERE `{effective_pk}` = %s LIMIT 1")
            # Re-use /query with inline parameter binding via placeholder emulation
            # (backend's /query does not parameterize — we escape manually for SELECT only)
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
                _append_back_button(children, conn_id, table)
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
        # For insert: no default. For edit: current value stringified
        raw_val = current_row.get(col_name, "") if mode == "edit" else ""
        form_children.extend(_render_input(col, "" if raw_val is None else str(raw_val)))

    # Hidden fields: table, pk_col, pk_value — passed via defaults
    submit_action = "__panel__editor_row_save"  # dispatcher (see below)
    form_defaults = {
        "note_id": conn_id,
        "table": table,
        "mode": mode,
        "tab": "row_form_submit",
    }
    if mode == "edit":
        form_defaults["pk_col"] = effective_pk
        form_defaults["pk_value"] = pk_value

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

    _append_back_button(children, conn_id, table)


def _append_back_button(children: list, conn_id: str, table: str) -> None:
    """Back to Browse (results with SELECT * for this table)."""
    children.append(ui.Button(
        "Back to Browse", icon="ArrowLeft", variant="ghost", size="sm",
        on_click=ui.Call(
            "__panel__editor",
            note_id=conn_id, tab="results", action="run",
            sql=f"SELECT * FROM `{table}` LIMIT 200",
        ),
    ))


# ─── Form submit handler (panel-side) ─────────────────────────────────── #
#
# The form submits to action="__panel__editor" with tab="row_form_submit".
# The dispatcher in panels_editor.py routes that tab here for processing,
# then re-renders row_form tab with a success/error Alert.
# Form values arrive as col__<name> params — we strip the prefix and
# dispatch to insert_row / update_row chat handlers.

async def process_row_form_submit(
    children: list, ctx, uid: str, conn_id: str, conn_data: dict,
    table: str, mode: str, pk_col: str, pk_value: str, form_params: dict,
) -> None:
    """Collect col__* params from form, dispatch to insert_row/update_row handler."""
    # Extract col__<name> values into a dict
    values = {}
    for k, v in form_params.items():
        if k.startswith("col__"):
            col_name = k[len("col__"):]
            # Skip empty strings on insert (let DB use DEFAULT)
            if mode == "insert" and v == "":
                continue
            values[col_name] = v

    if not values:
        children.append(ui.Alert(
            title="No changes",
            message="Form is empty — nothing to save.", type="warning",
        ))
        _append_back_button(children, conn_id, table)
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
            _append_back_button(children, conn_id, table)
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
        _append_back_button(children, conn_id, table)
        return

    if result.get("status") != "ok":
        children.append(ui.Alert(
            title="Save failed",
            message=result.get("detail", "Unknown error"), type="error",
        ))
        _append_back_button(children, conn_id, table)
        return

    affected = result.get("rows_affected", 0)
    if mode == "insert":
        inserted_id = result.get("inserted_id")
        msg = f"Inserted row" + (f" (id={inserted_id})" if inserted_id else "")
    else:
        msg = f"Updated {affected} row(s)"

    children.append(ui.Alert(title="Saved", message=msg, type="success"))
    _append_back_button(children, conn_id, table)
