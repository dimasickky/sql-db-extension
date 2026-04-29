"""sql-db · Sidebar panel (left) — cache-only render (Phase 2 — sql-db-scale).

The panel render path NEVER awaits an HTTP call to the backend. All schema
data is read from ``ctx.cache``; on a miss the panel renders an "Indexing
schema…" placeholder and emits ``schema.refresh.requested`` so the
populator (events.on_schema_refresh_requested) fills the cache off the
render path. When the populator finishes it emits ``schema.indexed`` and
the panel re-renders with data.

Refresh attribute event taxonomy:

  * connection.added / .deleted / .selected — connection list changed.
  * sql.ddl_executed                        — structure changed; refetch.
  * table.touched                           — DML happened; optimistic patch
                                              already applied to cache by
                                              events.on_table_touched.
  * schema.indexed                          — populator finished; cache warm.

Notably absent: ``row.inserted``, ``row.updated``, ``row.deleted``,
``sql.executed``. Those would re-trigger a full refetch on every keystroke
action — the original failure mode on huge databases.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import (
    ext,
    _api_post,
    _user_id,
    build_conn_info,
    CONN_COLLECTION,
    # Tier-2 cache models + key helpers
    CatalogCache,
    TablesPageCache,
    cache_key_catalog,
    cache_key_tables_page,
    SIDEBAR_PAGE_LIMIT,
)

log = logging.getLogger("sql-db")


@ext.panel(
    "sidebar", slot="left", title="SQL DB", icon="Database",
    default_width=260, min_width=200, max_width=400,
    refresh="on_event:connection.added,connection.deleted,connection.selected,"
            "sql.ddl_executed,table.touched,schema.indexed",
)
async def sql_sidebar(ctx, active_conn_id: str = "", view: str = "main", **kwargs):
    """Sidebar render — O(1) in target-DB size. Cache-only data path.

    See `extensions/sql-db-scale.md` §5 for the render contract and §6 for
    the optimistic-UI liveness model that powers the per-row pulse.
    """
    uid = _user_id(ctx)
    children: list = []

    # ── Fetch connections (ctx.store — fast, small per-user collection) ─
    try:
        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=50)
        connections = page.data
    except Exception:
        connections = []

    active_doc = None
    for doc in connections:
        if doc.data.get("is_active"):
            active_doc = doc
            break
    active_id = active_doc.id if active_doc else ""

    # ── Action bar ─────────────────────────────────────────────────────
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

    # ── New Connection form (conditional) ──────────────────────────────
    if view == "new_connection":
        children.append(ui.Card(
            title="New Connection",
            content=ui.Stack([
                ui.Input(placeholder="Connection name", param_name="name"),
                ui.Input(placeholder="Host (e.g. db.example.com)", param_name="host"),
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

    # ── Connection list ────────────────────────────────────────────────
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

    # ── Schema tree (active connection only, cache-only) ───────────────
    if active_doc:
        conn = active_doc.data
        database = conn.get("database", "")
        if database:
            schema_block = await _render_schema_block(ctx, active_id, database)
            children.extend(schema_block)

    return ui.Stack(children=children, gap=2, className="min-h-full")


async def _render_schema_block(ctx, conn_id: str, database: str) -> list:
    """Cache-only schema block. On miss: render placeholder + fire event.

    Two reads:
      * CatalogCache for the connection — gives total table count.
      * First TablesPageCache for the active database — gives rows for the
        sidebar.

    If either is cold, we render a non-blocking "Indexing schema…" empty
    state and emit ``schema.refresh.requested``. The populator runs off the
    panel render path and emits ``schema.indexed`` when done; the panel's
    refresh= attr re-renders us with data.
    """
    out: list = [ui.Divider(f"Schema: {database}")]

    try:
        cat = await ctx.cache.get(CatalogCache, cache_key_catalog(conn_id))
    except Exception:
        cat = None

    try:
        page = await ctx.cache.get(
            TablesPageCache,
            cache_key_tables_page(conn_id, database, "", 0, SIDEBAR_PAGE_LIMIT),
        )
    except Exception:
        page = None

    if cat is None or page is None:
        # Cold cache — non-blocking placeholder + ask the populator.
        out.append(ui.Empty(message="Indexing schema…", icon="Loader"))
        try:
            await ctx.events.emit("schema.refresh.requested", {
                "conn_id": conn_id, "database": database,
            })
        except Exception as exc:
            log.warning("schema.refresh.requested emit failed: %s", exc)
        return out

    if not page.items:
        out.append(ui.Empty(message="No tables", icon="Table"))
        return out

    items = [_table_list_item(conn_id, t) for t in page.items]

    # ui.List built-in pagination + search → smooth even on 50k tables.
    # `total_count` shown on the divider so the user knows there's more
    # behind the page-1 cap. Pages 2+ will be lazy-fetched on demand —
    # currently the SDK's List doesn't surface pagination event hooks for
    # us to wire that, so for now we just show first 200; phase-N spec
    # has the follow-up.
    title = f"Schema: {database} ({page.total_count} tables)"
    out[0] = ui.Divider(title)
    out.append(ui.List(items=items, page_size=50, search=True))
    return out


def _table_list_item(conn_id: str, t) -> ui.ListItem:
    """Build a ListItem for one cached TablesPageItem.

    Uses `rows_estimate` (TABLE_ROWS optimizer estimate, free) — exact
    counts are a separate T3 affordance not wired to the sidebar by default.
    Pulse highlight: items with a fresh `last_touched_at` (set by the
    optimistic-UI patcher) get a `pulse` className that the DUI renderer
    fades over ~5s.
    """
    name = t.name
    rows = t.rows_estimate
    quoted = f"`{name}`"
    select_sql = f"SELECT * FROM {quoted}"

    subtitle_parts = []
    if rows:
        subtitle_parts.append(f"~{rows} rows")
    if t.engine:
        subtitle_parts.append(t.engine)

    class_name = ""
    if t.last_touched_at:
        # The DUI side maps `pulse` to a brief CSS animation; safe no-op
        # on the chat-only render path.
        class_name = "pulse"

    return ui.ListItem(
        id=name,
        title=name,
        subtitle=" · ".join(subtitle_parts),
        icon="Table",
        className=class_name,
        on_click=ui.Call(
            "__panel__editor",
            note_id=conn_id, tab="results", action="run", sql=select_sql,
        ),
        actions=[
            {"icon": "Code",
             "label": "Open in Editor",
             "on_click": ui.Call(
                 "__panel__editor",
                 note_id=conn_id, tab="editor", action="run", sql=select_sql,
                 edit="1",
             )},
        ],
    )
