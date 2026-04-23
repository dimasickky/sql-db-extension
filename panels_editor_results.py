"""sql-db · Editor panel — Run/Explain/DryRun execution orchestration.

Single-table browse enhancements live here as the orchestrator; renderers
(pagination, DataTable, paginator) are in `_editor_result_renderers.py`.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import _api_post, build_conn_info
from sql_parser import classify_sql
from _editor_result_renderers import (
    detect_single_table,
    strip_trailing_limit,
    fetch_pk_column,
    fetch_total_rows,
    render_paginator,
    render_select_result,
)

log = logging.getLogger("sql-db")


async def _pulse_sql_executed(ctx) -> None:
    """Emit sql.executed so the sidebar schema re-fetches row counts.

    Panel Execute bypasses @chat.function, so kernel auto-event-publishing
    doesn't fire. We nudge it via a tiny internal function that has
    event="sql.executed" on its decorator. Failures are swallowed — the
    event is a UX nicety, never load-bearing.
    """
    try:
        if hasattr(ctx, "extensions") and ctx.extensions is not None:
            await ctx.extensions.call("sql-db", "_pulse_sql_executed",
                                      {"kind": "editor_dml"})
    except Exception as e:
        log.debug("sql.executed pulse skipped: %s", e)


async def _run_explain(
    children: list, uid: str, conn_id: str, conn_info: dict,
    sql_clean: str, is_explain: bool,
) -> None:
    """EXPLAIN path — own-function block."""
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


async def _run_dry_run(
    children: list, uid: str, conn_id: str, conn_info: dict,
    sql_clean: str, first_word: str, is_read: bool,
) -> None:
    """Dry-run path — wraps backend /dry_run."""
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


async def run_and_show(
    children: list, ctx, uid: str, conn_id: str, conn_data: dict, sql: str, action: str,
    page: int = 0, page_size: int = 50, paginate: bool = False,
) -> None:
    """Execute SQL with given action (run/explain/dry_run) and render result."""
    sql_clean = sql.strip().rstrip(";")
    if not sql_clean:
        children.append(ui.Alert(title="Empty", message="Empty SQL", type="warning"))
        return

    first_word, is_read, is_explain = classify_sql(sql_clean)
    conn_info = build_conn_info(conn_data)

    if action == "explain":
        await _run_explain(children, uid, conn_id, conn_info, sql_clean, is_explain)
        return

    if action == "dry_run":
        await _run_dry_run(children, uid, conn_id, conn_info, sql_clean, first_word, is_read)
        return

    # ── Browse detection + pagination prep ────────────────────────────
    table = detect_single_table(sql_clean) if is_read else ""
    base_sql = sql_clean
    total_rows = -1
    paging_on = False
    sql_to_run = sql_clean

    if paginate and table:
        base_sql = strip_trailing_limit(sql_clean)
        sql_to_run = f"{base_sql} LIMIT {page_size} OFFSET {page * page_size}"
        total_rows = await fetch_total_rows(uid, conn_id, conn_data, table)
        paging_on = True

    # ── RUN mode with inline self-repair (read↔write retry) ──────────
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
            await _run_explain(children, uid, conn_id, conn_info, sql_clean, True)
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

        pk_col = await fetch_pk_column(uid, conn_id, conn_data, table) if table else ""
        render_select_result(children, conn_id, table, pk_col, columns, raw_rows)

        if paging_on:
            render_paginator(
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
        await _pulse_sql_executed(ctx)
