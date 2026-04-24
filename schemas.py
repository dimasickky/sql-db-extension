"""sql-db · Pydantic output schemas.

Every `@sdk_ext.tool` declares an `output_schema`. Error flow: `ok=False,
error=<msg>` inside the same shape — the Narrator reads both branches and
composes one response. Leaf field names mirror the db-service wire
(api-server:8099, contract frozen 2026-04-24).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class _ToolResult(BaseModel):
    ok: bool = True
    error: str | None = None


# ─── Leaf types ──────────────────────────────────────────────────────── #

class ConnectionRef(BaseModel):
    connection_id: str
    name: str = ""
    host: str = ""
    database: str = ""
    is_active: bool = False
    server_version: str = ""


class ColumnInfo(BaseModel):
    name: str
    type: str = ""
    key: str = ""  # PRI | MUL | UNI | ''


class TableInfo(BaseModel):
    name: str
    rows: int = 0
    columns: list[ColumnInfo] = Field(default_factory=list)


# ─── Connection tool outputs ─────────────────────────────────────────── #

class ConnectionAdded(_ToolResult):
    connection_id: str = ""
    name: str = ""
    version: str = ""
    databases: list[str] = Field(default_factory=list)


class ConnectionList(_ToolResult):
    connections: list[ConnectionRef] = Field(default_factory=list)
    total: int = 0


class ConnectionResolved(_ToolResult):
    connection_id: str = ""
    database: str = ""
    name: str = ""
    host: str = ""
    available: list[str] = Field(default_factory=list)


class ConnectionTested(_ToolResult):
    version: str = ""
    databases: list[str] = Field(default_factory=list)


class ConnectionSelected(_ToolResult):
    connection_id: str = ""
    name: str = ""


class ConnectionDeleted(_ToolResult):
    connection_id: str = ""


# ─── Query / schema / explain / dry_run ──────────────────────────────── #

class QueryResult(_ToolResult):
    columns: list[str] = Field(default_factory=list)
    # Rows as list[dict] (column -> value). Values may be any JSON scalar.
    rows: list[dict[str, Any]] = Field(default_factory=list)
    total_rows: int = 0
    exec_ms: int = 0


class SchemaResult(_ToolResult):
    database: str = ""
    tables: list[TableInfo] = Field(default_factory=list)
    table_count: int = 0


class ExplainResult(_ToolResult):
    sql: str = ""
    plan: list[dict[str, Any]] = Field(default_factory=list)


class DryRunResult(_ToolResult):
    would_affect: int = 0
    query_type: str = ""
    tables: list[str] = Field(default_factory=list)
    exec_ms: int = 0


# ─── Execute / editor ────────────────────────────────────────────────── #

class ExecuteResult(_ToolResult):
    rows_affected: int = 0
    query_type: str = ""
    tables: list[str] = Field(default_factory=list)
    exec_ms: int = 0


class EditorResult(_ToolResult):
    """Heterogeneous envelope for run_editor_sql.

    `kind` discriminates between read ("query"), explain ("explain"), and
    mutation ("execute"). Irrelevant fields stay at their defaults so the
    Narrator has a stable shape regardless of branch.
    """
    kind: str = ""  # query | explain | execute
    sql: str = ""
    # query branch
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    total_rows: int = 0
    # explain branch
    plan: list[dict[str, Any]] = Field(default_factory=list)
    # execute branch
    rows_affected: int = 0
    query_type: str = ""
    tables: list[str] = Field(default_factory=list)
    # shared
    exec_ms: int = 0


# ─── Natural-language SQL ────────────────────────────────────────────── #

class NlqResult(_ToolResult):
    sql: str = ""
    question: str = ""
    database: str = ""


# ─── History / saved ─────────────────────────────────────────────────── #

class HistoryEntry(BaseModel):
    sql_text: str = ""
    executed_at: str = ""
    rows_affected: int | None = None
    total_rows: int | None = None
    exec_ms: int = 0
    query_type: str = ""
    error: str | None = None


class HistoryList(_ToolResult):
    history: list[HistoryEntry] = Field(default_factory=list)
    total: int = 0


class QuerySaved(_ToolResult):
    query_id: str = ""
    name: str = ""


class SavedQuery(BaseModel):
    id: str
    name: str = ""
    sql_text: str = ""
    description: str = ""
    created_at: str | None = None


class SavedList(_ToolResult):
    saved_queries: list[SavedQuery] = Field(default_factory=list)
    total: int = 0


class SavedRunResult(_ToolResult):
    query_id: str = ""
    name: str = ""
    sql: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    total_rows: int = 0
    exec_ms: int = 0


class SavedDeleted(_ToolResult):
    query_id: str = ""


# ─── Row CRUD ────────────────────────────────────────────────────────── #

class RowInserted(_ToolResult):
    table: str = ""
    rows_affected: int = 0
    inserted_id: Any = None


class RowUpdated(_ToolResult):
    table: str = ""
    rows_affected: int = 0
    pk: dict[str, Any] = Field(default_factory=dict)


class RowDeleted(_ToolResult):
    table: str = ""
    rows_affected: int = 0
    pk: dict[str, Any] = Field(default_factory=dict)


class PulseResult(_ToolResult):
    kind: str = "dml"
