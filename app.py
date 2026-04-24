"""sql-db · SqlDbExtension instance, db-service HTTP client, Fernet helpers.

Loader entry point: module-level ``ext`` is the Extension subclass instance
the kernel discovers by duck-typing (``hasattr(attr, 'tools')``). Panels and
skeleton bind against this instance unchanged.

The Fernet key is loaded lazily so the validator (which may import
``main.py`` before worker secrets are wired) doesn't fail at import time.
"""
from __future__ import annotations

import logging
import os

from imperal_sdk.http import HTTPClient

from tools import SqlDbExtension

log = logging.getLogger("sql-db")


# ─── Config ──────────────────────────────────────────────────────────── #

DB_SERVICE_URL = os.environ["DB_SERVICE_URL"]
DB_SERVICE_KEY = os.getenv("DB_SERVICE_KEY", "")
SQL_DB_ENCRYPTION_KEY = os.getenv("SQL_DB_ENCRYPTION_KEY", "")

CONN_COLLECTION = "db_connections"


# ─── Fernet — lazy, first call only ──────────────────────────────────── #

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
    """Fernet-encrypt a DB password at the extension boundary.

    Passwords NEVER leave this extension in plaintext: every call into the
    db-service carries ``password_encrypted`` only, and db-service / MariaDB
    never see the Fernet key. Losing the Fernet key = losing every saved
    password — back it up out-of-band.
    """
    return _get_fernet().encrypt(plaintext.encode()).decode()


# ─── HTTP client (module-level, SDK HTTPClient) ──────────────────────── #
#
# Same wrapper ctx.http uses — typed HTTPResponse, per-request
# httpx.AsyncClient, no cross-tenant state. Module-level because _api_*
# helpers are shared across tools, panels, and skeleton refreshers.

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
    try:
        return resp.json()
    except Exception:
        return {"status": "error", "detail": str(resp.body)[:500]}


# ─── Identity helpers ─────────────────────────────────────────────────── #

def _user_id(ctx) -> str:
    return ctx.user.id if hasattr(ctx, "user") and ctx.user else ""


def require_user_id(ctx) -> str:
    uid = _user_id(ctx)
    if not uid:
        raise RuntimeError(
            "No authenticated user on context. Refusing to query db-service "
            "with an empty user_id (silently returns 0 rows = wrong data).",
        )
    return uid


def _tenant_id(ctx) -> str:
    if hasattr(ctx, "user") and ctx.user and hasattr(ctx.user, "tenant_id"):
        return ctx.user.tenant_id
    return "default"


# ─── Backend API helpers ─────────────────────────────────────────────── #

async def _api_post(path: str, data: dict) -> dict:
    r = await _http().post(_db_url(path), json=data, headers=_auth_headers())
    if not r.ok:
        return _extract_error(r)
    return _safe_json(r)


async def _api_get(path: str, params: dict | None = None) -> dict:
    r = await _http().get(_db_url(path), params=params or {}, headers=_auth_headers())
    if not r.ok:
        return _extract_error(r)
    return _safe_json(r)


async def _api_delete(path: str, params: dict | None = None) -> dict:
    r = await _http().delete(
        _db_url(path), params=params or {}, headers=_auth_headers(),
    )
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


# ─── Connection resolution ───────────────────────────────────────────── #

async def resolve_connection(ctx) -> tuple[dict | None, str]:
    """Find active connection, with a sane fallback to any saved one.

    The fallback is logged because silent "wrong database" UX confuses
    users who have prod + staging saved but neither marked active.
    """
    uid = _user_id(ctx)

    try:
        page = await ctx.store.query(
            CONN_COLLECTION, where={"user_id": uid, "is_active": True}, limit=1,
        )
        if page.data:
            return page.data[0].data, page.data[0].id
    except Exception:
        pass

    try:
        page = await ctx.store.query(
            CONN_COLLECTION, where={"user_id": uid}, limit=1,
        )
        if page.data:
            conn = page.data[0].data
            log.warning(
                "resolve_connection: no active for user=%s, falling back to '%s' (id=%s)",
                uid, conn.get("name", "?"), page.data[0].id,
            )
            return conn, page.data[0].id
    except Exception:
        pass

    return None, ""


async def get_active_connection(ctx) -> dict | None:
    conn, _ = await resolve_connection(ctx)
    return conn


async def get_connection_by_id(ctx, conn_id: str) -> dict | None:
    doc = await ctx.store.get(CONN_COLLECTION, conn_id)
    return doc.data if doc else None


def build_conn_info(conn: dict) -> dict:
    """Serialise a saved connection row into the wire shape db-service wants."""
    return {
        "host":               conn["host"],
        "port":               conn.get("port", 3306),
        "user":               conn["db_user"],
        "password_encrypted": conn["password_encrypted"],
        "database":           conn.get("database", ""),
    }


# ─── Extension instance (loader entry point) ─────────────────────────── #

ext = SqlDbExtension(
    app_id="sql-db",
    version="2.0.0",
    capabilities=["sql-db:read", "sql-db:write"],
)


# ─── Health ──────────────────────────────────────────────────────────── #

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


@ext.on_install
async def on_install(ctx):
    log.info("sql-db installed for user %s", _user_id(ctx))
