"""sql-db · Skeleton tools for schema caching."""
from __future__ import annotations

import logging

from app import (
    ext,
    _api_post,
    _user_id,
    resolve_connection,
    build_conn_info,
    DbSchemaSnapshot,
    SCHEMA_CACHE_KEY,
    SCHEMA_CACHE_TTL,
)

log = logging.getLogger("sql-db")


async def _mirror_to_cache(ctx, payload: dict) -> None:
    """Mirror skeleton payload into ctx.cache for @chat.function write-time validation."""
    try:
        snap = DbSchemaSnapshot.model_validate(payload)
        await ctx.cache.set(SCHEMA_CACHE_KEY, snap, ttl_seconds=SCHEMA_CACHE_TTL)
    except Exception as exc:
        log.warning("schema cache mirror failed: %s", exc)


@ext.skeleton(
    "db_schema",
    alert=True,
    # 60s, matching the platform's own mail section, down from 300s. The schema
    # snapshot changes as a RESULT of our own tools (execute_sql running DDL,
    # switching the active connection), and the SDK's SkeletonClient is
    # read-only — no ctx.skeleton.invalidate() exists for a handler to call
    # after a write. The tick is therefore the only thing closing the window,
    # and 300s meant the model could reason from a schema five minutes stale,
    # e.g. still naming a table that was just dropped.
    #
    # Not lower on purpose: the platform derives its tick from the MINIMUM ttl
    # across all of a user's sections (floor 15s), so an aggressive value here
    # speeds up every other extension's refresh too.
    ttl=60,
    description="Active database schema cache — tables, columns, row counts.",
)
async def skeleton_refresh_db_schema(ctx) -> dict:
    """Refresh schema for the user's active connection. Idempotent — safe per tick."""
    try:
        conn, conn_id = await resolve_connection(ctx)
        if not conn:
            payload = {"database": "", "connection": "", "table_count": 0, "tables": [],
                       "note": "No active connection"}
            await _mirror_to_cache(ctx, payload)
            return {"response": payload}

        database = conn.get("database", "")
        if not database:
            payload = {"database": "", "connection": conn.get("name", ""),
                       "table_count": 0, "tables": [], "note": "No database selected"}
            await _mirror_to_cache(ctx, payload)
            return {"response": payload}

        result = await _api_post(ctx, f"/v1/connections/{conn_id}/schema", {
            "user_id":    _user_id(ctx),
            "database":   database,
            "connection": build_conn_info(conn),
        })

        tables = result.get("tables", [])
        compact_tables = []
        for t in tables:
            cols = [
                {"name": c.get("COLUMN_NAME", ""), "type": c.get("COLUMN_TYPE", ""),
                 "key": c.get("COLUMN_KEY", "")}
                for c in t.get("columns", [])
            ]
            compact_tables.append({"name": t["name"], "rows": t.get("rows", 0), "columns": cols})

        # There used to be a separate `table_names` field here, justified by a
        # comment about "better LLM visibility" for a flat string list. Both
        # halves of that were wrong, and the kernel's own renderer says so.
        #
        # _render_skeleton_value expands a list of DICTS into its item fields
        # but collapses a list of SCALARS to a shape hint, so the field rendered
        # as `table_names=list[42]` — the names never reached the model. Joining
        # them into one string does render, but _render_scalar caps a string at
        # 60 chars, which silently truncates the sample AND any "+N more" tail
        # placed after it.
        #
        # It was redundant regardless: `tables` below is a list of dicts with a
        # `name` key, so the kernel already renders real table names inline
        # (`tables=[wp_posts rows=1200; ...]`, first 10 then "(+N more)").
        # `table_count` carries the true total and list_tables() serves the full
        # catalogue. So the field is gone rather than reshaped — fewer bytes on
        # a per-user hot loop, and nothing lost.
        payload = {
            "database":    database,
            "connection":  conn.get("name", ""),
            "table_count": len(compact_tables),
            "tables":      compact_tables,
        }
        await _mirror_to_cache(ctx, payload)
        return {"response": payload}
    except Exception as e:
        log.error("skeleton refresh failed: %s", e)
        return {"response": {
            "database": "", "connection": "", "table_count": 0, "tables": [],
        }}


@ext.tool(
    "skeleton_alert_db_schema",
    description="Alert on database schema changes — tables added or removed.",
)
async def skeleton_alert_db_schema(
    ctx,
    old: dict | None = None,
    new: dict | None = None,
) -> dict:
    """Called by platform when db_schema snapshot changes between ticks."""
    if not old or not new:
        return {"response": ""}

    old_tables = {t["name"] for t in old.get("tables", [])}
    new_tables = {t["name"] for t in new.get("tables", [])}
    added   = new_tables - old_tables
    removed = old_tables - new_tables

    if not added and not removed:
        return {"response": ""}

    parts = []
    if added:
        parts.append(f"New tables: {', '.join(sorted(added))}")
    if removed:
        parts.append(f"Removed tables: {', '.join(sorted(removed))}")

    return {"response": f"Schema changed — {'; '.join(parts)}"}

