"""sql-db · Connection CRUD handlers."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app import (
    chat, ActionResult, _api_post, require_user_id, _tenant_id,
    encrypt_password, build_conn_info,
    CONN_COLLECTION, get_active_connection, get_connection_by_id,
)


# ─── Models ───────────────────────────────────────────────────────────── #

class AddConnectionParams(BaseModel):
    """Add a new database connection."""
    name: str = Field(description="Connection name (e.g. 'production', 'staging')")
    host: str = Field(description="MySQL host")
    port: int = Field(default=3306, description="MySQL port")
    db_user: str = Field(description="MySQL username")
    password: str = Field(description="MySQL password (will be encrypted)")
    database: str = Field(default="", description="Default database name")


class ConnectionIdParams(BaseModel):
    """Target a specific connection."""
    connection_id: str = Field(description="Connection ID")


class UpdateConnectionParams(BaseModel):
    """Update connection details."""
    connection_id: str = Field(description="Connection ID")
    name: str = Field(default="", description="New name")
    host: str = Field(default="", description="New host")
    port: int = Field(default=0, description="New port (0 = no change)")
    db_user: str = Field(default="", description="New MySQL username")
    password: str = Field(default="", description="New password (will be encrypted)")
    database: str = Field(default="", description="New default database")


class SelectConnectionParams(BaseModel):
    """Switch active connection."""
    connection_id: str = Field(description="Connection ID to activate")


class ResolveConnByDbParams(BaseModel):
    """Resolve a connection by database or connection name."""
    database_name: str = Field(
        description="Database name (e.g. 'ijodghbk_test') or connection name"
    )


# ─── Handlers ─────────────────────────────────────────────────────────── #

@chat.function(
    "add_connection", action_type="write", event="connection.added",
    description="Add a new MySQL/MariaDB connection. Tests before saving.",
)
async def fn_add_connection(ctx, params: AddConnectionParams) -> ActionResult:
    """Add a new MySQL/MariaDB connection. Tests before saving."""
    uid = require_user_id(ctx)
    try:
        pwd_enc = encrypt_password(params.password)
        conn_info = {
            "host": params.host,
            "port": params.port,
            "user": params.db_user,
            "password_encrypted": pwd_enc,
            "database": params.database,
        }

        # Test first
        result = await _api_post("/v1/connections/test", {
            "user_id": uid,
            "connection": conn_info,
        })
        if result.get("status") != "ok":
            return ActionResult.error(f"Connection failed: {result.get('error', 'unknown')}")

        # Deactivate other connections
        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=100)
        for doc in page.data:
            if doc.data.get("is_active"):
                await ctx.store.update(CONN_COLLECTION, doc.id, {**doc.data, "is_active": False})

        # Save connection
        doc = await ctx.store.create(CONN_COLLECTION, {
            "user_id": uid,
            "tenant_id": _tenant_id(ctx),
            "name": params.name,
            "host": params.host,
            "port": params.port,
            "db_user": params.db_user,
            "password_encrypted": pwd_enc,
            "database": params.database,
            "server_version": result.get("version", ""),
            "databases": result.get("databases", []),
            "is_active": True,
        })

        return ActionResult.success(
            data={
                "connection_id": doc.id,
                "name": params.name,
                "version": result.get("version", ""),
                "databases": result.get("databases", []),
            },
            summary=f"Connected to {params.host} ({result.get('version', '')})",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "list_connections", action_type="read",
    description="List all saved database connections.",
)
async def fn_list_connections(ctx) -> ActionResult:
    """List all saved database connections."""
    uid = require_user_id(ctx)
    try:
        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=100)
        connections = [{
            "connection_id": doc.id,
            "name": doc.data.get("name", ""),
            "host": doc.data.get("host", ""),
            "database": doc.data.get("database", ""),
            "is_active": doc.data.get("is_active", False),
            "server_version": doc.data.get("server_version", ""),
        } for doc in page.data]

        return ActionResult.success(
            data={"connections": connections, "total": len(connections)},
            summary=f"Found {len(connections)} connection(s)",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "resolve_connection_by_database", action_type="read",
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
    """Return {connection_id} for a saved connection matching database_name or name."""
    uid = require_user_id(ctx)
    target = (params.database_name or "").strip()
    if not target or target in ("database_name", "connection_id"):
        return ActionResult.error(
            "resolve_connection_by_database: database_name is empty or an unresolved placeholder"
        )
    try:
        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=100)
        # Prefer exact database match, then connection name, then case-insensitive.
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
            return ActionResult.error(
                f"No connection found for '{target}'. "
                f"Available: {available or '(none)'}"
            )
        return ActionResult.success(
            data={
                "connection_id": exact.id,
                "database": exact.data.get("database", ""),
                "name": exact.data.get("name", ""),
                "host": exact.data.get("host", ""),
            },
            summary=f"connection_id={exact.id} for {target}",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "test_connection", action_type="read",
    description="Test an existing connection.",
)
async def fn_test_connection(ctx, params: ConnectionIdParams) -> ActionResult:
    """Test an existing connection."""
    try:
        conn = await get_connection_by_id(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("Connection not found")

        result = await _api_post("/v1/connections/test", {
            "user_id": require_user_id(ctx),
            "connection": build_conn_info(conn),
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
    "select_connection", action_type="write", event="connection.selected",
    description="Switch active connection.",
)
async def fn_select_connection(ctx, params: SelectConnectionParams) -> ActionResult:
    """Switch active connection."""
    uid = require_user_id(ctx)
    try:
        target = await get_connection_by_id(ctx, params.connection_id)
        if not target:
            return ActionResult.error("Connection not found")

        # Deactivate all, activate target
        page = await ctx.store.query(CONN_COLLECTION, where={"user_id": uid}, limit=100)
        for doc in page.data:
            is_target = doc.id == params.connection_id
            if doc.data.get("is_active") != is_target:
                await ctx.store.update(CONN_COLLECTION, doc.id, {**doc.data, "is_active": is_target})

        return ActionResult.success(
            data={"connection_id": params.connection_id, "name": target.get("name", "")},
            summary=f"Switched to {target.get('name', params.connection_id)}",
        )
    except Exception as e:
        return ActionResult.error(str(e))


@chat.function(
    "delete_connection", action_type="destructive", event="connection.deleted",
    description="Delete a saved connection.",
)
async def fn_delete_connection(ctx, params: ConnectionIdParams) -> ActionResult:
    """Delete a saved connection."""
    try:
        conn = await get_connection_by_id(ctx, params.connection_id)
        if not conn:
            return ActionResult.error("Connection not found")

        await ctx.store.delete(CONN_COLLECTION, params.connection_id)
        return ActionResult.success(
            data={"connection_id": params.connection_id},
            summary=f"Connection '{conn.get('name', '')}' deleted",
        )
    except Exception as e:
        return ActionResult.error(str(e))
