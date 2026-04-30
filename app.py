"""sql-db · Shared state & extension setup."""
from __future__ import annotations

import logging
import os

from imperal_sdk import Extension
from imperal_sdk.chat import ChatExtension, ActionResult

log = logging.getLogger("sql-db")


# ─── Config ───────────────────────────────────────────────────────────── #

DB_SERVICE_URL = os.environ["DB_SERVICE_URL"]
DB_SERVICE_KEY = os.getenv("DB_SERVICE_KEY", "")
SQL_DB_ENCRYPTION_KEY = os.getenv("SQL_DB_ENCRYPTION_KEY", "")

CONN_COLLECTION = "db_connections"


# ─── Fernet (encrypt passwords before sending to backend) ─────────────── #

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet
        if not SQL_DB_ENCRYPTION_KEY:
            raise RuntimeError("SQL_DB_ENCRYPTION_KEY not set")
        _fernet = Fernet(SQL_DB_ENCRYPTION_KEY.encode())
    return _fernet


def encrypt_password(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


# ─── HTTP via SDK HTTPClient (replaces direct httpx.AsyncClient) ──────── #
#
# We use imperal_sdk.http.HTTPClient directly (not ctx.http) because our
# _api_* helpers are module-level and called from panel render paths and
# handlers alike — threading ctx through every layer would be invasive.
# HTTPClient is the same wrapper ctx.http uses under the hood: typed
# HTTPResponse (.status_code / .body / .ok / .json()) and no hidden state
# that could bleed across tenants (per-request httpx.AsyncClient per call).

from imperal_sdk.http import HTTPClient

_http_client: HTTPClient | None = None


def _http() -> HTTPClient:
    global _http_client
    if _http_client is None:
        _http_client = HTTPClient(timeout=30)
    return _http_client


def _db_url(path: str) -> str:
    return f"{DB_SERVICE_URL.rstrip('/')}{path}"


def _auth_headers() -> dict:
    return {"x-api-key": DB_SERVICE_KEY} if DB_SERVICE_KEY else {}


def _extract_error(resp) -> dict:
    """Extract error detail from a failed HTTPResponse (SDK shape)."""
    body = resp.body
    if isinstance(body, dict):
        detail = body.get("detail") or str(body)
    elif isinstance(body, (bytes, str)):
        detail = body.decode() if isinstance(body, bytes) else body
        detail = detail or f"HTTP {resp.status_code}"
    else:
        detail = f"HTTP {resp.status_code}"
    return {"status": "error", "detail": detail}


def _safe_json(resp) -> dict:
    """Return resp.json() or a synthetic error dict if body isn't JSON."""
    try:
        return resp.json()
    except Exception:
        return {"status": "error", "detail": str(resp.body)[:500]}


# ─── Helpers ──────────────────────────────────────────────────────────── #

def _user_id(ctx) -> str:
    """Tolerant user-id read. Returns '' on missing ctx.user.

    Use from panel / skeleton renderers that must survive anonymous
    sessions. Chat handlers MUST use require_user_id() instead so an
    empty ctx fails loudly rather than silently scoping every backend
    query to no-user.
    """
    return ctx.user.imperal_id if hasattr(ctx, "user") and ctx.user else ""


def require_user_id(ctx) -> str:
    """Return ctx.user.imperal_id or raise. Use from every @chat.function handler.

    When a chain step arrives without ctx.user populated (kernel-side bug
    class observed 2026-04-23), a silent "" would scope every backend
    query to no-user and hand back empty lists — indistinguishable from
    a real empty collection. Raising makes the failure loud and catchable
    by the handler's except-clause (surfaces as ActionResult.error).
    """
    uid = _user_id(ctx)
    if not uid:
        raise RuntimeError(
            "No authenticated user on context. Refusing to query the backend "
            "with an empty user_id (would silently return no data)."
        )
    return uid


def _tenant_id(ctx) -> str:
    if hasattr(ctx, "user") and ctx.user and hasattr(ctx.user, "tenant_id"):
        return ctx.user.tenant_id
    return "default"


async def _api_post(path: str, data: dict) -> dict:
    r = await _http().post(_db_url(path), json=data, headers=_auth_headers())
    if not r.ok:
        return _extract_error(r)
    return _safe_json(r)


async def _api_get(path: str, params: dict = None) -> dict:
    r = await _http().get(_db_url(path), params=params or {}, headers=_auth_headers())
    if not r.ok:
        return _extract_error(r)
    return _safe_json(r)


async def _api_delete(path: str, params: dict = None) -> dict:
    r = await _http().delete(_db_url(path), params=params or {}, headers=_auth_headers())
    if not r.ok:
        return _extract_error(r)
    return _safe_json(r)


async def _api_patch(path: str, params: dict, data: dict) -> dict:
    r = await _http().patch(
        _db_url(path), params=params, json=data, headers=_auth_headers(),
    )
    if not r.ok:
        return _extract_error(r)
    return _safe_json(r)


# ─── Tiered schema HTTP helpers (Phase 2 — sql-db-scale) ──────────────── #
#
# Thin wrappers over the four tiers exposed by the backend routes_schema_v2:
#   T0 catalog      — list of databases on the connection
#   T1 tables page  — paginated table list for one database (no columns)
#   T2 table detail — columns + indexes + foreign keys for one table
#   T3 exact count  — explicit SELECT COUNT(*); user-action only
#
# All four tiers run with a 5s statement timeout on the target DB session
# (T3 raises this to 30s) so a sick target cannot stall the worker.

async def _api_catalog(ctx, conn: dict, conn_id: str) -> dict:
    """T0 — list of databases on this connection."""
    return await _api_post(f"/v1/connections/{conn_id}/catalog", {
        "user_id": _user_id(ctx),
        "connection": build_conn_info(conn),
    })


async def _api_tables_page(
    ctx, conn: dict, conn_id: str, database: str,
    *, search: str = "", offset: int = 0, limit: int = 200,
) -> dict:
    """T1 — paginated table list for one database."""
    return await _api_post(
        f"/v1/connections/{conn_id}/tables"
        f"?search={search}&offset={offset}&limit={limit}",
        {
            "user_id": _user_id(ctx),
            "database": database,
            "connection": build_conn_info(conn),
        },
    )


async def _api_table_detail(
    ctx, conn: dict, conn_id: str, database: str, table: str,
) -> dict:
    """T2 — columns + indexes + foreign keys for one table."""
    return await _api_post(
        f"/v1/connections/{conn_id}/tables/{table}/detail", {
            "user_id": _user_id(ctx),
            "database": database,
            "connection": build_conn_info(conn),
        },
    )


async def _api_exact_count(
    ctx, conn: dict, conn_id: str, database: str, table: str,
) -> dict:
    """T3 — real SELECT COUNT(*) for one table. Explicit user action."""
    return await _api_post(
        f"/v1/connections/{conn_id}/tables/{table}/count", {
            "user_id": _user_id(ctx),
            "database": database,
            "connection": build_conn_info(conn),
        },
    )


# ─── Connection helpers ───────────────────────────────────────────────── #

async def resolve_connection(ctx) -> tuple[dict | None, str]:
    """Find active connection. Fallback: first connection of user.

    Returns (conn_data, conn_id) or (None, "").
    """
    uid = _user_id(ctx)

    # Try is_active filter first
    try:
        page = await ctx.store.query(
            CONN_COLLECTION,
            where={"user_id": uid, "is_active": True},
            limit=1,
        )
        if page.data:
            return page.data[0].data, page.data[0].id
    except Exception as exc:
        log.warning("resolve_connection: active-flag query failed for user=%s: %s", uid, exc)

    # Fallback: any connection for this user. Logged so support can trace
    # surprising "wrong database" UX when a user has prod+staging saved
    # but neither is marked active.
    try:
        page = await ctx.store.query(
            CONN_COLLECTION,
            where={"user_id": uid},
            limit=1,
        )
        if page.data:
            conn = page.data[0].data
            log.warning(
                "resolve_connection: no active connection for user=%s, falling back to '%s' (id=%s)",
                uid, conn.get("name", "?"), page.data[0].id,
            )
            return conn, page.data[0].id
    except Exception as exc:
        log.warning("resolve_connection: fallback query failed for user=%s: %s", uid, exc)

    return None, ""


async def get_active_connection(ctx) -> dict | None:
    """Get active connection data (or first available)."""
    conn, _ = await resolve_connection(ctx)
    return conn


async def get_connection_by_id(ctx, conn_id: str) -> dict | None:
    """Get a specific connection from ctx.store."""
    doc = await ctx.store.get(CONN_COLLECTION, conn_id)
    return doc.data if doc else None


def build_conn_info(conn: dict) -> dict:
    """Build ConnInfo dict for backend requests."""
    return {
        "host": conn["host"],
        "port": conn.get("port", 3306),
        "user": conn["db_user"],
        "password_encrypted": conn["password_encrypted"],
        "database": conn.get("database", ""),
    }


# ─── System Prompt ────────────────────────────────────────────────────── #

from pathlib import Path as _Path
SYSTEM_PROMPT = (_Path(__file__).parent / "system_prompt.txt").read_text()


# ─── Extension ────────────────────────────────────────────────────────── #

ext = Extension(
    "sql-db",
    version="1.5.8",
    capabilities=["sql-db:read", "sql-db:write"],
)

# SDK 3.3+ — `model=` deprecated; LLM resolution moved to kernel ctx-injection
# (see ctx._llm_configs). Will be hard-removed in SDK 4.0.
chat = ChatExtension(
    ext=ext,
    tool_name="tool_sql_db_chat",
    description=(
        "SQL Database assistant — connect to MySQL/MariaDB databases, "
        "browse schema, run queries, explain plans, manage saved queries"
    ),
    system_prompt=SYSTEM_PROMPT,
)


# ─── Schema cache (SDK 1.6.x ctx.cache contract) ──────────────────────── #
#
# Skeleton is LLM-only in 1.6.0+ (SkeletonAccessForbidden raised from
# @chat.function). Schema snapshots required by write-time validation flow
# through ctx.cache instead. The skeleton refresher writes here after every
# successful refresh; @chat.function handlers read from here to validate
# tables/columns before round-tripping a doomed INSERT/UPDATE to the backend.

from pydantic import BaseModel as _BM

SCHEMA_CACHE_KEY = "schema:active"
SCHEMA_CACHE_TTL = 300  # matches @ext.skeleton(ttl=300); SDK cap is 300s.


class DbSchemaColumn(_BM):
    name: str
    type: str = ""
    key: str = ""


class DbSchemaTable(_BM):
    name: str
    rows: int = 0
    columns: list[DbSchemaColumn] = []


@ext.cache_model("db_schema_snapshot")
class DbSchemaSnapshot(_BM):
    """Mirror of the @ext.skeleton('db_schema') payload, readable from
    @chat.function handlers via ctx.cache. Shape matches skeleton.py.

    Registered directly with @ext.cache_model — SDK 1.6.x reverse-lookup in
    extension._resolve_cache_model_name uses class identity (`is`), not
    isinstance, so the *exact* class passed to ctx.cache.set/get must be
    the one carrying the decorator."""
    database: str = ""
    connection: str = ""
    table_count: int = 0
    tables: list[DbSchemaTable] = []
    note: str = ""


# ─── Tiered schema cache models (Phase 2 — sql-db-scale) ──────────────── #
#
# Power the sidebar's cache-only render path. Layout invariants:
#   - one envelope per logical query (catalog / tables-page / table-detail);
#   - never one giant blob (SDK envelope cap is 64 KB);
#   - schema_version on every envelope so clients detect drift between
#     a stale cached page and a freshly DDL'd target DB;
#   - cache keys are extension-scoped automatically by SDK, plus prefixed
#     by conn_id + database so multiple connections don't collide.
#
# Cache key conventions (see cache_key_* helpers below):
#   catalog:{conn_id}                              → CatalogCache
#   tables:{conn_id}:{db}:{search}:{offset}:{limit} → TablesPageCache
#   table:{conn_id}:{db}:{table_name}              → TableDetailCache

CATALOG_CACHE_TTL = 300
TABLES_PAGE_CACHE_TTL = 300
TABLE_DETAIL_CACHE_TTL = 600

SIDEBAR_PAGE_LIMIT = 200       # default page_size for first paint
HUGE_DB_THRESHOLD = 200        # > N tables → skeleton goes names-only


class CatalogDb(_BM):
    name: str
    table_count: int = 0
    schema_version: str = ""


@ext.cache_model("catalog")
class CatalogCache(_BM):
    """T0 cache — list of databases on a connection."""
    conn_id: str
    databases: list[CatalogDb] = []
    fetched_at: str = ""             # ISO8601


class TablesPageItem(_BM):
    name: str
    type: str = "BASE TABLE"
    engine: str = ""
    rows_estimate: int = 0
    size_bytes: int = 0
    last_modified: str | None = None
    comment: str = ""
    last_touched_at: str | None = None  # set by optimistic-UI patcher


@ext.cache_model("tables_page")
class TablesPageCache(_BM):
    """T1 cache — paginated table list for one database."""
    conn_id: str
    database: str
    search: str = ""
    offset: int = 0
    limit: int = SIDEBAR_PAGE_LIMIT
    items: list[TablesPageItem] = []
    total_count: int = 0
    schema_version: str = ""
    fetched_at: str = ""


class TableColumn(_BM):
    COLUMN_NAME: str = ""
    COLUMN_TYPE: str = ""
    IS_NULLABLE: str = ""
    COLUMN_KEY: str = ""
    COLUMN_DEFAULT: str | None = None
    EXTRA: str = ""
    COLUMN_COMMENT: str = ""


class TableIndex(_BM):
    name: str
    unique: bool = False
    columns: list[str] = []


class TableForeignKey(_BM):
    name: str = ""
    column_name: str = ""
    ref_schema: str = ""
    ref_table: str = ""
    ref_column: str = ""


@ext.cache_model("table_detail")
class TableDetailCache(_BM):
    """T2 cache — columns + indexes + FKs for one table."""
    conn_id: str
    database: str
    table: str
    type: str = "BASE TABLE"
    engine: str = ""
    rows_estimate: int = 0
    size_bytes: int = 0
    last_modified: str | None = None
    comment: str = ""
    columns: list[TableColumn] = []
    indexes: list[TableIndex] = []
    foreign_keys: list[TableForeignKey] = []
    schema_version: str = ""
    fetched_at: str = ""


# ─── Cache-key builders (single source of truth) ──────────────────────── #

def cache_key_catalog(conn_id: str) -> str:
    return f"catalog:{conn_id}"


def cache_key_tables_page(
    conn_id: str, database: str,
    search: str = "", offset: int = 0, limit: int = SIDEBAR_PAGE_LIMIT,
) -> str:
    return f"tables:{conn_id}:{database}:{search}:{offset}:{limit}"


def cache_key_table_detail(conn_id: str, database: str, table: str) -> str:
    return f"table:{conn_id}:{database}:{table}"


# ─── Health Check ─────────────────────────────────────────────────────── #

@ext.health_check
async def health(ctx) -> dict:
    try:
        r = await _http().get(_db_url("/health"), headers=_auth_headers())
        if not r.ok:
            return {"status": "degraded", "version": ext.version, "backend": "unreachable"}
        data = r.json() if isinstance(r.body, (dict, str)) else {}
        return {"status": "ok", "version": ext.version, "backend": data.get("status")}
    except Exception:
        return {"status": "degraded", "version": ext.version, "backend": "unreachable"}


# ─── Lifecycle ────────────────────────────────────────────────────────── #

@ext.on_install
async def on_install(ctx):
    log.info("sql-db installed for user %s", _user_id(ctx))
