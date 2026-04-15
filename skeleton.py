"""sql-db · Skeleton tools for schema caching."""
from __future__ import annotations

import logging

from app import ext, _api_post, _user_id, resolve_connection, build_conn_info

log = logging.getLogger("sql-db")


# ─── Skeleton ─────────────────────────────────────────────────────────── #

@ext.tool(
    "skeleton_refresh_db_schema",
    scopes=["sql-db.read"],
    description="Background refresh: database schema cache for active connection.",
)
async def skeleton_refresh_db_schema(ctx, **kwargs) -> dict:
    """Refresh schema for the user's active connection. Stored in skeleton."""
    try:
        conn, conn_id = await resolve_connection(ctx)
        if not conn:
            return {"response": {"tables": [], "note": "No active connection"}}

        database = conn.get("database", "")
        if not database:
            return {"response": {"tables": [], "note": "No database selected"}}

        result = await _api_post(f"/v1/connections/{conn_id}/schema", {
            "user_id": _user_id(ctx),
            "database": database,
            "connection": build_conn_info(conn),
        })

        tables = result.get("tables", [])
        # Compact format for skeleton (save tokens)
        compact_tables = []
        for t in tables[:50]:
            cols = [
                {"name": c.get("COLUMN_NAME", ""), "type": c.get("COLUMN_TYPE", ""),
                 "key": c.get("COLUMN_KEY", "")}
                for c in t.get("columns", [])
            ]
            compact_tables.append({
                "name": t["name"],
                "rows": t.get("rows", 0),
                "columns": cols,
            })

        return {"response": {
            "database": database,
            "connection": conn.get("name", ""),
            "tables": compact_tables,
            "table_count": len(compact_tables),
        }}
    except Exception as e:
        log.error("Skeleton refresh failed: %s", e)
        return {"response": {"tables": [], "error": str(e)}}


@ext.tool(
    "skeleton_alert_db",
    scopes=["sql-db.read"],
    description="Alert on schema changes.",
)
async def skeleton_alert_db(ctx, old: dict = None, new: dict = None, **kwargs) -> dict:
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
