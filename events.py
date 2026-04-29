"""sql-db · Event handlers (Phase 2 — sql-db-scale).

Three event classes drive the sidebar liveness model:

  * ``schema.refresh.requested`` — fired by the panel render path on a cache
    miss. Handler populates ``CatalogCache`` + the first ``TablesPageCache``
    via the new T0/T1 the backend tiers, then emits ``schema.indexed`` to
    re-render the panel.

  * ``sql.ddl_executed`` — fired by ``fn_run_editor_sql`` after a
    ``CREATE/DROP/ALTER/RENAME/TRUNCATE``. Handler invalidates the catalog +
    first-page caches and triggers a fresh fetch.

  * ``table.touched`` — fired by ``fn_run_editor_sql`` after a successful
    ``INSERT/UPDATE/DELETE``. Handler does an **optimistic local patch** on
    the cached ``TablesPageCache``: bumps the row estimate by the affected
    delta, sets ``last_touched_at`` so the UI can pulse the row. **No HTTP
    fetch.** A separate background reconciler will correct drift on a 60 s
    cadence (Phase 4 — not yet implemented).

Why this split:

The previous architecture used one ``sql.executed`` event that triggered a
full ``/schema`` refetch on every keystroke-level action. On a a customer database-snapshot
DB that is a 10–30 s freeze per ``INSERT``. The fix is to recognise that
INSERT does not change the schema — only the data of one row in one table —
and to update the sidebar locally from the writer's own knowledge of the
delta. We are the writer; we know what we wrote. See
``extensions/sql-db-scale.md`` §6 for the full liveness contract.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import (
    ext,
    _user_id,
    get_connection_by_id,
    _api_catalog,
    _api_tables_page,
    CatalogCache,
    CatalogDb,
    TablesPageCache,
    TablesPageItem,
    cache_key_catalog,
    cache_key_tables_page,
    cache_key_table_detail,
    CATALOG_CACHE_TTL,
    TABLES_PAGE_CACHE_TTL,
    SIDEBAR_PAGE_LIMIT,
)

log = logging.getLogger("sql-db.events")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── schema.refresh.requested ─────────────────────────────────────────── #

@ext.on_event("schema.refresh.requested")
async def on_schema_refresh_requested(ctx, data: dict | None = None) -> None:
    """Cold-cache populator. Runs OFF the panel render path so a slow target
    DB never freezes the UI.

    ``data`` carries ``{conn_id, database}``. We hit T0 (catalog) then T1
    (first 200 tables of the target database, no search, no offset). On
    success we emit ``schema.indexed`` so the sidebar re-renders.
    """
    data = data or {}
    conn_id = data.get("conn_id") or ""
    database = data.get("database") or ""
    if not conn_id:
        return

    conn = await get_connection_by_id(ctx, conn_id)
    if not conn:
        log.warning("schema.refresh.requested: conn_id=%s not found", conn_id)
        return
    database = database or conn.get("database", "")
    if not database:
        return

    # T0
    try:
        cat = await _api_catalog(ctx, conn, conn_id)
        if cat.get("status") == "ok":
            dbs = [
                CatalogDb(
                    name=d.get("name", ""),
                    table_count=int(d.get("table_count") or 0),
                    schema_version=d.get("schema_version") or "",
                )
                for d in cat.get("databases", [])
            ]
            await ctx.cache.set(
                cache_key_catalog(conn_id),
                CatalogCache(
                    conn_id=conn_id, databases=dbs, fetched_at=_now_iso(),
                ),
                ttl_seconds=CATALOG_CACHE_TTL,
            )
        else:
            log.warning("T0 catalog fetch failed: %s", cat.get("detail"))
    except Exception as exc:
        log.warning("T0 catalog fetch raised: %s", exc)

    # T1 — first page
    try:
        page = await _api_tables_page(
            ctx, conn, conn_id, database,
            search="", offset=0, limit=SIDEBAR_PAGE_LIMIT,
        )
        if page.get("status") == "ok":
            items = [
                TablesPageItem(
                    name=i.get("name", ""),
                    type=i.get("type", "BASE TABLE"),
                    engine=i.get("engine", ""),
                    rows_estimate=int(i.get("rows_estimate") or 0),
                    size_bytes=int(i.get("size_bytes") or 0),
                    last_modified=i.get("last_modified"),
                    comment=i.get("comment", ""),
                )
                for i in page.get("items", [])
            ]
            await ctx.cache.set(
                cache_key_tables_page(conn_id, database, "", 0, SIDEBAR_PAGE_LIMIT),
                TablesPageCache(
                    conn_id=conn_id, database=database, search="",
                    offset=0, limit=SIDEBAR_PAGE_LIMIT,
                    items=items,
                    total_count=int(page.get("total_count") or 0),
                    schema_version=page.get("schema_version") or "",
                    fetched_at=_now_iso(),
                ),
                ttl_seconds=TABLES_PAGE_CACHE_TTL,
            )
        else:
            log.warning("T1 first-page fetch failed: %s", page.get("detail"))
    except Exception as exc:
        log.warning("T1 first-page fetch raised: %s", exc)

    # Notify the panel — re-render now that cache is warm.
    try:
        await ctx.events.emit("schema.indexed", {
            "conn_id": conn_id, "database": database,
        })
    except Exception as exc:
        log.warning("schema.indexed emit raised: %s", exc)


# ─── sql.ddl_executed ─────────────────────────────────────────────────── #

@ext.on_event("sql.ddl_executed")
async def on_sql_ddl_executed(ctx, data: dict | None = None) -> None:
    """DDL changed the structure — invalidate caches and refetch.

    Cheap: just delete catalog + first-page keys, then emit a fresh
    ``schema.refresh.requested`` so the standard cold-cache populator runs.
    """
    data = data or {}
    conn_id = data.get("conn_id") or ""
    database = data.get("database") or ""
    if not conn_id:
        return

    try:
        await ctx.cache.delete(cache_key_catalog(conn_id))
    except Exception:
        pass
    if database:
        try:
            await ctx.cache.delete(
                cache_key_tables_page(conn_id, database, "", 0, SIDEBAR_PAGE_LIMIT),
            )
        except Exception:
            pass
        # Per-table detail caches: best-effort wipe of the explicit target.
        target = data.get("target_table") or ""
        if target:
            try:
                await ctx.cache.delete(cache_key_table_detail(conn_id, database, target))
            except Exception:
                pass

    try:
        await ctx.events.emit("schema.refresh.requested", {
            "conn_id": conn_id, "database": database,
        })
    except Exception as exc:
        log.warning("schema.refresh.requested re-emit raised: %s", exc)


# ─── table.touched ────────────────────────────────────────────────────── #

@ext.on_event("table.touched")
async def on_table_touched(ctx, data: dict | None = None) -> None:
    """Optimistic local patch. We are the writer — we know the delta.

    Reads the cached first-page envelope, finds the affected ListItem,
    bumps its ``rows_estimate`` by the signed delta, sets ``last_touched_at``,
    writes the envelope back. No HTTP fetch. Sidebar re-renders on this same
    event (panels.py refresh= attr) and the user sees the row light up.
    """
    data = data or {}
    conn_id = data.get("conn_id") or ""
    database = data.get("database") or ""
    table = data.get("table") or ""
    kind = data.get("kind") or ""        # insert | update | delete
    delta = int(data.get("row_delta") or 0)
    if not (conn_id and database and table):
        return

    key = cache_key_tables_page(conn_id, database, "", 0, SIDEBAR_PAGE_LIMIT)
    try:
        page = await ctx.cache.get(TablesPageCache, key)
    except Exception:
        page = None
    if page is None:
        # Cold cache — nothing to patch. Next render fetches fresh.
        return

    changed = False
    for item in page.items:
        if item.name == table:
            if kind == "insert":
                item.rows_estimate = max(0, item.rows_estimate + delta)
            elif kind == "delete":
                item.rows_estimate = max(0, item.rows_estimate - delta)
            # update: row count unchanged, only the touch timestamp.
            item.last_touched_at = _now_iso()
            changed = True
            break

    if not changed:
        return

    try:
        await ctx.cache.set(key, page, ttl_seconds=TABLES_PAGE_CACHE_TTL)
    except Exception as exc:
        log.warning("table.touched cache write raised: %s", exc)
