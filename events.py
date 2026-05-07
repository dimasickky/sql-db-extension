"""sql-db · Sidebar liveness helpers (Phase 2 — sql-db-scale).

NOTE on @ext.on_event vs inline call-site work:

The Imperal kernel's `the platform runtime.services.rule_engine.evaluate_event`
dispatches `@ext.on_event` handlers with literally `await
handler_func(None, event_obj)` — the SDK contract leaks no per-user ctx
into event handlers on the live platform. Any attempt to do `ctx.cache.set`
from inside an `@ext.on_event` handler crashes with `AttributeError:
'NoneType' object has no attribute 'cache'` (kernel swallows + logs but the
side-effect never lands). This is fine for fan-out signal events, broken
for cache mutations.

So sidebar-liveness work that needs the per-user ctx (cache writes for
optimistic UI, cache invalidation on DDL) lives in this module as plain
async helpers and is called inline from `fn_run_editor_sql` (which has
the live ctx). The `ctx.events.emit("...")` calls there continue to fire
— the panel `refresh="on_event:..."` attribute hooks Redis pub/sub
directly via the kernel's panel re-render dispatch, regardless of
whether `@ext.on_event` Python handlers ran.

Once the kernel grows a ctx-aware `on_event` dispatch (`handler_func(ctx,
event_obj)`), these helpers can be moved back behind decorators with no
call-site change.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import (
    TablesPageCache,
    cache_key_catalog,
    cache_key_tables_page,
    cache_key_table_detail,
    SIDEBAR_PAGE_LIMIT,
    TABLES_PAGE_CACHE_TTL,
)

log = logging.getLogger("sql-db.events")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── DML — optimistic local patch ─────────────────────────────────────── #

async def patch_cache_on_dml(
    ctx, *, conn_id: str, database: str, table: str,
    kind: str, row_delta: int,
) -> None:
    """Optimistic local patch on the cached first-page envelope.

    We are the writer — we know the delta. Bump `rows_estimate` by the
    signed delta, set `last_touched_at` so the panel renders the
    "just now" badge. No HTTP fetch.

    Best-effort: any exception is swallowed (logged) so a successful
    SQL execute is never blocked by a cache-write hiccup.
    """
    if not (conn_id and database and table):
        return
    key = cache_key_tables_page(conn_id, database, "", 0, SIDEBAR_PAGE_LIMIT)
    try:
        page = await ctx.cache.get(key, model=TablesPageCache)
    except Exception:
        page = None
    if page is None:
        # Cold cache — nothing to patch. The next render will fetch fresh
        # via _populate_inline (panels.py).
        return

    changed = False
    for item in page.items:
        if item.name == table:
            if kind == "insert":
                item.rows_estimate = max(0, item.rows_estimate + row_delta)
            elif kind == "delete":
                item.rows_estimate = max(0, item.rows_estimate - row_delta)
            # update: row count unchanged, only the touch timestamp.
            item.last_touched_at = _now_iso()
            changed = True
            break

    if not changed:
        return
    try:
        await ctx.cache.set(key, page, ttl_seconds=TABLES_PAGE_CACHE_TTL)
    except Exception as exc:
        log.warning("optimistic patch cache write failed: %s", exc)


# ─── DDL — invalidate caches so next render refetches via T0/T1 ───────── #

async def invalidate_cache_on_ddl(
    ctx, *, conn_id: str, database: str, target_table: str | None,
) -> None:
    """Wipe catalog + first-page cache on a structural change.

    The next sidebar render hits `_populate_inline` (panels.py) and
    refetches via T0 + T1 with the live ctx. Per-table detail cache is
    wiped only for the explicit target_table (best-effort).
    """
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
        if target_table:
            try:
                await ctx.cache.delete(
                    cache_key_table_detail(conn_id, database, target_table),
                )
            except Exception:
                pass
