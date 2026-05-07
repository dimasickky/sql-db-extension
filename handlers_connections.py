"""sql-db · Connection CRUD handlers."""
from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app import (
    chat, ActionResult, _api_post, require_user_id, _tenant_id,
    encrypt_password, build_conn_info,
    CONN_COLLECTION, get_active_connection, get_connection_by_id,
)
from schema_guard import invalidate as invalidate_schema_cache


# ─── Models ───────────────────────────────────────────────────────────────── #

class NoParams(BaseModel):
    """Empty params model for @chat.function handlers with no business inputs (V17)."""
    model_config = ConfigDict(populate_by_name=True)


class AddConnectionParams(BaseModel):
    """Add a new database connection."""
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        default="",
        validation_alias=AliasChoices("name", "connection_name", "conn_name", "label"),
        description=(
            "Short connection label (e.g. 'whmcs', 'production'). "
            "Optional — derived from host+database when empty."
        ),
    )
    host: str = Field(
        validation_alias=AliasChoices("host", "server", "hostname", "address"),
        description="MySQL host (e.g. server8.webhostmost.com)",
    )
    port: int = Field(default=3306, description="MySQL port")
    db_user: str = Field(
        validation_alias=AliasChoices("db_user", "user", "username", "login"),
        description="MySQL username",
    )
    password: str = Field(
        validation_alias=AliasChoices("password", "pass", "pwd"),
        description="MySQL password (will be encrypted)",
    )
    database: str = Field(
        default="",
        validation_alias=AliasChoices("database", "db", "db_name", "database_name", "schema"),
        description="Default database name",
    )


class ConnectionIdParams(BaseModel):
    """Target a specific connection."""
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(
        validation_alias=AliasChoices("connection_id", "conn_id", "connection", "id"),
        description="Connection ID",
    )


class SelectConnectionParams(BaseModel):
    """Switch active connection."""
    model_config = ConfigDict(populate_by_name=True)

    connection_id: str = Field(
        validation_alias=AliasChoices("connection_id", "conn_id", "connection", "id"),
        description="Connection ID to activate",
    )


class ResolveConnByDbParams(BaseModel):
    """Resolve a connection by database or connection name."""
    model_config = ConfigDict(populate_by_name=True)

    database_name: str = Field(
        validation_alias=AliasChoices(
            "database_name", "database", "db", "db_name",
            "name", "connection_name", "label",
        ),
        description="Database name (e.g. 'ijodghbk_test') or connection name",
    )


# ─── Handlers ─────────────────────────────────────────────────────────────── #

@chat.function(
    "add_connection",
    action_type="write",
    chain_callable=True,
    effects=["create:connection"],
    event="connection.added",
    description="Add a new MySQL/MariaDB connection. Tests connectivity before saving.",
)
async def fn_add_connection(ctx, params: AddConnectionParams) -> ActionResult:
    uid = require_user_id(ctx)
    try:
        name = params.name.strip()
        if not name:
            host_short = params.host.split(".", 1)[0] if params.host else "conn"
            tail = params.database or params.db_user or ""
            name = f"{host_short}_{tail}".rstrip("_") or host_short

        pwd_enc = encrypt_password(params.password)
        conn_info = {
            "host": params.host, "port": params.port, "user": params.db_user,
            "password_encrypted": pwd_enc, "database": params.database,
        }

        result = await _api_post(ctx, "/v1/connections/test", {
            "user_id": uid, "connection": conn_info,
        })
        if result.get("status") != "ok":
            return ActionResult.error(f"Connection failed: {result.get('error', 'unknown')}")

        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=100)
        for doc in page.data:
            if doc.data.get("is_active"):
                await ctx.store.update(CONN_COLLECTION, doc.id, {**doc.data, "is_active": False})

        doc = await ctx.store.create(CONN_COLLECTION, {
            "user_id": uid, "tenant_id": _tenant_id(ctx), "name": name,
            "host": params.host, "port": params.port, "db_user": params.db_user,
            "password_encrypted": pwd_enc, "database": params.database,
            "server_version": result.get("version", ""),
            "databases": result.get("databases", []),
            "is_active": True,
        })

        return ActionResult.success(
            data={"connection_id": doc.id, "name": name,
                  "version": result.get("version", ""),
                  "databases": result.get("databases", [])},
            summary=f"Connected to {params.host} ({result.get('version', '')})",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "list_connections",
    action_type="read",
    description="List all saved database connections with their status.",
)
async def fn_list_connections(ctx, params: NoParams) -> ActionResult:
    uid = require_user_id(ctx)
    try:
        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=100)
        connections = [{
            "connection_id":  doc.id,
            "name":           doc.data.get("name", ""),
            "host":           doc.data.get("host", ""),
            "database":       doc.data.get("database", ""),
            "is_active":      doc.data.get("is_active", False),
            "server_version": doc.data.get("server_version", ""),
        } for doc in page.data]
        return ActionResult.success(
            data={"connections": connections, "total": len(connections)},
            summary=f"Found {len(connections)} connection(s)",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "resolve_connection_by_database",
    action_type="read",
    description=(
        "Resolve connection_id for a database or connection name. "
        "Use as the first step in automations before run_query/execute_sql, "
        "e.g. resolve_connection_by_database(database_name='ijodghbk_test') "
        "-> pass the returned connection_id into the next step."
    ),
)
async def fn_resolve_connection_by_database(
    ctx, params: ResolveConnByDbParams,
) -> ActionResult:
    uid = require_user_id(ctx)
    target = (params.database_name or "").strip()
    if not target or target in ("database_name", "connection_id"):
        return ActionResult.error(
            "resolve_connection_by_database: database_name is empty or an unresolved placeholder"
        )
    try:
        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=100)
        target_lc = target.lower()
        exact = next((d for d in page.data if (d.data.get("database") or "") == target), None)
        if not exact:
            exact = next((d for d in page.data if (d.data.get("name") or "") == target), None)
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
            return ActionResult.error(
                f"No connection found for '{target}'. Available: {available or '(none)'}"
            )
        return ActionResult.success(
            data={"connection_id": exact.id, "database": exact.data.get("database", ""),
                  "name": exact.data.get("name", ""), "host": exact.data.get("host", "")},
            summary=f"connection_id={exact.id} for {target}",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "test_connection",
    action_type="read",
    description="Test connectivity for an existing saved connection.",
)
async def fn_test_connection(ctx, params: ConnectionIdParams) -> ActionResult:
    try:
        conn = await get_connection_by_id(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("Connection not found")
        result = await _api_post(ctx, "/v1/connections/test", {
            "user_id": require_user_id(ctx), "connection": build_conn_info(conn),
        })
        if result.get("status") == "ok":
            return ActionResult.success(
                data={"version": result.get("version"), "databases": result.get("databases", [])},
                summary=f"Connection OK — {result.get('version', '')}",
            )
        return ActionResult.error(result.get("error", "Connection failed"))
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "select_connection",
    action_type="write",
    chain_callable=True,
    effects=["update:connection"],
    event="connection.selected",
    description="Switch the active database connection.",
)
async def fn_select_connection(ctx, params: SelectConnectionParams) -> ActionResult:
    uid = require_user_id(ctx)
    try:
        target = await get_connection_by_id(ctx, params.connection_id)
        if not target:
            return ActionResult.error("Connection not found")
        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=100)
        for doc in page.data:
            is_target = doc.id == params.connection_id
            if doc.data.get("is_active") != is_target:
                await ctx.store.update(CONN_COLLECTION, doc.id, {**doc.data, "is_active": is_target})
        # Invalidate schema cache so the next write validates against the new connection.
        await invalidate_schema_cache(ctx)
        return ActionResult.success(
            data={"connection_id": params.connection_id, "name": target.get("name", "")},
            summary=f"Switched to {target.get('name', params.connection_id)}",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "delete_connection",
    action_type="destructive",
    chain_callable=True,
    effects=["delete:connection"],
    event="connection.deleted",
    description="Delete a saved database connection permanently.",
)
async def fn_delete_connection(ctx, params: ConnectionIdParams) -> ActionResult:
    uid = require_user_id(ctx)
    try:
        conn = await get_connection_by_id(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("Connection not found")
        if conn.get("user_id", "") != uid:
            return ActionResult.error("Connection not found")
        await ctx.store.delete(CONN_COLLECTION, params.connection_id)
        return ActionResult.success(
            data={"connection_id": params.connection_id},
            summary=f"Connection '{conn.get('name', '')}' deleted",
        )
    except Exception as e:
        return ActionResult.error(str(e))
