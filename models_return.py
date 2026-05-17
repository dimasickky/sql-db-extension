"""sql-db · Typed return models for @chat.function data_model= contracts (SDK 5.0.1)."""

from typing import Any

from pydantic import BaseModel


# ─── handlers_connections ─────────────────────────────────────────────────── #

class AddConnectionResult(BaseModel):
    connection_id: str
    name: str
    version: str
    databases: list[str]


class ConnectionItem(BaseModel):
    connection_id: str
    name: str
    host: str
    database: str
    is_active: bool
    server_version: str


class ListConnectionsResult(BaseModel):
    connections: list[ConnectionItem]
    total: int


class ResolveConnectionResult(BaseModel):
    connection_id: str
    database: str
    name: str
    host: str


class TestConnectionResult(BaseModel):
    version: str | None
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
    # DML/DDL branch
    rows_affected: int | None = None
    query_type: str | None = None
    tables: list[Any] | None = None
    exec_ms: int | None = None
    # EXPLAIN branch
    plan: list[Any] | None = None
    sql: str | None = None
    # SELECT branch
    columns: list[str] | None = None
    rows: list[Any] | None = None
    total_rows: int | None = None


# ─── handlers_history ─────────────────────────────────────────────────────── #

class ListHistoryResult(BaseModel):
    history: list[Any]
    total: int


class SaveQueryResult(BaseModel):
    query_id: str | None
    name: str


class ListSavedResult(BaseModel):
    saved_queries: list[Any]
    total: int


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
