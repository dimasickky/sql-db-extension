"""sql-db · Pure SQL parsing helpers (no UI, no I/O)."""
from __future__ import annotations


# ─── Statement splitter ───────────────────────────────────────────────── #

_SQL_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "AND", "OR", "GROUP BY", "ORDER BY", "HAVING",
    "LIMIT", "OFFSET", "JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "OUTER JOIN",
    "CROSS JOIN", "ON", "INSERT INTO", "INSERT", "VALUES", "UPDATE", "SET",
    "DELETE FROM", "DELETE", "CREATE TABLE", "ALTER TABLE", "DROP TABLE",
    "TRUNCATE TABLE", "TRUNCATE", "UNION ALL", "UNION", "EXPLAIN", "SHOW",
    "DESCRIBE", "DESC", "IN", "AS", "BY", "DISTINCT", "COUNT", "SUM", "AVG",
    "MIN", "MAX", "BETWEEN", "LIKE", "IS NULL", "IS NOT NULL", "CASE", "WHEN",
    "THEN", "ELSE", "END", "PRIMARY KEY", "FOREIGN KEY", "REFERENCES", "INDEX"
}

_MAJOR_CLAUSES = [
    "SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING",
    "LIMIT", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "OUTER JOIN",
    "CROSS JOIN", "JOIN", "SET", "VALUES"
]


def format_sql(sql: str) -> str:
    """Format and pretty-print SQL with proper indentation and capitalized keywords."""
    if not sql or not sql.strip():
        return ""
    
    stmts = split_statements(sql)
    formatted_stmts = []
    
    for stmt in stmts:
        tokens = stmt.split()
        if not tokens:
            continue
        
        lines: list[str] = []
        curr_line: list[str] = []
        indent = "  "
        
        i = 0
        while i < len(tokens):
            w = tokens[i]
            upper_w = w.upper().rstrip(",;")
            
            # Check 2-word clauses first (e.g. GROUP BY, ORDER BY, LEFT JOIN, INSERT INTO, DELETE FROM)
            two_word = ""
            if i + 1 < len(tokens):
                two_word = f"{tokens[i].upper()} {tokens[i+1].upper().rstrip(',;')}"
            
            matched_clause = None
            advance = 1
            if two_word in _MAJOR_CLAUSES:
                matched_clause = two_word
                advance = 2
            elif upper_w in _MAJOR_CLAUSES:
                matched_clause = upper_w
                advance = 1
            
            if matched_clause:
                if curr_line:
                    lines.append(" ".join(curr_line))
                    curr_line = []
                
                clause_header = matched_clause
                if advance == 2 and tokens[i+1].endswith(","):
                    clause_header += ","
                elif advance == 1 and w.endswith(","):
                    clause_header += ","
                
                curr_line.append(clause_header)
                i += advance
            else:
                # Check keyword capitalization
                clean_w = w.strip("`,()").upper()
                if clean_w in _SQL_KEYWORDS and not (w.startswith("`") or w.startswith("'") or w.startswith('"')):
                    # preserve punctuation around keyword
                    formatted_w = w.upper()
                    curr_line.append(formatted_w)
                else:
                    curr_line.append(w)
                i += 1
        
        if curr_line:
            lines.append(" ".join(curr_line))
        
        # Apply indentation to subsequent lines
        indented_lines = []
        for idx, line in enumerate(lines):
            if idx == 0:
                indented_lines.append(line)
            else:
                indented_lines.append(indent + line)
        
        formatted_stmts.append("\n".join(indented_lines) + ";")
    
    return "\n\n".join(formatted_stmts)


def is_dangerous_sql(sql: str) -> tuple[bool, str]:
    """Check whether SQL contains dangerous or destructive statements (DROP, TRUNCATE, DELETE without WHERE)."""
    if not sql or not sql.strip():
        return False, ""
    
    stmts = split_statements(sql)
    for stmt in stmts:
        fw, is_read, is_explain = classify_sql(stmt)
        upper = stmt.upper()
        if fw in ("DROP", "TRUNCATE"):
            return True, f"High-risk DDL operation ({fw})"
        if fw == "DELETE" and "WHERE" not in upper:
            return True, "DELETE statement without a WHERE clause (will delete ALL rows)"
        if fw == "UPDATE" and "WHERE" not in upper:
            return True, "UPDATE statement without a WHERE clause (will update ALL rows)"
        if fw in ("ALTER", "RENAME"):
            return True, f"Schema modification DDL operation ({fw})"
    return False, ""


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


# ─── DDL vs DML event classifier (Phase 2 — sql-db-scale) ────────────── #
#
# Used by fn_run_editor_sql to emit the right event after a successful
# execute: DDL → sql.ddl_executed (sidebar refetches), DML → table.touched
# (optimistic patch). Nothing here parses the FROM clause of a SELECT — read
# paths emit no schema-related event at all.

_DDL_KINDS = {
    "CREATE": "create",
    "DROP": "drop",
    "ALTER": "alter",
    "RENAME": "rename",
    "TRUNCATE": "truncate",
}
_DML_KINDS = {
    "INSERT": "insert",
    "UPDATE": "update",
    "DELETE": "delete",
    "REPLACE": "insert",   # MySQL REPLACE acts like delete+insert; net
                           # sidebar effect is touch + (likely) +1 row.
}


def classify_event_kind(sql: str) -> tuple[str, str | None, str | None]:
    """Classify a successfully-executed statement for event routing.

    Returns ``(class, subkind, target_table_or_None)`` where ``class`` is
    one of ``"ddl"``, ``"dml"``, ``"read"``, ``"explain"``, ``"other"``.
    Subkind is the lowercased verb (``"insert"``, ``"create"``, …) — useful
    for metric labels and the table-touched optimistic-patch direction.

    target_table is best-effort: we reuse extract_target_tables() and pick
    the first hit. Good enough to scope a cache invalidation; mismatches
    fall back to the 5-min TTL.
    """
    if not sql:
        return ("other", None, None)
    fw, is_read, is_explain = classify_sql(sql.strip())
    if is_explain:
        return ("explain", "explain", None)
    if is_read:
        return ("read", (fw or "").lower() or None, None)
    if fw in _DDL_KINDS:
        targets = extract_target_tables(sql)
        return ("ddl", _DDL_KINDS[fw], targets[0] if targets else None)
    if fw in _DML_KINDS:
        targets = extract_target_tables(sql)
        return ("dml", _DML_KINDS[fw], targets[0] if targets else None)
    return ("other", (fw or "").lower() or None, None)


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
