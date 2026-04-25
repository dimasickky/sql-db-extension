"""sql-db · Schema guard (SDK 1.6.x ctx.cache contract).

Validates that table / column names referenced by writes (`insert_row`,
`update_row`, `delete_row`, `execute_sql`) exist in the user's active
schema BEFORE the round-trip to the backend. The LLM gets a
recovery-friendly error string instead of a raw MariaDB 1054.

### v1.6.x migration note

In SDK 1.6.0+ `ctx.skeleton_data` is removed and `ctx.skeleton.get(...)`
raises `SkeletonAccessForbidden` from `@chat.function` handlers. The
schema snapshot now flows through `ctx.cache` (Pydantic-typed,
per-extension namespace, TTL ≤300s), populated by the
`@ext.skeleton('db_schema')` refresher in `skeleton.py`.

Public surface:

    section = await load_schema_section(ctx)   # dict, {} on cold cache
    list_known_tables(section) -> list[str]
    find_table(section, table) -> dict | None
    validate_table_exists(section, table) -> str | None
    validate_columns(section, table, referenced) -> str | None
    invalidate(ctx) -> None                    # used after DDL

Validators return `None` either when (a) the cache is cold (skeleton
hasn't refreshed yet) or (b) validation passes. Callers MUST treat
`None` as "skip, defer to backend" rather than as a positive signal.
"""
from __future__ import annotations

import logging
from typing import Any

from app import DbSchemaSnapshot, SCHEMA_CACHE_KEY

log = logging.getLogger("sql-db")


# ─── Cache loader ─────────────────────────────────────────────────────── #

async def load_schema_section(ctx) -> dict:
    """Read the active schema snapshot from ctx.cache.

    Returns the same dict shape that skeleton.py's payload produces:

        {
            "database": "...",
            "connection": "...",
            "table_count": N,
            "tables": [{"name": ..., "rows": ..., "columns": [...]}],
        }

    Returns `{}` on any failure (cold cache, model mismatch, transport error).
    Never raises — this is a best-effort read.
    """
    cache = getattr(ctx, "cache", None)
    if cache is None:
        return {}
    try:
        snap = await cache.get(SCHEMA_CACHE_KEY, model=DbSchemaSnapshot)
    except Exception as exc:  # transport / serialisation / model-mismatch
        log.debug("schema cache read failed: %s", exc)
        return {}
    if snap is None:
        return {}
    return snap.model_dump()


async def invalidate(ctx) -> None:
    """Drop the cached schema snapshot. Call after successful DDL so the next
    write-time validation either sees the fresh skeleton refresh or skips
    validation (cold-cache fallback) rather than rejecting on stale shape.
    """
    cache = getattr(ctx, "cache", None)
    if cache is None:
        return
    try:
        await cache.delete(SCHEMA_CACHE_KEY)
    except Exception as exc:
        log.debug("schema cache invalidate failed: %s", exc)


# ─── Pure validators (operate on already-loaded section) ──────────────── #

def list_known_tables(section: dict) -> list[str]:
    """Return the list of table names from a loaded section, or []."""
    if not isinstance(section, dict):
        return []
    tables = section.get("tables") or []
    return [t.get("name", "") for t in tables if isinstance(t, dict) and t.get("name")]


def find_table(section: dict, table: str) -> dict | None:
    """Return the table dict from a loaded section, or None if not cached."""
    if not isinstance(section, dict):
        return None
    for t in section.get("tables") or []:
        if isinstance(t, dict) and t.get("name") == table:
            return t
    return None


def known_columns(table_dict: dict) -> list[str]:
    """Extract column names from a single table dict."""
    if not isinstance(table_dict, dict):
        return []
    cols = table_dict.get("columns") or []
    return [c.get("name", "") for c in cols if isinstance(c, dict) and c.get("name")]


def validate_table_exists(section: dict, table: str) -> str | None:
    """Return an error message if `table` is not known.

    Returns `None` when:
      - the section is empty (cold cache → defer to backend),
      - the table is known.
    """
    tables = list_known_tables(section)
    if not tables:
        return None  # cold cache — skip validation
    if table in tables:
        return None
    tables_str = ", ".join(tables)
    return (
        f"Unknown table '{table}'. "
        f"Known tables: {tables_str}."
    )


def validate_columns(
    section: dict, table: str, referenced: list[str],
) -> str | None:
    """Return an error message if any referenced column is unknown.

    Returns `None` when:
      - the table isn't cached (defer to backend),
      - the table has no column metadata (defer),
      - all referenced columns exist.
    """
    t = find_table(section, table)
    if not t:
        return None
    valid = known_columns(t)
    if not valid:
        return None
    unknown = [c for c in referenced if c and c not in valid]
    if not unknown:
        return None
    unknown_str = ", ".join(sorted(set(unknown)))
    valid_str = ", ".join(valid)
    return (
        f"Unknown column(s) for table '{table}': {unknown_str}. "
        f"Valid columns: {valid_str}."
    )
