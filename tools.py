"""sql-db · SqlDbExtension — class-based v2.0 tool surface.

22 `@sdk_ext.tool` methods spanning connections, schema introspection,
read queries, DML/DDL execution, row-level CRUD, NL→SQL, query history,
and saved queries. Thin wrappers over db-service HTTP helpers in ``app``
plus private schema-guard / sql-parser modules for client-side safety
gates — wire contract to db-service (api-server:8099) unchanged.

Key v2 annotations:
- ``nl_to_sql`` is declared ``llm_backed=True`` — it calls ``ctx.ai.complete``
  with ``purpose="execution"`` so federal BYOLLM routing applies.
- Destructive write tools (``execute_sql``, ``delete_row``, ``delete_saved``,
  ``delete_connection``) carry ``cost_credits=1`` — triggers the pre-ACK
  confirmation gate regardless of user default settings.
- ``_pulse_sql_executed`` is preserved as an internal side-channel: panel
  handlers that bypass tools still need a way to emit ``sql.executed`` for
  sidebar schema refresh; kernel-side event wiring runs off the scope
  tag + tool name (``_`` prefix hints LLM not to route to it).
"""
from __future__ import annotations

import json as _json
import logging

from imperal_sdk import Extension, ext as sdk_ext

from schema_guard import list_known_tables, validate_columns, validate_table_exists
from schemas import (
    ColumnInfo,
    ConnectionAdded,
    ConnectionDeleted,
    ConnectionList,
    ConnectionRef,
    ConnectionResolved,
    ConnectionSelected,
    ConnectionTested,
    DryRunResult,
    EditorResult,
    ExecuteResult,
    ExplainResult,
    HistoryEntry,
    HistoryList,
    NlqResult,
    PulseResult,
    QueryResult,
    QuerySaved,
    RowDeleted,
    RowInserted,
    RowUpdated,
    SavedDeleted,
    SavedList,
    SavedQuery,
    SavedRunResult,
    SchemaResult,
    TableInfo,
)
from sql_parser import extract_target_tables

log = logging.getLogger("sql-db.tools")


def _err(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__


_SELECT_VERBS = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}
_WRITE_VERBS_AFFECTING_ROWS = {"INSERT", "UPDATE", "DELETE", "REPLACE"}


# ─── SqlDbExtension ───────────────────────────────────────────────────── #

class SqlDbExtension(Extension):
    """MySQL / MariaDB assistant — connections, schema, queries, DML, NLQ."""

    app_id = "sql-db"

    # ── Helpers ───────────────────────────────────────────────────────── #

    async def _resolve_conn(self, ctx, connection_id: str):
        """Return (conn_data, conn_id) — by explicit id if given, else active."""
        from app import get_connection_by_id, resolve_connection

        connection_id = (connection_id or "").strip()
        if connection_id and connection_id != "connection_id":
            conn = await get_connection_by_id(ctx, connection_id)
            return conn, connection_id
        return await resolve_connection(ctx)

    # ── Connections ───────────────────────────────────────────────────── #

    @sdk_ext.tool(
        description=(
            "Add a new MySQL / MariaDB connection. The connection is tested "
            "before saving — bad credentials or unreachable host fail fast "
            "and the connection is not persisted."
        ),
        output_schema=ConnectionAdded,
        scopes=["sql-db:write"],
    )
    async def add_connection(
        self,
        ctx,
        name: str,
        host: str,
        db_user: str,
        password: str,
        port: int = 3306,
        database: str = "",
    ) -> ConnectionAdded:
        from app import (
            CONN_COLLECTION, _api_post, _tenant_id, encrypt_password,
            require_user_id,
        )

        try:
            uid = require_user_id(ctx)
            pwd_enc = encrypt_password(password)
            conn_info = {
                "host":               host,
                "port":               int(port),
                "user":               db_user,
                "password_encrypted": pwd_enc,
                "database":           database,
            }

            test = await _api_post(
                "/v1/connections/test", {"user_id": uid, "connection": conn_info},
            )
            if test.get("status") != "ok":
                return ConnectionAdded(
                    ok=False, error=f"Connection failed: {test.get('error', 'unknown')}",
                )

            # Single active per user: deactivate all, then save this one active.
            page = await ctx.store.query(
                CONN_COLLECTION, where={"user_id": uid}, limit=100,
            )
            for doc in page.data:
                if doc.data.get("is_active"):
                    await ctx.store.update(
                        CONN_COLLECTION, doc.id,
                        {**doc.data, "is_active": False},
                    )

            doc = await ctx.store.create(CONN_COLLECTION, {
                "user_id":             uid,
                "tenant_id":           _tenant_id(ctx),
                "name":                name,
                "host":                host,
                "port":                int(port),
                "db_user":             db_user,
                "password_encrypted":  pwd_enc,
                "database":            database,
                "server_version":     test.get("version", ""),
                "databases":          test.get("databases", []),
                "is_active":           True,
            })
            return ConnectionAdded(
                connection_id=doc.id,
                name=name,
                version=test.get("version", ""),
                databases=list(test.get("databases", [])),
            )
        except Exception as e:
            return ConnectionAdded(ok=False, error=_err(e))

    @sdk_ext.tool(
        description="List every saved database connection, with active one flagged.",
        output_schema=ConnectionList,
        scopes=["sql-db:read"],
    )
    async def list_connections(self, ctx) -> ConnectionList:
        from app import CONN_COLLECTION, require_user_id

        try:
            uid = require_user_id(ctx)
            page = await ctx.store.query(
                CONN_COLLECTION, where={"user_id": uid}, limit=100,
            )
            conns = [
                ConnectionRef(
                    connection_id=doc.id,
                    name=doc.data.get("name", ""),
                    host=doc.data.get("host", ""),
                    database=doc.data.get("database", ""),
                    is_active=doc.data.get("is_active", False),
                    server_version=doc.data.get("server_version", ""),
                ) for doc in page.data
            ]
            return ConnectionList(connections=conns, total=len(conns))
        except Exception as e:
            return ConnectionList(ok=False, error=_err(e))

    @sdk_ext.tool(
        description=(
            "Resolve connection_id for a database or saved connection name. "
            "Use as the first step in automations before run_query / execute_sql. "
            "Matches exact database, then exact name, then case-insensitive."
        ),
        output_schema=ConnectionResolved,
        scopes=["sql-db:read"],
    )
    async def resolve_connection_by_database(
        self, ctx, database_name: str,
    ) -> ConnectionResolved:
        from app import CONN_COLLECTION, require_user_id

        try:
            uid = require_user_id(ctx)
            target = (database_name or "").strip()
            if not target or target in ("database_name", "connection_id"):
                return ConnectionResolved(
                    ok=False,
                    error="database_name is empty or unresolved placeholder",
                )

            page = await ctx.store.query(
                CONN_COLLECTION, where={"user_id": uid}, limit=100,
            )
            target_lc = target.lower()
            exact = next(
                (d for d in page.data if (d.data.get("database") or "") == target),
                None,
            )
            if not exact:
                exact = next(
                    (d for d in page.data if (d.data.get("name") or "") == target),
                    None,
                )
            if not exact:
                exact = next(
                    (d for d in page.data
                     if (d.data.get("database") or "").lower() == target_lc
                     or (d.data.get("name") or "").lower() == target_lc),
                    None,
                )
            if not exact:
                available = sorted({
                    (d.data.get("database") or d.data.get("name") or "")
                    for d in page.data
                    if d.data.get("database") or d.data.get("name")
                })
                return ConnectionResolved(
                    ok=False,
                    error=f"No connection found for '{target}'",
                    available=list(available),
                )
            return ConnectionResolved(
                connection_id=exact.id,
                database=exact.data.get("database", ""),
                name=exact.data.get("name", ""),
                host=exact.data.get("host", ""),
            )
        except Exception as e:
            return ConnectionResolved(ok=False, error=_err(e))

    @sdk_ext.tool(
        description="Test that an existing connection is reachable and the credentials still work.",
        output_schema=ConnectionTested,
        scopes=["sql-db:read"],
    )
    async def test_connection(self, ctx, connection_id: str) -> ConnectionTested:
        from app import _api_post, build_conn_info, get_connection_by_id, require_user_id

        try:
            conn = await get_connection_by_id(ctx, connection_id)
            if not conn:
                return ConnectionTested(ok=False, error="Connection not found")
            result = await _api_post("/v1/connections/test", {
                "user_id":    require_user_id(ctx),
                "connection": build_conn_info(conn),
            })
            if result.get("status") != "ok":
                return ConnectionTested(
                    ok=False, error=str(result.get("error", "Connection failed")),
                )
            return ConnectionTested(
                version=result.get("version", ""),
                databases=list(result.get("databases", [])),
            )
        except Exception as e:
            return ConnectionTested(ok=False, error=_err(e))

    @sdk_ext.tool(
        description="Switch the user's active database connection to the given connection_id.",
        output_schema=ConnectionSelected,
        scopes=["sql-db:write"],
    )
    async def select_connection(
        self, ctx, connection_id: str,
    ) -> ConnectionSelected:
        from app import CONN_COLLECTION, get_connection_by_id, require_user_id

        try:
            uid = require_user_id(ctx)
            target = await get_connection_by_id(ctx, connection_id)
            if not target:
                return ConnectionSelected(
                    ok=False, error="Connection not found",
                    connection_id=connection_id,
                )
            page = await ctx.store.query(
                CONN_COLLECTION, where={"user_id": uid}, limit=100,
            )
            for doc in page.data:
                is_target = doc.id == connection_id
                if doc.data.get("is_active") != is_target:
                    await ctx.store.update(
                        CONN_COLLECTION, doc.id,
                        {**doc.data, "is_active": is_target},
                    )
            return ConnectionSelected(
                connection_id=connection_id, name=target.get("name", ""),
            )
        except Exception as e:
            return ConnectionSelected(
                ok=False, error=_err(e), connection_id=connection_id,
            )

    @sdk_ext.tool(
        description=(
            "Delete a saved database connection — the encrypted password and "
            "metadata are removed. Any saved queries / history stay in place."
        ),
        output_schema=ConnectionDeleted,
        scopes=["sql-db:write"],
        cost_credits=1,
    )
    async def delete_connection(
        self, ctx, connection_id: str,
    ) -> ConnectionDeleted:
        from app import CONN_COLLECTION, get_connection_by_id

        try:
            conn = await get_connection_by_id(ctx, connection_id)
            if not conn:
                return ConnectionDeleted(
                    ok=False, error="Connection not found",
                    connection_id=connection_id,
                )
            await ctx.store.delete(CONN_COLLECTION, connection_id)
            return ConnectionDeleted(connection_id=connection_id)
        except Exception as e:
            return ConnectionDeleted(
                ok=False, error=_err(e), connection_id=connection_id,
            )

    # ── Query / schema / explain / dry_run ────────────────────────────── #

    @sdk_ext.tool(
        description=(
            "Run a read-only query (SELECT / WITH / SHOW / DESCRIBE / EXPLAIN). "
            "Use execute_sql for INSERT / UPDATE / DELETE / DDL."
        ),
        output_schema=QueryResult,
        scopes=["sql-db:read"],
    )
    async def run_query(
        self, ctx, sql: str, limit: int = 100, connection_id: str = "",
    ) -> QueryResult:
        from app import _api_post, build_conn_info, require_user_id

        try:
            clean = (sql or "").strip().rstrip(";")
            if not clean or clean.lower() == "sql":
                return QueryResult(ok=False, error="sql parameter is empty")
            first = clean.split(None, 1)[0].upper() if clean else ""
            if first not in _SELECT_VERBS:
                return QueryResult(
                    ok=False,
                    error=(
                        f"run_query accepts read-only verbs only (got '{first}'). "
                        f"Use execute_sql for {first}."
                    ),
                )

            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return QueryResult(
                    ok=False, error="No active connection. Use add_connection first.",
                )

            result = await _api_post(f"/v1/connections/{conn_id}/query", {
                "user_id":    require_user_id(ctx),
                "sql":        clean,
                "limit":      max(1, int(limit)),
                "connection": build_conn_info(conn),
            })
            if result.get("status") != "ok":
                return QueryResult(
                    ok=False, error=str(result.get("detail", "Query failed")),
                )
            return QueryResult(
                columns=list(result.get("columns", [])),
                rows=list(result.get("rows", [])),
                total_rows=int(result.get("total_rows", 0)),
                exec_ms=int(result.get("exec_ms", 0)),
            )
        except Exception as e:
            return QueryResult(ok=False, error=_err(e))

    @sdk_ext.tool(
        description=(
            "Get the full schema (tables, columns, indexes, row counts) for "
            "a database. If database is empty the connection's default is used."
        ),
        output_schema=SchemaResult,
        scopes=["sql-db:read"],
    )
    async def get_schema(
        self, ctx, database: str = "", connection_id: str = "",
    ) -> SchemaResult:
        from app import _api_post, build_conn_info, require_user_id

        try:
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return SchemaResult(ok=False, error="No active connection.")
            db = database or conn.get("database", "")
            if not db:
                return SchemaResult(
                    ok=False, error="No database specified on connection or call.",
                )
            result = await _api_post(f"/v1/connections/{conn_id}/schema", {
                "user_id":    require_user_id(ctx),
                "database":   db,
                "connection": build_conn_info(conn),
            })
            tables = [
                TableInfo(
                    name=t["name"],
                    rows=int(t.get("rows", 0) or 0),
                    columns=[
                        ColumnInfo(
                            name=c.get("COLUMN_NAME", c.get("name", "")),
                            type=c.get("COLUMN_TYPE", c.get("type", "")),
                            key=c.get("COLUMN_KEY", c.get("key", "")),
                        ) for c in t.get("columns", [])
                    ],
                ) for t in result.get("tables", [])
            ]
            return SchemaResult(database=db, tables=tables, table_count=len(tables))
        except Exception as e:
            return SchemaResult(ok=False, error=_err(e))

    @sdk_ext.tool(
        description="Run EXPLAIN on a SQL statement to inspect its execution plan.",
        output_schema=ExplainResult,
        scopes=["sql-db:read"],
    )
    async def explain_query(
        self, ctx, sql: str, connection_id: str = "",
    ) -> ExplainResult:
        from app import _api_post, build_conn_info, require_user_id

        try:
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return ExplainResult(ok=False, error="No active connection.", sql=sql)
            result = await _api_post(f"/v1/connections/{conn_id}/explain", {
                "user_id":    require_user_id(ctx),
                "sql":        sql,
                "connection": build_conn_info(conn),
            })
            return ExplainResult(sql=sql, plan=list(result.get("plan", [])))
        except Exception as e:
            return ExplainResult(ok=False, error=_err(e), sql=sql)

    @sdk_ext.tool(
        description=(
            "Dry-run a DML statement inside a transaction: execute it, report "
            "how many rows would be affected, then ROLLBACK. Nothing is persisted."
        ),
        output_schema=DryRunResult,
        scopes=["sql-db:read"],
    )
    async def dry_run(
        self, ctx, sql: str, connection_id: str = "",
    ) -> DryRunResult:
        from app import _api_post, build_conn_info, require_user_id

        try:
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return DryRunResult(ok=False, error="No active connection.")
            result = await _api_post(f"/v1/connections/{conn_id}/dry_run", {
                "user_id":    require_user_id(ctx),
                "sql":        sql,
                "connection": build_conn_info(conn),
            })
            return DryRunResult(
                would_affect=int(result.get("would_affect", 0)),
                query_type=str(result.get("query_type", "")),
                tables=list(result.get("tables", [])),
                exec_ms=int(result.get("exec_ms", 0)),
            )
        except Exception as e:
            return DryRunResult(ok=False, error=_err(e))

    # ── Execute (DML/DDL) ─────────────────────────────────────────────── #

    @sdk_ext.tool(
        description=(
            "Execute a write statement (INSERT, UPDATE, DELETE, REPLACE, "
            "ALTER, CREATE, DROP, TRUNCATE). Rejects the call when all "
            "target tables can be resolved and none are in the active "
            "schema. Always confirm destructive ops with the user."
        ),
        output_schema=ExecuteResult,
        scopes=["sql-db:write"],
        cost_credits=1,
    )
    async def execute_sql(
        self, ctx, sql: str, connection_id: str = "",
    ) -> ExecuteResult:
        from app import _api_post, build_conn_info, require_user_id

        try:
            clean = (sql or "").strip().rstrip(";")
            if not clean:
                return ExecuteResult(ok=False, error="sql parameter is empty")

            # Skeleton-cheap gate: when the schema cache knows a concrete
            # table list and we can extract a single target table that is
            # NOT in the list, fail fast. Ambiguous extraction (CTEs / DDL /
            # subqueries → []) skips the gate to avoid false rejects.
            known = list_known_tables(ctx)
            if known:
                targets = extract_target_tables(clean)
                missing = [t for t in targets if t not in known]
                if missing:
                    return ExecuteResult(
                        ok=False,
                        error=(
                            f"Unknown table(s) referenced: {', '.join(missing)}. "
                            f"Known: {', '.join(known)}."
                        ),
                    )

            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return ExecuteResult(ok=False, error="No connection resolved.")

            result = await _api_post(f"/v1/connections/{conn_id}/execute", {
                "user_id":    require_user_id(ctx),
                "sql":        clean,
                "confirmed":  True,
                "connection": build_conn_info(conn),
            })
            if result.get("status") != "ok":
                return ExecuteResult(
                    ok=False, error=str(result.get("detail", "Execution failed")),
                )

            rows_affected = int(result.get("rows_affected", 0) or 0)
            query_type = (result.get("query_type") or "").upper()

            # Loud-fail for zero-row writes on INSERT/UPDATE/DELETE/REPLACE.
            # The kernel-side automation normaliser would otherwise report
            # phantom success on a bad WHERE / empty VALUES clause.
            if rows_affected == 0 and query_type in _WRITE_VERBS_AFFECTING_ROWS:
                return ExecuteResult(
                    ok=False,
                    error=(
                        f"{query_type} executed but 0 rows affected — "
                        "check VALUES list or WHERE clause."
                    ),
                    query_type=query_type,
                    tables=list(result.get("tables", [])),
                    exec_ms=int(result.get("exec_ms", 0)),
                )
            return ExecuteResult(
                rows_affected=rows_affected,
                query_type=str(result.get("query_type", "")),
                tables=list(result.get("tables", [])),
                exec_ms=int(result.get("exec_ms", 0)),
            )
        except Exception as e:
            return ExecuteResult(ok=False, error=_err(e))

    @sdk_ext.tool(
        description=(
            "Run any SQL from the editor panel. Auto-detects: SELECT and "
            "friends go to the query path; EXPLAIN is unwrapped and explained; "
            "everything else runs as a mutation. Internal panel tool."
        ),
        output_schema=EditorResult,
        scopes=["sql-db:write"],
    )
    async def run_editor_sql(
        self, ctx, sql: str, connection_id: str = "",
    ) -> EditorResult:
        from app import _api_post, build_conn_info, require_user_id

        try:
            clean = (sql or "").strip().rstrip(";")
            if not clean:
                return EditorResult(ok=False, error="Empty SQL")
            first_word = clean.split()[0].upper()
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return EditorResult(ok=False, error="No active connection.", sql=clean)

            uid = require_user_id(ctx)

            if first_word == "EXPLAIN":
                inner = clean[len("EXPLAIN"):].strip()
                if not inner:
                    return EditorResult(
                        ok=False, error="EXPLAIN requires a query after it.",
                        sql=clean,
                    )
                r = await _api_post(f"/v1/connections/{conn_id}/explain", {
                    "user_id": uid, "sql": inner, "connection": build_conn_info(conn),
                })
                if r.get("status") == "error":
                    return EditorResult(
                        ok=False, error=str(r.get("detail", "EXPLAIN failed")),
                        sql=inner, kind="explain",
                    )
                return EditorResult(
                    kind="explain", sql=inner, plan=list(r.get("plan", [])),
                )

            if first_word in _SELECT_VERBS:
                r = await _api_post(f"/v1/connections/{conn_id}/query", {
                    "user_id":    uid,
                    "sql":        clean,
                    "limit":      100,
                    "connection": build_conn_info(conn),
                })
                if r.get("status") == "error":
                    return EditorResult(
                        ok=False, error=str(r.get("detail", "Query failed")),
                        sql=clean, kind="query",
                    )
                return EditorResult(
                    kind="query", sql=clean,
                    columns=list(r.get("columns", [])),
                    rows=list(r.get("rows", [])),
                    total_rows=int(r.get("total_rows", 0)),
                    exec_ms=int(r.get("exec_ms", 0)),
                )

            # Mutation
            r = await _api_post(f"/v1/connections/{conn_id}/execute", {
                "user_id":    uid,
                "sql":        clean,
                "confirmed":  True,
                "connection": build_conn_info(conn),
            })
            if r.get("status") == "error":
                return EditorResult(
                    ok=False, error=str(r.get("detail", "Execution failed")),
                    sql=clean, kind="execute", query_type=first_word,
                )
            return EditorResult(
                kind="execute", sql=clean,
                rows_affected=int(r.get("rows_affected", 0) or 0),
                query_type=str(r.get("query_type", first_word)),
                tables=list(r.get("tables", [])),
                exec_ms=int(r.get("exec_ms", 0)),
            )
        except Exception as e:
            return EditorResult(ok=False, error=_err(e), sql=sql)

    # ── Natural-language SQL (LLM-backed) ─────────────────────────────── #

    @sdk_ext.tool(
        description=(
            "Convert a natural-language question into a SQL SELECT using the "
            "active connection's schema. Returns the SQL only — does not run "
            "it. Pass the result into run_query to execute."
        ),
        output_schema=NlqResult,
        scopes=["sql-db:read"],
        llm_backed=True,
    )
    async def nl_to_sql(
        self, ctx, question: str, connection_id: str = "",
    ) -> NlqResult:
        from app import _api_post, build_conn_info, require_user_id

        try:
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return NlqResult(
                    ok=False, error="No active connection.", question=question,
                )
            database = conn.get("database", "")
            if not database:
                return NlqResult(
                    ok=False, error="No database selected on connection.",
                    question=question,
                )

            # Prefer cached schema from skeleton; refresh if cold.
            schema_data = None
            if ctx.skeleton:
                schema_data = await ctx.skeleton.get("db_schema")

            if not schema_data or not schema_data.get("tables"):
                result = await _api_post(f"/v1/connections/{conn_id}/schema", {
                    "user_id":    require_user_id(ctx),
                    "database":   database,
                    "connection": build_conn_info(conn),
                })
                schema_data = {
                    "database": database, "tables": result.get("tables", []),
                }

            schema_desc = _build_schema_description(schema_data)
            if not schema_desc:
                return NlqResult(
                    ok=False, error="No schema available — run get_schema first.",
                    question=question, database=database,
                )

            prompt = (
                f"Given the following MySQL database schema:\n\n{schema_desc}\n\n"
                f"Write a SQL SELECT query that answers: {question}\n\n"
                f"Return ONLY the SQL query, no explanation."
            )
            try:
                completion = await ctx.ai.complete(prompt, purpose="execution")
            except TypeError:
                # Older ctx.ai may not accept purpose=. Fall back cleanly.
                completion = await ctx.ai.complete(prompt)
            sql = (completion.text or "").strip().strip("`").strip()
            if sql.lower().startswith("sql"):
                sql = sql[3:].strip()

            return NlqResult(sql=sql, question=question, database=database)
        except Exception as e:
            return NlqResult(ok=False, error=_err(e), question=question)

    # ── History / saved ────────────────────────────────────────────────── #

    @sdk_ext.tool(
        description="List the most recent query history entries for the active connection.",
        output_schema=HistoryList,
        scopes=["sql-db:read"],
    )
    async def list_history(
        self, ctx, limit: int = 20, connection_id: str = "",
    ) -> HistoryList:
        from app import _api_get, require_user_id

        try:
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return HistoryList(ok=False, error="No active connection.")
            result = await _api_get(
                f"/v1/connections/{conn_id}/history",
                {"user_id": require_user_id(ctx), "limit": max(1, int(limit))},
            )
            return HistoryList(
                history=[HistoryEntry(**h) for h in result.get("history", [])],
                total=int(result.get("total", 0)),
            )
        except Exception as e:
            return HistoryList(ok=False, error=_err(e))

    @sdk_ext.tool(
        description="Save a SQL query with a name and optional description for later re-use.",
        output_schema=QuerySaved,
        scopes=["sql-db:write"],
    )
    async def save_query(
        self,
        ctx,
        name: str,
        sql_text: str,
        description: str = "",
        connection_id: str = "",
    ) -> QuerySaved:
        from app import _api_post, require_user_id

        try:
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return QuerySaved(ok=False, error="No active connection.")
            result = await _api_post(f"/v1/connections/{conn_id}/saved", {
                "user_id":     require_user_id(ctx),
                "conn_id":     conn_id,
                "name":        name,
                "sql_text":    sql_text,
                "description": description,
            })
            return QuerySaved(
                query_id=str(result.get("id", "")),
                name=name,
            )
        except Exception as e:
            return QuerySaved(ok=False, error=_err(e))

    @sdk_ext.tool(
        description="List every saved SQL query for the active connection.",
        output_schema=SavedList,
        scopes=["sql-db:read"],
    )
    async def list_saved(
        self, ctx, connection_id: str = "",
    ) -> SavedList:
        from app import _api_get, require_user_id

        try:
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return SavedList(ok=False, error="No active connection.")
            result = await _api_get(
                f"/v1/connections/{conn_id}/saved",
                {"user_id": require_user_id(ctx)},
            )
            raw = result.get("saved_queries", []) or []
            return SavedList(
                saved_queries=[SavedQuery(**q) for q in raw],
                total=len(raw),
            )
        except Exception as e:
            return SavedList(ok=False, error=_err(e))

    @sdk_ext.tool(
        description="Execute a previously saved query by its query_id and return rows.",
        output_schema=SavedRunResult,
        scopes=["sql-db:read"],
    )
    async def run_saved(
        self, ctx, query_id: str, connection_id: str = "",
    ) -> SavedRunResult:
        from app import _api_get, _api_post, build_conn_info, require_user_id

        try:
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return SavedRunResult(
                    ok=False, error="No active connection.", query_id=query_id,
                )
            saved_list = await _api_get(
                f"/v1/connections/{conn_id}/saved",
                {"user_id": require_user_id(ctx)},
            )
            target = next(
                (q for q in saved_list.get("saved_queries", []) if q.get("id") == query_id),
                None,
            )
            if not target:
                return SavedRunResult(
                    ok=False, error="Saved query not found", query_id=query_id,
                )
            result = await _api_post(f"/v1/connections/{conn_id}/query", {
                "user_id":    require_user_id(ctx),
                "sql":        target["sql_text"],
                "limit":      100,
                "connection": build_conn_info(conn),
            })
            return SavedRunResult(
                query_id=query_id,
                name=target.get("name", ""),
                sql=target.get("sql_text", ""),
                columns=list(result.get("columns", [])),
                rows=list(result.get("rows", [])),
                total_rows=int(result.get("total_rows", 0)),
                exec_ms=int(result.get("exec_ms", 0)),
            )
        except Exception as e:
            return SavedRunResult(ok=False, error=_err(e), query_id=query_id)

    @sdk_ext.tool(
        description="Permanently delete a saved query by query_id. Cannot be undone.",
        output_schema=SavedDeleted,
        scopes=["sql-db:write"],
        cost_credits=1,
    )
    async def delete_saved(
        self, ctx, query_id: str, connection_id: str = "",
    ) -> SavedDeleted:
        from app import _api_delete, require_user_id

        try:
            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return SavedDeleted(
                    ok=False, error="No active connection.", query_id=query_id,
                )
            await _api_delete(
                f"/v1/connections/{conn_id}/saved/{query_id}",
                {"user_id": require_user_id(ctx)},
            )
            return SavedDeleted(query_id=query_id)
        except Exception as e:
            return SavedDeleted(ok=False, error=_err(e), query_id=query_id)

    # ── Row-level CRUD ────────────────────────────────────────────────── #

    @sdk_ext.tool(
        description=(
            "Insert a new row into a table. values_json is a JSON object "
            "mapping column name to value. Values are parameterised on the "
            "backend — never interpolated into SQL."
        ),
        output_schema=RowInserted,
        scopes=["sql-db:write"],
    )
    async def insert_row(
        self,
        ctx,
        table: str,
        values_json: str,
        connection_id: str = "",
    ) -> RowInserted:
        from app import _api_post, build_conn_info, require_user_id

        try:
            values, err = _parse_values(values_json)
            if err:
                return RowInserted(ok=False, error=err, table=table)
            if not values:
                return RowInserted(ok=False, error="No values to insert", table=table)

            if (t_err := validate_table_exists(ctx, table)):
                return RowInserted(ok=False, error=t_err, table=table)
            if (c_err := validate_columns(ctx, table, list(values.keys()))):
                return RowInserted(ok=False, error=c_err, table=table)

            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return RowInserted(ok=False, error="No active connection", table=table)

            result = await _api_post(f"/v1/connections/{conn_id}/row", {
                "user_id":    require_user_id(ctx),
                "operation":  "insert",
                "table":      table,
                "values":     values,
                "connection": build_conn_info(conn),
            })
            if result.get("status") != "ok":
                return RowInserted(
                    ok=False, error=str(result.get("detail", "Insert failed")),
                    table=table,
                )
            return RowInserted(
                table=table,
                rows_affected=int(result.get("rows_affected", 0)),
                inserted_id=result.get("inserted_id"),
            )
        except Exception as e:
            return RowInserted(ok=False, error=_err(e), table=table)

    @sdk_ext.tool(
        description=(
            "Update a single row identified by its primary key. Changes as "
            "JSON object of column -> new value. Backend is parameterised."
        ),
        output_schema=RowUpdated,
        scopes=["sql-db:write"],
    )
    async def update_row(
        self,
        ctx,
        table: str,
        pk_col: str,
        pk_value: str,
        values_json: str,
        connection_id: str = "",
    ) -> RowUpdated:
        from app import _api_post, build_conn_info, require_user_id

        try:
            values, err = _parse_values(values_json)
            if err:
                return RowUpdated(ok=False, error=err, table=table)
            if not values:
                return RowUpdated(ok=False, error="No changes to apply", table=table)

            if (t_err := validate_table_exists(ctx, table)):
                return RowUpdated(ok=False, error=t_err, table=table)
            referenced = list(values.keys()) + [pk_col]
            if (c_err := validate_columns(ctx, table, referenced)):
                return RowUpdated(ok=False, error=c_err, table=table)

            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return RowUpdated(ok=False, error="No active connection", table=table)

            result = await _api_post(f"/v1/connections/{conn_id}/row", {
                "user_id":    require_user_id(ctx),
                "operation":  "update",
                "table":      table,
                "values":     values,
                "where":      {pk_col: pk_value},
                "connection": build_conn_info(conn),
            })
            if result.get("status") != "ok":
                return RowUpdated(
                    ok=False, error=str(result.get("detail", "Update failed")),
                    table=table,
                )
            return RowUpdated(
                table=table,
                rows_affected=int(result.get("rows_affected", 0)),
                pk={pk_col: pk_value},
            )
        except Exception as e:
            return RowUpdated(ok=False, error=_err(e), table=table)

    @sdk_ext.tool(
        description=(
            "Delete a single row identified by its primary key. Backend is "
            "parameterised. Always confirm destructive ops with the user."
        ),
        output_schema=RowDeleted,
        scopes=["sql-db:write"],
        cost_credits=1,
    )
    async def delete_row(
        self,
        ctx,
        table: str,
        pk_col: str,
        pk_value: str,
        connection_id: str = "",
    ) -> RowDeleted:
        from app import _api_post, build_conn_info, require_user_id

        try:
            if (t_err := validate_table_exists(ctx, table)):
                return RowDeleted(ok=False, error=t_err, table=table)
            if (c_err := validate_columns(ctx, table, [pk_col])):
                return RowDeleted(ok=False, error=c_err, table=table)

            conn, conn_id = await self._resolve_conn(ctx, connection_id)
            if not conn:
                return RowDeleted(ok=False, error="No active connection", table=table)

            result = await _api_post(f"/v1/connections/{conn_id}/row", {
                "user_id":    require_user_id(ctx),
                "operation":  "delete",
                "table":      table,
                "where":      {pk_col: pk_value},
                "connection": build_conn_info(conn),
            })
            if result.get("status") != "ok":
                return RowDeleted(
                    ok=False, error=str(result.get("detail", "Delete failed")),
                    table=table,
                )
            return RowDeleted(
                table=table,
                rows_affected=int(result.get("rows_affected", 0)),
                pk={pk_col: pk_value},
            )
        except Exception as e:
            return RowDeleted(ok=False, error=_err(e), table=table)

    # ── Internal side-channel (panel DML → event pulse) ──────────────── #

    @sdk_ext.tool(
        description=(
            "Internal side-channel tool: emits a sql.executed-equivalent pulse "
            "after a panel-direct DML run, so the sidebar schema cache refreshes. "
            "Do not invoke from chat — extension-internal use only."
        ),
        output_schema=PulseResult,
        scopes=["sql-db:write"],
    )
    async def _pulse_sql_executed(self, ctx, kind: str = "dml") -> PulseResult:
        return PulseResult(kind=kind)


# ─── Private module helpers ──────────────────────────────────────────── #

def _parse_values(values_json: str) -> tuple[dict, str | None]:
    """Parse values_json, return (dict, error_msg or None)."""
    if not values_json or not values_json.strip():
        return {}, "No values provided"
    try:
        data = _json.loads(values_json)
        if not isinstance(data, dict):
            return {}, "values_json must be a JSON object"
        return data, None
    except _json.JSONDecodeError as e:
        return {}, f"Invalid JSON: {e}"


def _build_schema_description(schema_data: dict) -> str:
    """Compact schema text for the nl_to_sql prompt."""
    lines: list[str] = []
    db = schema_data.get("database", "")
    lines.append(f"Database: {db}")
    for table in schema_data.get("tables", [])[:30]:
        cols = []
        for c in table.get("columns", []):
            # SchemaResult leaf type vs raw dict from skeleton — accept both.
            if hasattr(c, "name"):
                name, ctype, key = c.name, c.type, c.key
            else:
                name = c.get("COLUMN_NAME", c.get("name", ""))
                ctype = c.get("COLUMN_TYPE", c.get("type", ""))
                key = c.get("COLUMN_KEY", c.get("key", ""))
            marker = " PK" if key == "PRI" else " FK" if key == "MUL" else ""
            cols.append(f"  {name} {ctype}{marker}")
        tname = table["name"] if isinstance(table, dict) else table.name
        rows = table.get("rows", "?") if isinstance(table, dict) else table.rows
        lines.append(f"\n{tname} ({rows} rows):")
        lines.extend(cols)
    return "\n".join(lines)
