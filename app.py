"""sql-db · Shared state & extension setup."""
import logging
import os
import re as _re

from imperal_sdk import Extension
from imperal_sdk.chat import ChatExtension, ActionResult  # noqa: F401 — re-exported

log = logging.getLogger("sql-db")


# ─── Config ───────────────────────────────────────────────────────────────── #

DB_SERVICE_URL = os.environ["DB_SERVICE_URL"]
DB_SERVICE_KEY = os.getenv("DB_SERVICE_KEY", "")
SQL_DB_ENCRYPTION_KEY = os.getenv("SQL_DB_ENCRYPTION_KEY", "")

CONN_COLLECTION = "db_connections"


# ─── Fernet (encrypt passwords before sending to backend) ─────────────────── #

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


# ─── MySQL error translator ───────────────────────────────────────────────── #

_MYSQL_CODE_RE = _re.compile(r'^\((\d{4}),\s*[\'"]')


def _translate_db_error(detail: str) -> str:
    """Translate a raw MySQL error string to a human-readable message.

    Parses the (NNNN, 'text') tuple format returned by aiomysql/pymysql
    and maps the most common error codes to actionable messages.
    Falls back to the original string when the format is unrecognised.
    """
    if not detail:
        return detail
    m = _MYSQL_CODE_RE.match(detail.strip())
    if not m:
        return detail
    code = int(m.group(1))

    if code == 1451:
        ref = _re.search(r'REFERENCES\s+`?(\w+)`?', detail)
        ref_table = ref.group(1) if ref else "a related table"
        return (
            f"Cannot delete: this record is referenced by '{ref_table}'. "
            f"Remove or reassign the related records there first."
        )
    if code == 1452:
        ref = _re.search(r'REFERENCES\s+`?(\w+)`?', detail)
        ref_table = ref.group(1) if ref else "a related table"
        return f"Cannot insert/update: the referenced record does not exist in '{ref_table}'."
    if code == 1062:
        m2 = _re.search(r"Duplicate entry '([^']+)' for key '([^']+)'", detail)
        if m2:
            return f"Duplicate value '{m2.group(1)}' — violates unique constraint on '{m2.group(2)}'."
        return "Duplicate value: a record with this key already exists."
    if code == 1054:
        m2 = _re.search(r"Unknown column '([^']+)'", detail)
        col = m2.group(1) if m2 else "unknown"
        return f"Unknown column '{col}'. Call get_schema() to check the available columns."
    if code == 1064:
        return "SQL syntax error — check the query and try again."
    if code == 1146:
        m2 = _re.search(r"Table '([^']+)' doesn't exist", detail)
        tbl = m2.group(1) if m2 else "unknown"
        return f"Table '{tbl}' does not exist. Call get_schema() to see available tables."
    if code == 1406:
        return "Data too long for one of the columns."

    return detail


# ─── HTTP helpers (ctx-scoped, per-request, no shared state) ──────────────── #

def _db_url(path: str) -> str:
    return f"{DB_SERVICE_URL.rstrip('/')}{path}"


def _auth() -> dict:
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


def _safe_body(resp) -> dict:
    """Return parsed body dict, or a synthetic error dict if body isn't a dict."""
    body = resp.body
    if isinstance(body, dict):
        return body
    return {"status": "error", "detail": str(body)[:500]}


async def _api_post(ctx, path: str, data: dict) -> dict:
    r = await ctx.http.post(_db_url(path), json=data, headers=_auth())
    if not r.ok:
        return _extract_error(r)
    return _safe_body(r)


async def _api_get(ctx, path: str, params: dict | None = None) -> dict:
    r = await ctx.http.get(_db_url(path), params=params or {}, headers=_auth())
    if not r.ok:
        return _extract_error(r)
    return _safe_body(r)


async def _api_delete(ctx, path: str, params: dict | None = None) -> dict:
    r = await ctx.http.delete(_db_url(path), params=params or {}, headers=_auth())
    if not r.ok:
        return _extract_error(r)
    return _safe_body(r)


async def _api_patch(ctx, path: str, params: dict, data: dict) -> dict:
    r = await ctx.http.patch(_db_url(path), params=params, json=data, headers=_auth())
    if not r.ok:
        return _extract_error(r)
    return _safe_body(r)


# ─── Tiered schema HTTP helpers (Phase 2 — sql-db-scale) ──────────────────── #

async def _api_catalog(ctx, conn: dict, conn_id: str) -> dict:
    """T0 — list of databases on this connection."""
    return await _api_post(ctx, f"/v1/connections/{conn_id}/catalog", {
        "user_id": _user_id(ctx),
        "connection": build_conn_info(conn),
    })


async def _api_tables_page(
    ctx, conn: dict, conn_id: str, database: str,
    *, search: str = "", offset: int = 0, limit: int = 200,
) -> dict:
    """T1 — paginated table list for one database."""
    return await _api_post(
        ctx,
        f"/v1/connections/{conn_id}/tables"
        f"?search={search}&offset={offset}&limit={limit}",
        {
            "user_id":    _user_id(ctx),
            "database":   database,
            "connection": build_conn_info(conn),
        },
    )


async def _api_table_detail(
    ctx, conn: dict, conn_id: str, database: str, table: str,
) -> dict:
    """T2 — columns + indexes + foreign keys for one table."""
    return await _api_post(
        ctx,
        f"/v1/connections/{conn_id}/tables/{table}/detail", {
            "user_id":    _user_id(ctx),
            "database":   database,
            "connection": build_conn_info(conn),
        },
    )


async def _api_exact_count(
    ctx, conn: dict, conn_id: str, database: str, table: str,
) -> dict:
    """T3 — real SELECT COUNT(*) for one table. Explicit user action."""
    return await _api_post(
        ctx,
        f"/v1/connections/{conn_id}/tables/{table}/count", {
            "user_id":    _user_id(ctx),
            "database":   database,
            "connection": build_conn_info(conn),
        },
    )


# ─── Identity helpers ─────────────────────────────────────────────────────── #

def _user_id(ctx) -> str:
    """Tolerant user-id read. Returns '' for anonymous panel/skeleton contexts."""
    return ctx.user.imperal_id if hasattr(ctx, "user") and ctx.user else ""


def require_user_id(ctx) -> str:
    """Return ctx.user.imperal_id or raise. Every @chat.function handler must call this."""
    uid = _user_id(ctx)
    if not uid:
        raise RuntimeError(
            "No authenticated user on context. Refusing to query db-service "
            "with an empty user_id (would silently return no data)."
        )
    return uid


def _tenant_id(ctx) -> str:
    if hasattr(ctx, "user") and ctx.user and hasattr(ctx.user, "tenant_id"):
        return ctx.user.tenant_id
    return "default"


# ─── Connection helpers ───────────────────────────────────────────────────── #

async def resolve_connection(ctx) -> tuple[dict | None, str]:
    """Find active connection. Fallback: first connection of user."""
    uid = _user_id(ctx)
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
    conn, _ = await resolve_connection(ctx)
    return conn


async def get_connection_by_id(ctx, conn_id: str) -> dict | None:
    doc = await ctx.store.get(CONN_COLLECTION, conn_id)
    return doc.data if doc else None


def build_conn_info(conn: dict) -> dict:
    return {
        "host":               conn["host"],
        "port":               conn.get("port", 3306),
        "user":               conn["db_user"],
        "password_encrypted": conn["password_encrypted"],
        "database":           conn.get("database", ""),
    }


# ─── Extension ───────────────────────────────────────────────────────────── #

ext = Extension(
    "sql-db",
    version="2.14.0",
    capabilities=["sql-db:read", "sql-db:write"],
    display_name="SQL Database",
    description=(
        "Connect to MySQL/MariaDB databases, browse schema, run queries, "
        "execute DML/DDL, manage saved queries, and insert or update rows."
    ),
    icon="icon.svg",
    actions_explicit=True,
)


# ─── Schema cache (SDK ctx.cache contract) ────────────────────────────────── #

from pydantic import BaseModel as _BM

SCHEMA_CACHE_KEY = "schema:active"
SCHEMA_CACHE_TTL = 300


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
    database: str = ""
    connection: str = ""
    table_count: int = 0
    tables: list[DbSchemaTable] = []
    note: str = ""


# ─── Tiered schema cache models ───────────────────────────────────────────── #

CATALOG_CACHE_TTL = 300
TABLES_PAGE_CACHE_TTL = 300
TABLE_DETAIL_CACHE_TTL = 600

SIDEBAR_PAGE_LIMIT = 200
HUGE_DB_THRESHOLD = 200


class CatalogDb(_BM):
    name: str
    table_count: int = 0
    schema_version: str = ""


@ext.cache_model("catalog")
class CatalogCache(_BM):
    conn_id: str
    databases: list[CatalogDb] = []
    fetched_at: str = ""


class TablesPageItem(_BM):
    name: str
    type: str = "BASE TABLE"
    engine: str = ""
    rows_estimate: int = 0
    size_bytes: int = 0
    last_modified: str | None = None
    comment: str = ""
    last_touched_at: str | None = None


@ext.cache_model("tables_page")
class TablesPageCache(_BM):
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


# ─── Cache-key builders ────────────────────────────────────────────────────── #

def cache_key_catalog(conn_id: str) -> str:
    return f"catalog:{conn_id}"


def cache_key_tables_page(
    conn_id: str, database: str,
    search: str = "", offset: int = 0, limit: int = SIDEBAR_PAGE_LIMIT,
) -> str:
    return f"tables:{conn_id}:{database}:{search}:{offset}:{limit}"


def cache_key_table_detail(conn_id: str, database: str, table: str) -> str:
    return f"table:{conn_id}:{database}:{table}"


# ─── ChatExtension ───────────────────────────────────────────────────────── #

chat = ChatExtension(
    ext=ext,
    tool_name="tool_sql_db_chat",
    description=(
        "SQL Database assistant — connect to MySQL/MariaDB databases, "
        "browse schema, run queries, explain plans, manage saved queries"
    ),
)


# ─── Lifecycle ────────────────────────────────────────────────────────────── #

@ext.health_check
async def health(ctx) -> dict:
    try:
        r = await ctx.http.get(_db_url("/health"), headers=_auth())
        if not r.ok:
            return {"status": "degraded", "version": ext.version, "backend": "unreachable"}
        body = r.body if isinstance(r.body, dict) else {}
        return {"status": "ok", "version": ext.version, "backend": body.get("status")}
    except Exception:
        return {"status": "degraded", "version": ext.version, "backend": "unreachable"}


@ext.on_install
async def on_install(ctx) -> None:
    log.info("sql-db installed for user %s", _user_id(ctx))
