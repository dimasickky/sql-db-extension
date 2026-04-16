"""sql-db · Sidebar panel (left) — connections + clickable schema."""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import ext, _api_post, _user_id, _tenant_id, build_conn_info, CONN_COLLECTION

log = logging.getLogger("sql-db")


@ext.panel(
    "sidebar", slot="left", title="SQL DB", icon="Database",
    default_width=260, min_width=200, max_width=400,
    refresh="on_event:connection.added,connection.deleted,connection.selected,"
            "row.inserted,row.updated,row.deleted,sql.executed",
)
async def sql_sidebar(ctx, active_conn_id: str = "", view: str = "main", **kwargs):
    """Sidebar: connection list + clickable schema (tables → SELECT in editor)."""
    uid = _user_id(ctx)
    children: list = []

    # ── Fetch connections ─────────────────────────────────────────────
    try:
        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=50)
        connections = page.data
    except Exception:
        connections = []

    # Find active connection
    active_doc = None
    for doc in connections:
        if doc.data.get("is_active"):
            active_doc = doc
            break

    active_id = active_doc.id if active_doc else ""

    # ── Action bar ────────────────────────────────────────────────────
    action_buttons = [
        ui.Button("New Connection", icon="Plus", variant="primary", size="sm",
                  on_click=ui.Call("__panel__sidebar", view="new_connection")),
    ]
    if active_doc:
        action_buttons.append(
            ui.Button("SQL Editor", icon="Code", variant="ghost", size="sm",
                      on_click=ui.Call("__panel__editor", note_id=active_id)),
        )
    children.append(ui.Stack(action_buttons, direction="horizontal", wrap=True, sticky=True))

    # ── New Connection form (conditional) ─────────────────────────────
    if view == "new_connection":
        children.append(ui.Card(
            title="New Connection",
            content=ui.Stack([
                ui.Input(placeholder="Connection name", param_name="name"),
                ui.Input(placeholder="Host (e.g. 66.78.41.20)", param_name="host"),
                ui.Input(placeholder="Port", value="3306", param_name="port"),
                ui.Input(placeholder="Username", param_name="db_user"),
                ui.Input(placeholder="Password", param_name="password"),
                ui.Input(placeholder="Database name", param_name="database"),
                ui.Stack([
                    ui.Button("Connect", icon="PlugZap", variant="primary", size="sm",
                              on_click=ui.Call("add_connection")),
                    ui.Button("Cancel", variant="ghost", size="sm",
                              on_click=ui.Call("__panel__sidebar", view="main")),
                ], direction="horizontal"),
            ], gap=2),
        ))

    # ── Connection list ───────────────────────────────────────────────
    if not connections and view != "new_connection":
        children.append(ui.Empty(message="No connections yet", icon="Database"))
        return ui.Stack(children=children, gap=2)

    conn_items = []
    for doc in connections:
        d = doc.data
        is_active = d.get("is_active", False)

        conn_items.append(ui.ListItem(
            id=doc.id,
            title=d.get("name", "Unnamed"),
            subtitle=f"{d.get('host', '')} · {d.get('database', '')}",
            badge=ui.Badge("active", color="green") if is_active else None,
            selected=is_active,
            on_click=ui.Call("select_connection", connection_id=doc.id),
            actions=[
                {"icon": "Code", "on_click": ui.Call("__panel__editor", note_id=doc.id)},
                {"icon": "Trash2",
                 "on_click": ui.Call("delete_connection", connection_id=doc.id),
                 "confirm": f"Delete '{d.get('name', '')}'?"},
            ],
        ))

    if conn_items:
        children.append(ui.Divider(f"Connections ({len(conn_items)})"))
        children.append(ui.List(items=conn_items))

    # ── Clickable schema for active connection ────────────────────────
    if active_doc:
        conn = active_doc.data
        database = conn.get("database", "")

        if database:
            children.append(ui.Divider(f"Schema: {database}"))
            try:
                result = await _api_post(
                    f"/v1/connections/{active_id}/schema",
                    {"user_id": uid, "database": database,
                     "connection": build_conn_info(conn)},
                )
                tables = result.get("tables", [])
                table_items = _build_table_items(active_id, tables)
                if table_items:
                    children.append(ui.List(items=table_items))
                else:
                    children.append(ui.Empty(message="No tables", icon="Table"))
            except Exception as e:
                children.append(ui.Alert(
                    title="Schema error", message=str(e), type="warning",
                ))

    return ui.Stack(children=children, gap=2, className="min-h-full")


# ─── Schema item builder ──────────────────────────────────────────────── #

def _build_table_items(conn_id: str, tables: list[dict]) -> list:
    """Build ListItems for tables: click = SELECT *, expand = show columns."""
    items = []
    for t in tables[:60]:
        name = t.get("name", "")
        if not name:
            continue

        row_count = t.get("rows", "?")
        columns = t.get("columns", [])[:50]

        # Backtick-escape table name for MariaDB identifiers
        quoted = f"`{name}`"
        select_sql = f"SELECT * FROM {quoted} LIMIT 200"

        # Expanded content: column list with PK highlight
        col_items = []
        for c in columns:
            col_name = c.get("COLUMN_NAME", "")
            col_type = c.get("COLUMN_TYPE", "")
            col_key = c.get("COLUMN_KEY", "")
            is_pk = col_key == "PRI"
            col_items.append(ui.ListItem(
                id=f"{name}.{col_name}",
                title=col_name,
                subtitle=col_type,
                icon="Key" if is_pk else "Columns",
                badge=ui.Badge("PK", color="yellow") if is_pk else None,
            ))

        items.append(ui.ListItem(
            id=name,
            title=name,
            subtitle=f"{row_count} row(s)" if row_count != "?" else "",
            icon="Table",
            on_click=ui.Call(
                "__panel__editor",
                note_id=conn_id, tab="results", action="run", sql=select_sql,
            ),
            expandable=True,
            expanded_content=[ui.List(items=col_items)] if col_items else None,
            actions=[
                {"icon": "Code",
                 "label": "Open in Editor",
                 "on_click": ui.Call(
                     "__panel__editor",
                     note_id=conn_id, tab="editor", sql=select_sql,
                 )},
            ],
        ))

    return items
