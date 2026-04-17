"""sql-db · Editor panel — Run/Explain/DryRun execution + result rendering.

Single-table browse enhancements:
- If the executed SQL is a simple single-table SELECT (no JOIN/UNION) and the
  target table has a single primary key, result rows become clickable (click
  → row_form edit). An "Insert new row" button is rendered above the DataTable.
- When called with `paginate=True`, the SQL's existing LIMIT/OFFSET are stripped
  and replaced with page-based ones; a COUNT(*) query fetches the total; and
  Previous / Next / "Page N of M" controls are rendered.
"""
from __future__ import annotations

import logging
import re

from imperal_sdk import ui

from app import _api_post, build_conn_info
from sql_parser import classify_sql

log = logging.getLogger("sql-db")

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


def _detect_single_table(sql: str) -> str:
    """Return table name for simple single-table SELECT; else ''."""
    lower = f" {sql.lower()} "
    if " join " in lower or " union " in lower:
        return ""
    m = _SINGLE_TABLE_RE.match(sql)
    return m.group(1) if m else ""


def _strip_trailing_limit(sql: str) -> str:
    """Remove a trailing LIMIT/OFFSET clause so we can re-add page-based ones."""
    return _LIMIT_TAIL_RE.sub("", sql).rstrip(";").strip()


async def _fetch_pk_column(
    uid: str, conn_id: str, conn_data: dict, table: str,
) -> str:
    """Return the primary key column name for `table` (first PRI column), or ''.

    Calls `/schema` and filters to the target table. ~1 extra HTTP per render
    when single-table SELECT is detected — acceptable for v1.
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


async def _fetch_total_rows(
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


def _render_paginator(
    children: list, conn_id: str, base_sql: str, action: str,
    page: int, page_size: int, total_rows: int,
) -> None:
    """Render Previous / Next buttons + Page N of M text + page size selector."""
    if total_rows < 0:
        return  # no count → skip paginator
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
        # Page-size selector. on_change injects the new value under the
        # `page_size` key; page is reset to 0 so the user doesn't land on
        # an out-of-range offset after shrinking the window.
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


async def run_and_show(
    children: list, uid: str, conn_id: str, conn_data: dict, sql: str, action: str,
    page: int = 0, page_size: int = 50, paginate: bool = False,
) -> None:
    """Execute SQL with given action (run/explain/dry_run) and render result.

    When `paginate=True` and the statement is a simple single-table SELECT,
    the SQL's trailing LIMIT/OFFSET is stripped, replaced with page-based
    LIMIT/OFFSET, and Previous/Next controls are rendered.
    """
    sql_clean = sql.strip().rstrip(";")
    if not sql_clean:
        children.append(ui.Alert(title="Empty", message="Empty SQL", type="warning"))
        return

    first_word, is_read, is_explain = classify_sql(sql_clean)
    conn_info = build_conn_info(conn_data)

    # ── EXPLAIN mode ──────────────────────────────────────────────────
    if action == "explain":
        inner_sql = sql_clean[len("EXPLAIN"):].strip() if is_explain else sql_clean
        try:
            result = await _api_post(f"/v1/connections/{conn_id}/explain", {
                "user_id": uid, "sql": inner_sql, "connection": conn_info,
            })
        except Exception as e:
            children.append(ui.Alert(title="Request failed", message=str(e), type="error"))
            return

        if result.get("status") == "error":
            children.append(ui.Alert(
                title="EXPLAIN Error",
                message=result.get("detail", "Unknown error"), type="error",
            ))
            return

        plan = result.get("plan", [])
        children.append(ui.Alert(
            title="EXPLAIN", message=f"{len(plan)} step(s)", type="info",
        ))
        if plan:
            cols = [ui.DataColumn(key=k, label=k) for k in plan[0].keys()]
            rows = [{"id": str(i), **{k: str(v) for k, v in row.items()}}
                    for i, row in enumerate(plan)]
            children.append(ui.DataTable(columns=cols, rows=rows))
        return

    # ── DRY RUN mode ──────────────────────────────────────────────────
    if action == "dry_run":
        if is_read:
            children.append(ui.Alert(
                title="Dry Run skipped",
                message="Dry run is for DML only (INSERT/UPDATE/DELETE). Use Run for SELECT.",
                type="warning",
            ))
            return
        try:
            result = await _api_post(f"/v1/connections/{conn_id}/dry_run", {
                "user_id": uid, "sql": sql_clean, "connection": conn_info,
            })
        except Exception as e:
            children.append(ui.Alert(title="Request failed", message=str(e), type="error"))
            return

        if result.get("status") == "error":
            children.append(ui.Alert(
                title="Dry Run Error",
                message=result.get("detail", "Unknown error"), type="error",
            ))
            return

        would = result.get("would_affect", 0)
        exec_ms = result.get("exec_ms", 0)
        qtype = result.get("query_type", first_word)
        children.append(ui.Alert(
            title=f"Dry Run {qtype}",
            message=f"Would affect {would} row(s) · {exec_ms}ms (rolled back, no changes)",
            type="info",
        ))
        return

    # ── Browse detection + pagination prep ────────────────────────────
    table = _detect_single_table(sql_clean) if is_read else ""
    pk_col = ""
    base_sql = sql_clean
    total_rows = -1
    paging_on = False

    if paginate and table:
        base_sql = _strip_trailing_limit(sql_clean)
        sql_to_run = f"{base_sql} LIMIT {page_size} OFFSET {page * page_size}"
        total_rows = await _fetch_total_rows(uid, conn_id, conn_data, table)
        paging_on = True
    else:
        sql_to_run = sql_clean

    # ── RUN mode ──────────────────────────────────────────────────────
    async def _call_query():
        return await _api_post(f"/v1/connections/{conn_id}/query", {
            "user_id": uid, "sql": sql_to_run,
            "limit": page_size if paging_on else 200,
            "connection": conn_info,
        })

    async def _call_execute():
        return await _api_post(f"/v1/connections/{conn_id}/execute", {
            "user_id": uid, "sql": sql_clean, "confirmed": True,
            "connection": conn_info,
        })

    try:
        if is_explain:
            inner_sql = sql_clean[len("EXPLAIN"):].strip()
            result = await _api_post(f"/v1/connections/{conn_id}/explain", {
                "user_id": uid, "sql": inner_sql, "connection": conn_info,
            })
            if result.get("status") != "error":
                plan = result.get("plan", [])
                children.append(ui.Alert(
                    title="EXPLAIN", message=f"{len(plan)} step(s)", type="info",
                ))
                if plan:
                    cols = [ui.DataColumn(key=k, label=k) for k in plan[0].keys()]
                    rows = [{"id": str(i), **{k: str(v) for k, v in row.items()}}
                            for i, row in enumerate(plan)]
                    children.append(ui.DataTable(columns=cols, rows=rows))
                return
        elif is_read:
            result = await _call_query()
            if (result.get("status") == "error"
                    and "Use /execute" in str(result.get("detail", ""))):
                is_read = False
                result = await _call_execute()
        else:
            result = await _call_execute()
            if (result.get("status") == "error"
                    and "Use /query" in str(result.get("detail", ""))):
                is_read = True
                result = await _call_query()
    except Exception as e:
        children.append(ui.Alert(title="Request failed", message=str(e), type="error"))
        return

    if result.get("status") == "error":
        children.append(ui.Alert(
            title="SQL Error",
            message=result.get("detail", "Unknown error"),
            type="error",
        ))
        return

    exec_ms = result.get("exec_ms", 0)

    if is_read:
        page_rows = result.get("total_rows", 0)
        columns = result.get("columns", [])
        raw_rows = result.get("rows", [])

        if paging_on and total_rows >= 0:
            start = page * page_size + 1 if page_rows > 0 else 0
            end = page * page_size + page_rows
            alert_title = f"Showing rows {start}-{end} of {total_rows}"
        else:
            alert_title = f"{page_rows} row(s)"
        children.append(ui.Alert(
            title=alert_title,
            message=f"Executed in {exec_ms}ms",
            type="success" if page_rows > 0 else "info",
        ))

        if table:
            pk_col = await _fetch_pk_column(uid, conn_id, conn_data, table)
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
                columns=cols, rows=rows,
                on_row_click=on_row_click,
            ) if on_row_click else ui.DataTable(columns=cols, rows=rows))
        elif page_rows == 0:
            children.append(ui.Empty(message="No rows returned", icon="TableProperties"))

        # Paginator after DataTable
        if paging_on:
            _render_paginator(
                children, conn_id, base_sql, action,
                page, page_size, total_rows,
            )

    else:
        affected = result.get("rows_affected", 0)
        qtype = result.get("query_type", first_word)
        children.append(ui.Alert(
            title=f"{qtype} success",
            message=f"{affected} row(s) affected · {exec_ms}ms",
            type="success",
        ))
