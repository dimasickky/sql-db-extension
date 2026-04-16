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
