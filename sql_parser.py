"""sql-db · Pure SQL parsing helpers (no UI, no I/O)."""
from __future__ import annotations


# ─── Statement splitter ───────────────────────────────────────────────── #

def split_statements(sql: str) -> list[str]:
    """Split SQL on `;` outside of quotes. Returns non-empty trimmed statements."""
    parts: list[str] = []
    buf: list[str] = []
    quote = None
    i = 0
    while i < len(sql):
        c = sql[i]
        if quote:
            buf.append(c)
            if c == quote and sql[i - 1] != "\\":
                quote = None
        else:
            if c in ("'", '"', "`"):
                quote = c
                buf.append(c)
            elif c == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    parts.append(stmt)
                buf = []
            else:
                buf.append(c)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


# ─── Classify SQL (read vs write vs explain) ──────────────────────────── #

def classify_sql(sql_clean: str) -> tuple[str, bool, bool]:
    """Return (first_word, is_read, is_explain). Strips leading comments."""
    s = sql_clean
    while s.startswith("--") or s.startswith("/*"):
        if s.startswith("--"):
            nl = s.find("\n")
            s = s[nl + 1:].strip() if nl >= 0 else ""
        else:
            end = s.find("*/")
            s = s[end + 2:].strip() if end >= 0 else ""

    if not s:
        return "", True, False

    first_word = s.split()[0].upper()
    is_explain = first_word == "EXPLAIN"

    if first_word == "WITH":
        lower = " " + s.lower() + " "
        has_write = any(kw in lower for kw in
                        (" insert ", " update ", " delete ", " replace "))
        is_read = not has_write
    else:
        is_read = first_word in ("SELECT", "SHOW", "DESCRIBE", "DESC")

    return first_word, is_read, is_explain


# ─── Best-effort table extractor for write statements ─────────────────── #

import re as _re

_TABLE_AFTER = _re.compile(
    r"\b(?:INTO|FROM|UPDATE|TABLE)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
    _re.IGNORECASE,
)


def extract_target_tables(sql: str) -> list[str]:
    """Heuristic: pull table names after INTO/FROM/UPDATE/TABLE.

    Intentionally conservative — returns [] for anything non-obvious (e.g.
    subqueries, derived tables). Callers MUST treat [] as "unknown, skip
    validation" rather than "no tables".
    """
    if not sql:
        return []
    seen: list[str] = []
    for m in _TABLE_AFTER.finditer(sql):
        name = m.group(1)
        if name and name.upper() not in {"SELECT", "WHERE", "SET", "VALUES"} and name not in seen:
            seen.append(name)
    return seen


# ─── Column extractors for write-time guard ───────────────────────────── #

_INSERT_COLS = _re.compile(
    r"INSERT\s+(?:IGNORE\s+)?INTO\s+`?[A-Za-z_][A-Za-z0-9_]*`?\s*\(([^)]+)\)",
    _re.IGNORECASE,
)

_UPDATE_HEAD = _re.compile(
    r"UPDATE\s+`?[A-Za-z_][A-Za-z0-9_]*`?\s+SET\s+",
    _re.IGNORECASE,
)


def _find_set_clause(sql: str) -> str | None:
    """Return the substring between `SET` and the top-level WHERE/ORDER/LIMIT
    boundary, with paren / quote depth tracked so a nested `WHERE` in a
    subquery does not terminate the SET clause early."""
    m = _UPDATE_HEAD.search(sql)
    if not m:
        return None
    start = m.end()
    depth = 0
    quote: str | None = None
    i = start
    n = len(sql)
    boundaries = ("WHERE", "ORDER", "LIMIT")
    while i < n:
        c = sql[i]
        if quote:
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"', "`"):
            quote = c
            i += 1
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        if c == ";":
            return sql[start:i]
        if depth == 0 and c.isspace():
            # check for boundary keyword
            j = i + 1
            while j < n and sql[j].isspace():
                j += 1
            for kw in boundaries:
                kl = len(kw)
                if (
                    sql[j:j + kl].upper() == kw
                    and (j + kl == n or not sql[j + kl].isalnum())
                ):
                    return sql[start:i]
            i = j
            continue
        i += 1
    return sql[start:]


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` ignoring separators inside (), '', "", or backticks."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for c in s:
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            continue
        if c in ("'", '"', "`"):
            quote = c
            buf.append(c)
            continue
        if c == "(":
            depth += 1
            buf.append(c)
            continue
        if c == ")":
            depth -= 1
            buf.append(c)
            continue
        if c == sep and depth == 0:
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(c)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def extract_insert_columns(sql: str) -> list[str]:
    """Extract column names from `INSERT INTO t (a, b, c) VALUES ...`.

    Returns [] when the column list is omitted (positional INSERT) — caller
    treats [] as "skip column-level validation" because positional INSERTs
    rely on table-order and the backend will produce a more useful error.
    """
    if not sql:
        return []
    m = _INSERT_COLS.search(sql)
    if not m:
        return []
    inner = m.group(1)
    cols = _split_top_level(inner, ",")
    return [c.strip().strip("`").strip('"') for c in cols if c.strip()]


def extract_update_columns(sql: str) -> list[str]:
    """Extract LHS column names from `UPDATE t SET a=1, b='x' WHERE ...`.

    Conservative: returns [] when the SET clause cannot be isolated (CTEs,
    nested subqueries assigning columns, etc.). Caller treats [] as
    "skip column-level validation".
    """
    if not sql:
        return []
    inner = _find_set_clause(sql)
    if not inner:
        return []
    pairs = _split_top_level(inner, ",")
    cols: list[str] = []
    for p in pairs:
        if "=" not in p:
            return []  # malformed — bail to backend
        lhs = p.split("=", 1)[0].strip().strip("`").strip('"')
        if lhs:
            cols.append(lhs)
    return cols
