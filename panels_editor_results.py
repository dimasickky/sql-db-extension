"""sql-db · Editor panel — Run/Explain/DryRun execution + result rendering.

If the executed SQL is a simple single-table SELECT (no JOIN/UNION/subquery)
and the target table has a single primary key, result rows become clickable:
click → opens the row_form tab in edit mode. An "Insert new row" button is
rendered above the DataTable.
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


def _detect_single_table(sql: str) -> str:
    """Return table name for simple single-table SELECT; else ''."""
    lower = f" {sql.lower()} "
    if " join " in lower or " union " in lower:
        return ""
    m = _SINGLE_TABLE_RE.match(sql)
    return m.group(1) if m else ""


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


async def run_and_show(
    children: list, uid: str, conn_id: str, conn_data: dict, sql: str, action: str,
) -> None:
    """Execute SQL with given action (run/explain/dry_run) and render result."""
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

    # ── RUN mode ──────────────────────────────────────────────────────
    async def _call_query():
        return await _api_post(f"/v1/connections/{conn_id}/query", {
            "user_id": uid, "sql": sql_clean, "limit": 200,
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
        total = result.get("total_rows", 0)
        columns = result.get("columns", [])
        raw_rows = result.get("rows", [])

        children.append(ui.Alert(
            title=f"{total} row(s)",
            message=f"Executed in {exec_ms}ms",
            type="success" if total > 0 else "info",
        ))

        # Single-table browse detection → row_form interactivity
        table = _detect_single_table(sql_clean)
        pk_col = ""
        if table:
            pk_col = await _fetch_pk_column(uid, conn_id, conn_data, table)

        if table:
            # Insert button is always shown for single-table SELECT, even if no PK
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
                # If PK detected, use it as the stable row id so on_row_click
                # can route to row_form edit. Otherwise synthetic index.
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
                # DataTable injects the clicked row dict as `row` kwarg on the
                # target handler — pk_value is pulled from row[pk_col] there.
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
        elif total == 0:
            children.append(ui.Empty(message="No rows returned", icon="TableProperties"))

    else:
        affected = result.get("rows_affected", 0)
        qtype = result.get("query_type", first_word)
        children.append(ui.Alert(
            title=f"{qtype} success",
            message=f"{affected} row(s) affected · {exec_ms}ms",
            type="success",
        ))
