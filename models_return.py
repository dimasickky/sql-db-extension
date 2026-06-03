"""sql-db · Typed return models for @chat.function data_model= contracts (SDK 5.2.0 SDL)."""

from typing import Any, Optional

from pydantic import BaseModel

from imperal_sdk import sdl


# ─── Shared primitives ────────────────────────────────────────────────────── #

class TableColumnDetail(BaseModel):
    name: str
    type: str
    nullable: str
    key: str
    default: Optional[str] = None
    extra: str = ""


class TableIndexDetail(BaseModel):
    name: str
    unique: bool
    columns: list[str]


# ─── SDL Entity types (SDK 5.2.0) ─────────────────────────────────────────── #

class ConnectionEntity(sdl.Entity):
    """SDL DB connection. id=connection_id (UUID), title=name, kind="connection"."""
    host: str = ""
    database: str = ""
    is_active: bool = False
    server_version: str = ""
    databases: list[str] = []


class TableEntity(sdl.Entity):
    """SDL DB table. id=table_name, title=table_name, kind="table"."""
    database: str = ""
    exists: bool = True
    type: str = "BASE TABLE"
    engine: str = ""
    rows_estimate: int = 0
    columns: list[TableColumnDetail] = []
    indexes: list[TableIndexDetail] = []


class TableListEntity(sdl.Entity):
    """Slim SDL table entity for list_tables results. id=title=table name, kind="table"."""
    type: str = "BASE TABLE"
    rows_estimate: int = 0
    size_bytes: int = 0


class SavedQueryEntity(sdl.Entity):
    """SDL saved query. id=query_id, title=name, kind="saved_query"."""
    sql_text: str = ""


class HistoryEntity(sdl.Entity):
    """SDL query-history entry. id=history id, title=SQL snippet, kind="query_history"."""
    sql_text: str = ""
    query_type: str = ""
    rows_affected: int = 0
    exec_ms: int = 0
    created_at: str = ""


# ─── handlers_connections ─────────────────────────────────────────────────── #

class ListConnectionsResult(sdl.EntityList[ConnectionEntity]):
    """list_connections — a REAL sdl.EntityList[ConnectionEntity] (items=[...], total=N,
    x-sdl='entity-list')."""
    pass


class TestConnectionResult(BaseModel):
    version: Optional[str]
    databases: list[str]


class SelectConnectionResult(BaseModel):
    connection_id: str
    name: str


class DeleteConnectionResult(BaseModel):
    connection_id: str


# ─── handlers_execute ─────────────────────────────────────────────────────── #

class SqlExecuteResult(BaseModel):
    rows_affected: int
    query_type: str
    tables: list[Any]
    exec_ms: int


class RunEditorSqlResult(BaseModel):
    rows_affected: Optional[int] = None
    query_type: Optional[str] = None
    tables: Optional[list[Any]] = None
    exec_ms: Optional[int] = None
    plan: Optional[list[Any]] = None
    sql: Optional[str] = None
    columns: Optional[list[str]] = None
    rows: Optional[list[Any]] = None
    total_rows: Optional[int] = None


class BatchStatementResult(BaseModel):
    query_type: str
    is_destructive: bool = False
    tables: list[Any] = []


class BatchExecuteResult(BaseModel):
    """execute_batch — multiple statements run sequentially in ONE transaction."""
    statements_executed: int
    rows_affected: int = 0
    exec_ms: int = 0
    statements: list[BatchStatementResult] = []


# ─── handlers_history ─────────────────────────────────────────────────────── #

class ListHistoryResult(sdl.EntityList[HistoryEntity]):
    """list_history — a REAL sdl.EntityList[HistoryEntity] (items=[...], total=N)."""
    pass


class SaveQueryResult(BaseModel):
    query_id: Optional[str]
    name: str


class ListSavedResult(sdl.EntityList[SavedQueryEntity]):
    """list_saved — a REAL sdl.EntityList[SavedQueryEntity] (items=[...], total=N)."""
    pass


class RunSavedResult(BaseModel):
    name: str
    sql: str
    columns: list[str]
    rows: list[Any]
    total_rows: int
    exec_ms: int


class DeleteSavedResult(BaseModel):
    query_id: str


# ─── handlers_nlq ─────────────────────────────────────────────────────────── #

class NlToSqlResult(BaseModel):
    sql: str
    question: str
    database: str


# ─── handlers_query ───────────────────────────────────────────────────────── #

class QueryResult(BaseModel):
    columns: list[str]
    rows: list[Any]
    total_rows: int
    exec_ms: int


class GetSchemaResult(BaseModel):
    database: str
    tables: list[Any]
    table_count: int


class ExplainResult(BaseModel):
    plan: list[Any]
    sql: str


class DryRunResult(BaseModel):
    would_affect: int
    query_type: str
    tables: list[Any]
    exec_ms: int


# ─── handlers_rows ────────────────────────────────────────────────────────── #

class PulseSqlResult(BaseModel):
    kind: str


class InsertRowResult(BaseModel):
    rows_affected: int
    inserted_id: Any
    table: str


class RowMutateResult(BaseModel):
    rows_affected: int
    table: str
    pk: dict[str, Any]


class CountTableResult(BaseModel):
    database: str
    table: str
    count: int
    exec_ms: int


class ListTablesResult(sdl.EntityList[TableListEntity]):
    """list_tables — a REAL sdl.EntityList[TableListEntity] (items=[...], x-sdl='entity-list');
    database / total_matching / search carried as additive typed fields."""
    database: str = ""
    total_matching: int = 0
    search: str = ""
