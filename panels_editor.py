"""sql-db · Editor panel (center overlay) — self-contained SQL editor with ui.Form."""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import (
    ext, _api_get, _api_post, _user_id,
    resolve_connection, build_conn_info, CONN_COLLECTION,
)

log = logging.getLogger("sql-db")


@ext.panel("editor", slot="center", title="SQL Editor", icon="Code")
async def sql_editor(ctx, note_id: str = "", tab: str = "editor",
                     sql: str = "", action: str = "run", **kwargs):
    """SQL editor — self-contained. Executes queries and shows results in-panel."""
    uid = _user_id(ctx)

    log.info("sql_editor called: tab=%s action=%s sql=%r note_id=%s kwargs=%s",
             tab, action, sql[:50] if sql else "", note_id, list(kwargs.keys()))

    if not note_id:
        return ui.Empty(message="Open SQL Editor from sidebar", icon="Code")

    # ── Resolve connection ────────────────────────────────────────────
    conn_data = None
    conn_id = note_id

    try:
        doc = await ctx.store.get(CONN_COLLECTION, conn_id)
        if doc:
            conn_data = doc.data
    except Exception:
        pass

    if not conn_data:
        conn_data, conn_id = await resolve_connection(ctx)

    if not conn_data:
        return ui.Empty(message="Connection not found", icon="DatabaseZap")

    conn_name = conn_data.get("name", "")
    database = conn_data.get("database", "")

    # ── Action bar (sticky) ───────────────────────────────────────────
    action_bar = ui.Stack([
        ui.Button("Back", icon="ArrowLeft", variant="ghost", size="sm",
                  on_click=ui.Call("__panel__sidebar",
                                  view="main", active_conn_id="")),
        ui.Text(f"{conn_name} → {database}", variant="caption"),
    ], direction="horizontal", wrap=True, sticky=True)

    # ── Nav tabs ──────────────────────────────────────────────────────
    nav_tabs = ui.Stack([
        ui.Button("Editor", icon="Code", size="sm",
                  variant="primary" if tab in ("editor", "results") else "ghost",
                  on_click=ui.Call("__panel__editor",
                                  note_id=conn_id, tab="editor", sql="")),
        ui.Button("History", icon="Clock", size="sm",
                  variant="primary" if tab == "history" else "ghost",
                  on_click=ui.Call("__panel__editor",
                                  note_id=conn_id, tab="history", sql="")),
        ui.Button("Saved", icon="Bookmark", size="sm",
                  variant="primary" if tab == "saved" else "ghost",
                  on_click=ui.Call("__panel__editor",
                                  note_id=conn_id, tab="saved", sql="")),
    ], direction="horizontal")

    children = [action_bar, nav_tabs]

    if tab == "results":
        if sql:
            statements = _split_statements(sql)
            if len(statements) > 1:
                children.append(ui.Alert(
                    title=f"{len(statements)} statements",
                    message=f"Action: {action} · executing sequentially…",
                    type="info",
                ))
            for i, stmt in enumerate(statements, 1):
                if len(statements) > 1:
                    children.append(ui.Divider(f"[{i}/{len(statements)}] {stmt[:60]}…"))
                await _run_and_show(children, uid, conn_id, conn_data, stmt, action)
        else:
            children.append(ui.Alert(
                title="No SQL",
                message="Type SQL in the editor and press Run.",
                type="warning",
            ))
        _append_form(children, sql or "", conn_id, action)
    elif tab == "history":
        await _append_history(children, uid, conn_id)
        _append_form(children, "", conn_id, "run")
    elif tab == "saved":
        await _append_saved(children, uid, conn_id)
        _append_form(children, "", conn_id, "run")
    else:
        _append_form(children, sql, conn_id, action)

    return ui.Stack(children=children, gap=2, className="px-4 pb-4")


# ── SQL Form ──────────────────────────────────────────────────────────────

def _append_form(children: list, sql: str, conn_id: str, action: str) -> None:
    """SQL input form with action selector."""
    children.append(ui.Form(
        action="__panel__editor",
        submit_label="Execute",
        defaults={"note_id": conn_id, "tab": "results"},
        children=[
            ui.Text("Action", variant="caption"),
            ui.Select(
                param_name="action",
                value=action,
                options=[
                    {"value": "run", "label": "Run (query or DML/DDL)"},
                    {"value": "explain", "label": "Explain plan"},
                    {"value": "dry_run", "label": "Dry run (DML only — rollback after)"},
                ],
            ),
            ui.Text("SQL", variant="caption"),
            ui.TextArea(
                placeholder=(
                    "SELECT * FROM table LIMIT 10;\n"
                    "-- Multiple statements supported (separated by ;)\n"
                    "ALTER TABLE ...;\n"
                    "UPDATE ... WHERE ...;"
                ),
                value=sql,
                param_name="sql",
                rows=8,
            ),
        ],
    ))


# ── Statement splitter ────────────────────────────────────────────────────

def _split_statements(sql: str) -> list[str]:
    """Split SQL on ; outside of quotes. Returns non-empty trimmed statements."""
    parts: list[str] = []
    buf: list[str] = []
    quote = None
    i = 0
    while i < len(sql):
        c = sql[i]
        if quote:
            buf.append(c)
            if c == quote and sql[i - 1] != "\\":
                quote = None
        else:
            if c in ("'", '"', "`"):
                quote = c
                buf.append(c)
            elif c == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    parts.append(stmt)
                buf = []
            else:
                buf.append(c)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


# ── Classify SQL (read vs write vs explain) ───────────────────────────────

def _classify_sql(sql_clean: str) -> tuple[str, bool, bool]:
    """Return (first_word, is_read, is_explain). Strips leading comments."""
    s = sql_clean
    while s.startswith("--") or s.startswith("/*"):
        if s.startswith("--"):
            nl = s.find("\n")
            s = s[nl + 1:].strip() if nl >= 0 else ""
        else:
            end = s.find("*/")
            s = s[end + 2:].strip() if end >= 0 else ""

    if not s:
        return "", True, False

    first_word = s.split()[0].upper()
    is_explain = first_word == "EXPLAIN"

    if first_word == "WITH":
        lower = " " + s.lower() + " "
        has_write = any(kw in lower for kw in
                        (" insert ", " update ", " delete ", " replace "))
        is_read = not has_write
    else:
        is_read = first_word in ("SELECT", "SHOW", "DESCRIBE", "DESC")

    return first_word, is_read, is_explain


# ── Execute and render results ────────────────────────────────────────────

async def _run_and_show(
    children: list, uid: str, conn_id: str, conn_data: dict, sql: str, action: str,
) -> None:
    """Execute SQL with given action (run/explain/dry_run) and render result."""
    sql_clean = sql.strip().rstrip(";")
    if not sql_clean:
        children.append(ui.Alert(title="Empty", message="Empty SQL", type="warning"))
        return

    first_word, is_read, is_explain = _classify_sql(sql_clean)
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
            # User typed "EXPLAIN ..." in Run mode — dispatch to /explain
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

        if columns and raw_rows:
            cols = [ui.DataColumn(key=c, label=c) for c in columns]
            rows = [{"id": str(i), **{c: str(row.get(c, "")) for c in columns}}
                    for i, row in enumerate(raw_rows)]
            children.append(ui.DataTable(columns=cols, rows=rows))
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


# ── History tab ───────────────────────────────────────────────────────────

async def _append_history(children: list, uid: str, conn_id: str) -> None:
    """Query history."""
    try:
        result = await _api_get(
            f"/v1/connections/{conn_id}/history",
            {"user_id": uid, "limit": 30},
        )
        history = result.get("history", [])
    except Exception:
        history = []

    if not history:
        children.append(ui.Empty(message="No query history yet", icon="Clock"))
        return

    columns = [
        ui.DataColumn(key="sql_text", label="SQL", width="50%"),
        ui.DataColumn(key="query_type", label="Type", width="10%"),
        ui.DataColumn(key="rows_affected", label="Rows", width="10%"),
        ui.DataColumn(key="exec_ms", label="ms", width="10%"),
        ui.DataColumn(key="created_at", label="When", width="20%"),
    ]

    rows = []
    for h in history:
        sql_text = h.get("sql_text", "")
        rows.append({
            "id": str(h.get("id", "")),
            "sql_text": sql_text[:80] + ("..." if len(sql_text) > 80 else ""),
            "query_type": h.get("query_type", ""),
            "rows_affected": str(h.get("rows_affected", 0)),
            "exec_ms": str(h.get("exec_ms", 0)),
            "created_at": h.get("created_at", "")[:16],
        })

    children.append(ui.DataTable(columns=columns, rows=rows))


# ── Saved queries tab ────────────────────────────────────────────────────

async def _append_saved(children: list, uid: str, conn_id: str) -> None:
    """Saved queries list."""
    try:
        result = await _api_get(
            f"/v1/connections/{conn_id}/saved",
            {"user_id": uid},
        )
        queries = result.get("saved_queries", [])
    except Exception:
        queries = []

    if not queries:
        children.append(ui.Empty(message="No saved queries", icon="Bookmark"))
        return

    items = []
    for q in queries:
        items.append(ui.ListItem(
            id=q["id"],
            title=q.get("name", "Untitled"),
            subtitle=q.get("sql_text", "")[:60],
            meta=q.get("updated_at", "")[:16],
            on_click=ui.Call("__panel__editor",
                            note_id=conn_id, tab="results", action="run",
                            sql=q.get("sql_text", "")),
        ))

    children.append(ui.List(items=items))
