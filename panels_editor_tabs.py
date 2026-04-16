"""sql-db · Editor panel — History + Saved queries tabs."""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import _api_get

log = logging.getLogger("sql-db")


async def append_history(children: list, uid: str, conn_id: str) -> None:
    """History tab — recent queries table."""
    try:
        result = await _api_get(
            f"/v1/connections/{conn_id}/history",
            {"user_id": uid, "limit": 30},
        )
        history = result.get("history", [])
    except Exception:
        history = []

    if not history:
        children.append(ui.Empty(message="No query history yet", icon="Clock"))
        return

    columns = [
        ui.DataColumn(key="sql_text", label="SQL", width="50%"),
        ui.DataColumn(key="query_type", label="Type", width="10%"),
        ui.DataColumn(key="rows_affected", label="Rows", width="10%"),
        ui.DataColumn(key="exec_ms", label="ms", width="10%"),
        ui.DataColumn(key="created_at", label="When", width="20%"),
    ]

    rows = []
    for h in history:
        sql_text = h.get("sql_text", "")
        rows.append({
            "id": str(h.get("id", "")),
            "sql_text": sql_text[:80] + ("..." if len(sql_text) > 80 else ""),
            "query_type": h.get("query_type", ""),
            "rows_affected": str(h.get("rows_affected", 0)),
            "exec_ms": str(h.get("exec_ms", 0)),
            "created_at": h.get("created_at", "")[:16],
        })

    children.append(ui.DataTable(columns=columns, rows=rows))


async def append_saved(children: list, uid: str, conn_id: str) -> None:
    """Saved queries tab — click an item to load+run in editor."""
    try:
        result = await _api_get(
            f"/v1/connections/{conn_id}/saved",
            {"user_id": uid},
        )
        queries = result.get("saved_queries", [])
    except Exception:
        queries = []

    if not queries:
        children.append(ui.Empty(message="No saved queries", icon="Bookmark"))
        return

    items = []
    for q in queries:
        items.append(ui.ListItem(
            id=q["id"],
            title=q.get("name", "Untitled"),
            subtitle=q.get("sql_text", "")[:60],
            meta=q.get("updated_at", "")[:16],
            on_click=ui.Call("__panel__editor",
                            note_id=conn_id, tab="results", action="run",
                            sql=q.get("sql_text", "")),
        ))

    children.append(ui.List(items=items))
