"""sql-db · Schema guard.

Pulls known table schemas from `ctx.skeleton_data["db_schema"]` and validates
that column names referenced by writes (`insert_row` / `update_row` /
`delete_row`) actually exist in the target table. Returns a human-readable
error message listing unknown + valid columns so the LLM can self-correct
in a single retry instead of hammering the backend.

Skeleton shape (see `skeleton.py::skeleton_refresh_db_schema`):

    {
        "database": "my_db",
        "connection": "...",
        "table_count": 12,
        "tables": [
            {"name": "users", "rows": 42, "columns": [
                {"name": "id", "type": "int(11)", "key": "PRI"},
                {"name": "email", "type": "varchar(255)", "key": ""},
            ]},
            ...
        ],
    }

Note: skeleton stores compact column dicts with lowercase keys
(`name`, `type`, `key`). Do NOT confuse with backend `/schema` response
which uses INFORMATION_SCHEMA uppercase (`COLUMN_NAME`, `COLUMN_KEY`).
"""
from __future__ import annotations

from typing import Any


def _skeleton_section(ctx) -> dict:
    """Return the db_schema section dict, or {} if missing.

    Kernel exposes skeleton data via `ctx.skeleton_data`, a mapping of
    section name → latest response dict.
    """
    data: Any = getattr(ctx, "skeleton_data", None) or {}
    if not isinstance(data, dict):
        return {}
    section = data.get("db_schema") or {}
    if not isinstance(section, dict):
        return {}
    return section


def list_known_tables(ctx) -> list[str]:
    """Return the list of table names known from the skeleton, or []."""
    section = _skeleton_section(ctx)
    tables = section.get("tables") or []
    return [t.get("name", "") for t in tables if isinstance(t, dict) and t.get("name")]


def find_table(ctx, table: str) -> dict | None:
    """Return the table dict from the skeleton, or None if not cached."""
    section = _skeleton_section(ctx)
    for t in section.get("tables") or []:
        if isinstance(t, dict) and t.get("name") == table:
            return t
    return None


def known_columns(table_dict: dict) -> list[str]:
    """Extract column names from a skeleton table dict."""
    cols = table_dict.get("columns") or []
    return [c.get("name", "") for c in cols if isinstance(c, dict) and c.get("name")]


def validate_columns(
    ctx, table: str, referenced: list[str],
) -> str | None:
    """Return an error message if any referenced column is unknown.

    Returns None if:
      - skeleton has no cache for this table (can't validate → let backend handle)
      - all referenced columns are known

    Returns a user-friendly string listing unknown + valid columns otherwise.
    """
    t = find_table(ctx, table)
    if not t:
        # Skeleton doesn't know this table — either the skeleton hasn't
        # refreshed yet or the table really doesn't exist. Defer to backend.
        return None
    valid = known_columns(t)
    if not valid:
        return None  # nothing to validate against
    unknown = [c for c in referenced if c and c not in valid]
    if not unknown:
        return None
    unknown_str = ", ".join(sorted(set(unknown)))
    valid_str = ", ".join(valid)
    return (
        f"Unknown column(s) for table '{table}': {unknown_str}. "
        f"Valid columns: {valid_str}."
    )


def validate_table_exists(ctx, table: str) -> str | None:
    """Return an error message if the table is unknown in the skeleton.

    Returns None if either the table is known OR the skeleton is empty
    (can't validate → defer to backend).
    """
    tables = list_known_tables(ctx)
    if not tables:
        return None  # skeleton cold — skip
    if table in tables:
        return None
    tables_str = ", ".join(tables) if tables else "(none cached)"
    return (
        f"Unknown table '{table}'. "
        f"Known tables: {tables_str}."
    )
