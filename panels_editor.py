"""sql-db · Editor panel (center overlay) — dispatcher + SQL form.

Tabs:
  - editor            : SQL input form
  - results           : multi-statement execution + DataTable/Alerts
  - history           : recent queries
  - saved             : saved queries list
  - row_form          : type-aware Insert/Edit form for a single row
  - row_form_submit   : form submission target (dispatches to /row endpoint)
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import ext, _user_id, resolve_connection, CONN_COLLECTION
from sql_parser import split_statements
from panels_editor_results import run_and_show
from panels_editor_tabs import append_history, append_saved
from panels_editor_row_form import append_row_form, process_row_form_submit

log = logging.getLogger("sql-db")


@ext.panel("editor", slot="center", title="SQL Editor", icon="Code",
           refresh="on_event:row.inserted,row.updated,row.deleted")
async def sql_editor(ctx, note_id: str = "", tab: str = "editor",
                     sql: str = "", action: str = "run",
                     table: str = "", mode: str = "insert",
                     pk_col: str = "", pk_value: str = "",
                     **kwargs):
    """SQL editor — self-contained. Executes queries and shows results in-panel."""
    uid = _user_id(ctx)

    log.info("sql_editor called: tab=%s action=%s sql=%r note_id=%s table=%s mode=%s kwargs=%s",
             tab, action, sql[:50] if sql else "", note_id, table, mode,
             list(kwargs.keys()) + [f"{k}={v!r}" for k, v in kwargs.items()][:6])

    # Cross-panel ui.Call (sidebar → center editor) drops the `tab` param —
    # only `section`/`active` travel reliably per Panel Shell semantics. We
    # land with tab="editor" (the default) even when the caller sent
    # tab="results". If sql + action are provided, infer the intent was to
    # execute → promote to "results" tab so the user sees their output.
    #
    # Sentinel `edit=1` bypasses auto-promote — sent by "Edit this SQL"
    # buttons in results/history/saved tabs that WANT tab="editor" with
    # the SQL pre-filled for modification.
    if (tab in ("editor", "") and sql
            and action in ("run", "explain", "dry_run")
            and kwargs.get("edit") not in ("1", 1, True, "true")):
        tab = "results"

    # Defensive: if an empty tab=results call arrives (mystery re-renders
    # observed in prod — likely Shell auto-refetch on mount), fall back to
    # the editor tab instead of showing "No SQL" which replaces the real
    # DataTable that was just rendered. The editor tab with empty sql
    # renders the SQL input form — a safe default state.
    if tab == "results" and not sql:
        tab = "editor"

    # DataTable on_row_click passes the clicked row as a nested `row` dict
    # in kwargs. When navigating from the results table to row_form edit, the
    # pk_value is not in the Call params — we pull it from the row dict.
    if tab == "row_form" and mode == "edit" and pk_col and not pk_value:
        row_data = kwargs.get("row")
        if isinstance(row_data, dict):
            pk_value = str(row_data.get(pk_col, ""))

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
    row_form_active = tab in ("row_form", "row_form_submit")
    nav_items = [
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
    ]
    if row_form_active:
        nav_items.append(ui.Button(
            f"Row ({mode})", icon="Edit", size="sm", variant="primary",
            # Tab already open — button is informational, no re-navigation
            on_click=ui.Call("__panel__editor",
                            note_id=conn_id, tab="row_form",
                            table=table, mode=mode,
                            pk_col=pk_col, pk_value=pk_value),
        ))
    nav_tabs = ui.Stack(nav_items, direction="horizontal")

    children = [action_bar, nav_tabs]

    if tab == "results":
        # Results tab: show DataTable + "Edit this SQL" navigation button.
        # The SQL form is intentionally NOT rendered here — it would stay
        # mounted across tab changes (useState(defaults) persists) and
        # submit stale values. User clicks "Edit this SQL" to go back to
        # tab=editor where the Form mounts fresh from current sql.
        if sql:
            statements = split_statements(sql)
            if len(statements) > 1:
                children.append(ui.Alert(
                    title=f"{len(statements)} statements",
                    message=f"Action: {action} · executing sequentially…",
                    type="info",
                ))
            for i, stmt in enumerate(statements, 1):
                if len(statements) > 1:
                    children.append(ui.Divider(f"[{i}/{len(statements)}] {stmt[:60]}…"))
                await run_and_show(children, uid, conn_id, conn_data, stmt, action)

            children.append(ui.Divider())
            children.append(ui.Stack([
                ui.Button(
                    "Edit this SQL", icon="Pencil",
                    variant="primary", size="sm",
                    on_click=ui.Call(
                        "__panel__editor",
                        note_id=conn_id, tab="editor", action=action, sql=sql,
                        edit="1",  # bypass auto-promote to results
                    ),
                ),
                ui.Button(
                    "New query", icon="Plus",
                    variant="ghost", size="sm",
                    on_click=ui.Call(
                        "__panel__editor",
                        note_id=conn_id, tab="editor", action="run", sql="",
                    ),
                ),
            ], direction="horizontal", gap=2))
        else:
            children.append(ui.Alert(
                title="No SQL",
                message="Use the Editor tab to type a query, or click a table in the sidebar.",
                type="info",
            ))
            children.append(ui.Button(
                "Open Editor", icon="Code", variant="primary", size="sm",
                on_click=ui.Call(
                    "__panel__editor",
                    note_id=conn_id, tab="editor", action="run", sql="",
                ),
            ))
    elif tab == "history":
        await append_history(children, uid, conn_id)
        children.append(ui.Divider())
        children.append(ui.Button(
            "Open Editor", icon="Code", variant="primary", size="sm",
            on_click=ui.Call(
                "__panel__editor",
                note_id=conn_id, tab="editor", action="run", sql="",
            ),
        ))
    elif tab == "saved":
        await append_saved(children, uid, conn_id)
        children.append(ui.Divider())
        children.append(ui.Button(
            "Open Editor", icon="Code", variant="primary", size="sm",
            on_click=ui.Call(
                "__panel__editor",
                note_id=conn_id, tab="editor", action="run", sql="",
            ),
        ))
    elif tab == "row_form":
        await append_row_form(
            children, ctx, uid, conn_id, conn_data,
            table=table, mode=mode, pk_col=pk_col, pk_value=pk_value,
        )
    elif tab == "row_form_submit":
        await process_row_form_submit(
            children, ctx, uid, conn_id, conn_data,
            table=table, mode=mode, pk_col=pk_col, pk_value=pk_value,
            form_params=kwargs,
        )
    else:
        _append_form(children, sql, conn_id, action)

    return ui.Stack(children=children, gap=2, className="px-4 pb-4")


# ─── SQL Form ─────────────────────────────────────────────────────────── #

def _append_form(children: list, sql: str, conn_id: str, action: str) -> None:
    """SQL input form with action selector.

    `sql` and `action` are placed into Form `defaults` (not only on the child
    components). FormContext only registers a child value after the user
    edits it — so the pre-filled TextArea `value=` doesn't travel on submit
    by itself. Putting them into `defaults` guarantees submission, and the
    Select/TextArea `value=` props are still used for the visual display.
    """
    children.append(ui.Form(
        action="__panel__editor",
        submit_label="Execute",
        defaults={
            "note_id": conn_id,
            "tab": "results",
            "sql": sql,
            "action": action,
        },
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
