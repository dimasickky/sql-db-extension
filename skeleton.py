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
    ttl=300,
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
    scopes=["sql-db.read"],
    description="Alert on schema changes (tables added or removed).",
)
async def skeleton_alert_db_schema(
    ctx, old: dict = None, new: dict = None, **kwargs,
) -> dict:
    """Compare old and new schema, alert on table additions/removals."""
    if not old or not new:
        return {"response": ""}

    old_tables = {t["name"] for t in old.get("tables", [])}
    new_tables = {t["name"] for t in new.get("tables", [])}
    added = new_tables - old_tables
    removed = old_tables - new_tables

    if not added and not removed:
        return {"response": ""}

    parts = []
    if added:
        parts.append(f"New tables: {', '.join(sorted(added))}")
    if removed:
        parts.append(f"Removed tables: {', '.join(sorted(removed))}")

    return {"response": f"Schema changed — {'; '.join(parts)}"}
