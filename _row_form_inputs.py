"""sql-db · Row form — column-type → input element mapping.

Pure rendering helpers. No I/O.
"""
from __future__ import annotations

from imperal_sdk import ui


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


def render_input(col: dict, default_value: str) -> list:
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
            placeholder="NULL" if nullable and not default_value else "",
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


def append_back_button(children: list, conn_id: str, table: str) -> None:
    """Back to Browse (results with SELECT * for this table)."""
    children.append(ui.Button(
        "Back to Browse", icon="ArrowLeft", variant="ghost", size="sm",
        on_click=ui.Call(
            "__panel__editor",
            note_id=conn_id, tab="results", action="run",
            sql=f"SELECT * FROM `{table}` LIMIT 200",
        ),
    ))
