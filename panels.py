"""sql-db · Sidebar panel (left) — cache-only render (Phase 2 — sql-db-scale).

The panel render path NEVER awaits an HTTP call to db-service. All schema
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

from datetime import datetime, timezone

from app import (
    ext,
    _api_post,
    _api_catalog,
    _api_tables_page,
    _user_id,
    build_conn_info,
    CONN_COLLECTION,
    # Tier-2 cache models + key helpers
    CatalogCache,
    CatalogDb,
    TablesPageCache,
    TablesPageItem,
    cache_key_catalog,
    cache_key_tables_page,
    SIDEBAR_PAGE_LIMIT,
    CATALOG_CACHE_TTL,
    TABLES_PAGE_CACHE_TTL,
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
                ui.Password(placeholder="Password", param_name="password"),
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
            schema_block = await _render_schema_block(ctx, conn, active_id, database)
            children.extend(schema_block)

    return ui.Stack(children=children, gap=2, className="min-h-full")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _render_schema_block(ctx, conn: dict, conn_id: str, database: str) -> list:
    """Render the schema tree.

    Cache-first: read CatalogCache + first TablesPageCache. If either is
    cold, populate INLINE in this same render — kernel's `@ext.on_event`
    contract on this platform passes ``ctx=None`` to handlers, so we can't
    delegate cache writes to a background event handler. Inline populate
    is bounded by db-service's MAX_STATEMENT_TIME=5s on every introspection
    session, so worst case the panel paints with placeholder + empty error
    state in ~5–8 s rather than spinning forever.

    On warm cache the path is two `ctx.cache.get` reads — sub-millisecond.
    """
    out: list = [ui.Divider(f"Schema: {database}")]

    cat = await _safe_cache_get(ctx, CatalogCache, cache_key_catalog(conn_id))
    page_key = cache_key_tables_page(conn_id, database, "", 0, SIDEBAR_PAGE_LIMIT)
    page = await _safe_cache_get(ctx, TablesPageCache, page_key)

    if cat is None or page is None:
        # Cold cache — populate inline now, write through, render.
        cat, page = await _populate_inline(ctx, conn, conn_id, database)

    if cat is None or page is None:
        # Inline populate failed (target DB unreachable, auth error, …).
        # Surface a real error rather than a stuck spinner.
        out.append(ui.Empty(
            message="Schema unavailable — target DB unreachable or rejected the connection.",
            icon="AlertTriangle",
        ))
        return out

    if not page.items:
        out.append(ui.Empty(message="No tables", icon="Table"))
        return out

    items = [_table_list_item(conn_id, t) for t in page.items]
    title = f"Schema: {database} ({page.total_count} tables)"
    out[0] = ui.Divider(title)
    out.append(ui.List(items=items, page_size=50, searchable=True))
    return out


async def _safe_cache_get(ctx, model_cls, key):
    """ctx.cache.get that returns None on any error (miss or backend hiccup)."""
    try:
        return await ctx.cache.get(model_cls, key)
    except Exception:
        return None


async def _populate_inline(ctx, conn: dict, conn_id: str, database: str):
    """Cold-cache populator — runs on the panel render path with the live ctx.

    Returns (CatalogCache, TablesPageCache) or (None, None) on failure.
    Writes both envelopes to ctx.cache before returning so subsequent
    renders are warm-cache fast.
    """
    cat_obj: CatalogCache | None = None
    page_obj: TablesPageCache | None = None

    # T0 — catalog
    try:
        cat_resp = await _api_catalog(ctx, conn, conn_id)
        if cat_resp.get("status") == "ok":
            cat_obj = CatalogCache(
                conn_id=conn_id,
                databases=[
                    CatalogDb(
                        name=d.get("name", ""),
                        table_count=int(d.get("table_count") or 0),
                        schema_version=d.get("schema_version") or "",
                    )
                    for d in cat_resp.get("databases", [])
                ],
                fetched_at=_now_iso(),
            )
            try:
                await ctx.cache.set(
                    cache_key_catalog(conn_id), cat_obj,
                    ttl_seconds=CATALOG_CACHE_TTL,
                )
            except Exception as exc:
                log.warning("CatalogCache write failed: %s", exc)
        else:
            log.warning("T0 catalog: %s", cat_resp.get("detail"))
    except Exception as exc:
        log.warning("T0 catalog raised: %s", exc)

    # T1 — first 200 tables of the active DB, no search, offset=0
    try:
        page_resp = await _api_tables_page(
            ctx, conn, conn_id, database,
            search="", offset=0, limit=SIDEBAR_PAGE_LIMIT,
        )
        if page_resp.get("status") == "ok":
            page_obj = TablesPageCache(
                conn_id=conn_id,
                database=database,
                search="",
                offset=0,
                limit=SIDEBAR_PAGE_LIMIT,
                items=[
                    TablesPageItem(
                        name=i.get("name", ""),
                        type=i.get("type", "BASE TABLE"),
                        engine=i.get("engine", ""),
                        rows_estimate=int(i.get("rows_estimate") or 0),
                        size_bytes=int(i.get("size_bytes") or 0),
                        last_modified=i.get("last_modified"),
                        comment=i.get("comment", ""),
                    )
                    for i in page_resp.get("items", [])
                ],
                total_count=int(page_resp.get("total_count") or 0),
                schema_version=page_resp.get("schema_version") or "",
                fetched_at=_now_iso(),
            )
            try:
                await ctx.cache.set(
                    cache_key_tables_page(
                        conn_id, database, "", 0, SIDEBAR_PAGE_LIMIT,
                    ),
                    page_obj,
                    ttl_seconds=TABLES_PAGE_CACHE_TTL,
                )
            except Exception as exc:
                log.warning("TablesPageCache write failed: %s", exc)
        else:
            log.warning("T1 first-page: %s", page_resp.get("detail"))
    except Exception as exc:
        log.warning("T1 first-page raised: %s", exc)

    return cat_obj, page_obj


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

    # Optimistic-UI signal: a freshly touched table gets a non-blocking
    # "just now" Badge so the user sees their DML reflected immediately.
    # ui.ListItem has no className/CSS hook in SDK 3.4.x, so we lean on
    # `badge` (a built-in slot) instead of injecting a class name.
    just_touched_badge = (
        ui.Badge("just now", color="blue") if t.last_touched_at else None
    )

    return ui.ListItem(
        id=name,
        title=name,
        subtitle=" · ".join(subtitle_parts),
        icon="Table",
        badge=just_touched_badge,
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
