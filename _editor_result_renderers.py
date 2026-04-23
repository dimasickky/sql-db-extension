"""sql-db · Editor result rendering helpers.

Pagination, single-table detection, paginator, row-form wiring. No I/O
beyond backend round-trips for PK lookup / COUNT(*).
"""
from __future__ import annotations

import re

from imperal_sdk import ui

from app import _api_post, build_conn_info


# Detect a simple SELECT over a single table. Captures the table identifier
# (optionally backticked). Rejects JOIN and UNION anywhere in the statement.
_SINGLE_TABLE_RE = re.compile(
    r"^\s*SELECT\s+.+?\s+FROM\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
    re.IGNORECASE | re.DOTALL,
)

# Strip trailing LIMIT [offset,] n  OR  LIMIT n OFFSET m at the end.
_LIMIT_TAIL_RE = re.compile(
    r"\s+LIMIT\s+\d+(?:\s*,\s*\d+|\s+OFFSET\s+\d+)?\s*;?\s*$",
    re.IGNORECASE,
)


def detect_single_table(sql: str) -> str:
    """Return table name for simple single-table SELECT; else ''."""
    lower = f" {sql.lower()} "
    if " join " in lower or " union " in lower:
        return ""
    m = _SINGLE_TABLE_RE.match(sql)
    return m.group(1) if m else ""


def strip_trailing_limit(sql: str) -> str:
    """Remove a trailing LIMIT/OFFSET clause so we can re-add page-based ones."""
    return _LIMIT_TAIL_RE.sub("", sql).rstrip(";").strip()


async def fetch_pk_column(
    uid: str, conn_id: str, conn_data: dict, table: str,
) -> str:
    """Return the primary key column name for `table` (first PRI column), or ''.

    Calls `/schema` and filters to the target table. ~1 extra HTTP per
    render when single-table SELECT is detected.
    """
    database = conn_data.get("database", "")
    try:
        schema_res = await _api_post(f"/v1/connections/{conn_id}/schema", {
            "user_id": uid, "database": database,
            "connection": build_conn_info(conn_data),
        })
    except Exception:
        return ""
    for t in schema_res.get("tables", []):
        if t.get("name") != table:
            continue
        for c in t.get("columns", []):
            if c.get("COLUMN_KEY") == "PRI":
                return c.get("COLUMN_NAME", "")
    return ""


async def fetch_total_rows(
    uid: str, conn_id: str, conn_data: dict, table: str,
) -> int:
    """Run SELECT COUNT(*) for pagination total; return -1 on failure."""
    try:
        result = await _api_post(f"/v1/connections/{conn_id}/query", {
            "user_id": uid,
            "sql": f"SELECT COUNT(*) AS cnt FROM `{table}`",
            "limit": 1,
            "connection": build_conn_info(conn_data),
        })
    except Exception:
        return -1
    if result.get("status") == "error":
        return -1
    rows = result.get("rows", [])
    if not rows:
        return -1
    try:
        return int(rows[0].get("cnt", -1))
    except (TypeError, ValueError):
        return -1


_PAGE_SIZE_OPTIONS = [
    {"value": "10", "label": "10 / page"},
    {"value": "25", "label": "25 / page"},
    {"value": "50", "label": "50 / page"},
    {"value": "100", "label": "100 / page"},
    {"value": "200", "label": "200 / page"},
    {"value": "500", "label": "500 / page"},
]


def render_paginator(
    children: list, conn_id: str, base_sql: str, action: str,
    page: int, page_size: int, total_rows: int,
) -> None:
    """Render Previous / Next buttons + Page N of M text + page size selector."""
    if total_rows < 0:
        return
    total_pages = max((total_rows + page_size - 1) // page_size, 1)
    has_prev = page > 0
    has_next = page + 1 < total_pages

    children.append(ui.Stack([
        ui.Button(
            "Previous", icon="ChevronLeft",
            variant="ghost" if has_prev else "primary",
            size="sm",
            disabled=not has_prev,
            on_click=ui.Call(
                "__panel__editor",
                note_id=conn_id, tab="results", action=action,
                sql=base_sql,
                page=str(max(page - 1, 0)), page_size=str(page_size),
            ),
        ),
        ui.Text(
            f"Page {page + 1} of {total_pages}  ·  {total_rows} row(s) total",
            variant="caption",
        ),
        ui.Button(
            "Next", icon="ChevronRight",
            variant="ghost" if has_next else "primary",
            size="sm",
            disabled=not has_next,
            on_click=ui.Call(
                "__panel__editor",
                note_id=conn_id, tab="results", action=action,
                sql=base_sql,
                page=str(page + 1), page_size=str(page_size),
            ),
        ),
        ui.Select(
            param_name="page_size",
            value=str(page_size),
            options=_PAGE_SIZE_OPTIONS,
            on_change=ui.Call(
                "__panel__editor",
                note_id=conn_id, tab="results", action=action,
                sql=base_sql, page="0",
            ),
        ),
    ], direction="horizontal", gap=2))


def render_select_result(
    children: list, conn_id: str, table: str, pk_col: str,
    columns: list, raw_rows: list,
) -> None:
    """Render the DataTable + Insert button for a SELECT result."""
    if table:
        children.append(ui.Stack([
            ui.Button(
                f"Insert new row into {table}",
                icon="Plus", variant="primary", size="sm",
                on_click=ui.Call(
                    "__panel__editor",
                    note_id=conn_id, tab="row_form",
                    table=table, mode="insert",
                ),
            ),
        ], direction="horizontal"))

    if columns and raw_rows:
        cols = [ui.DataColumn(key=c, label=c) for c in columns]
        rows = []
        for i, row in enumerate(raw_rows):
            if pk_col and pk_col in row and row[pk_col] is not None:
                row_id = str(row[pk_col])
            else:
                row_id = str(i)
            rows.append({
                "id": row_id,
                **{c: str(row.get(c, "")) for c in columns},
            })

        on_row_click = None
        if table and pk_col:
            on_row_click = ui.Call(
                "__panel__editor",
                note_id=conn_id, tab="row_form",
                table=table, mode="edit",
                pk_col=pk_col,
            )
        children.append(ui.DataTable(
            columns=cols, rows=rows, on_row_click=on_row_click,
        ) if on_row_click else ui.DataTable(columns=cols, rows=rows))
    elif not raw_rows:
        children.append(ui.Empty(message="No rows returned", icon="TableProperties"))
