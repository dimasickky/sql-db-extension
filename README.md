<div align="center">

# Imperal SQL DB

### Your personal AI-powered SQL workbench on Imperal Cloud.

**Connect. Query. Explore. — in plain English or pure SQL.**

[![Platform](https://img.shields.io/badge/platform-Imperal%20Cloud-blue)](https://imperal.io)
[![SDK](https://img.shields.io/badge/imperal--sdk-1.5.7-blue)](https://pypi.org/project/imperal-sdk/)
[![Version](https://img.shields.io/badge/version-1.0.0-brightgreen)]()
[![License](https://img.shields.io/badge/license-LGPL--3.0-blue)](LICENSE)

[Features](#features) | [Functions](#functions) | [Architecture](#architecture) | [Development](#development) | [Platform](https://imperal.io)

</div>

---

## What is Imperal SQL DB?

**Imperal SQL DB** is a first-party AI extension for [Imperal Cloud](https://imperal.io) — the world's first AI Cloud OS.

It connects to any MySQL / MariaDB database and lets users read, query, and modify data through natural language or raw SQL. Passwords are encrypted with Fernet; queries are validated, classified, and optionally rolled back. Results render inline in a self-contained editor panel — no round-trip through chat required.

```
User: "подключись к базе"
  → SQL DB prompts for host/user/password, tests, saves.

User: "какие у меня таблицы?"
  → get_schema → returns table list with row counts and columns.

User: "средняя цена товара по каждой категории"
  → nl_to_sql generates SQL → run_query → returns a DataTable.

User types SQL in editor panel, hits Run
  → panel calls backend directly → renders result inline, no chat noise.
```

---

## Features

| Feature | Description |
|---------|-------------|
| **AI Chat** | Natural-language interface — connect, browse, query, modify by talking |
| **SQL Editor Panel** | Self-contained center panel with Action dropdown (Run / Explain / Dry Run) |
| **Multi-statement** | Paste several queries separated by `;` — executed sequentially with per-statement results |
| **Auto-routing** | Detects SELECT vs DML/DDL and dispatches to the right backend endpoint; auto-fallback if wrong |
| **Inline results** | DataTable / Alert rendered in the panel itself — not in chat |
| **EXPLAIN** | One-click execution plan on any query |
| **Dry Run** | Execute DML inside a transaction, count affected rows, ROLLBACK — zero side effects |
| **Fernet encryption** | DB passwords encrypted before storage; decrypted only in the backend service |
| **Schema tree** | Sidebar shows tables, columns, indexes for the active connection |
| **Query history** | Every query logged; browse recent executions in the History tab |
| **Saved queries** | Name & save queries for reuse; run by click or by chat reference |
| **NL → SQL** | Ask a question in plain language; schema-aware SQL is generated |
| **Skeleton** | Background schema cache — LLM always has fresh structure context |
| **2-Step Confirmation** | Destructive `execute_sql` / `delete_connection` require explicit confirmation |

---

## Functions

The AI routes user intent to the correct function automatically.

### Connections

| Function | Action | Description |
|----------|--------|-------------|
| `add_connection` | write | Add a new MySQL/MariaDB connection (test → encrypt → save) |
| `list_connections` | read | List all saved connections |
| `test_connection` | read | Re-test an existing connection |
| `select_connection` | write | Switch active connection |
| `delete_connection` | destructive | Remove a saved connection |

### Schema & Queries

| Function | Action | Description |
|----------|--------|-------------|
| `get_schema` | read | Tables, columns, indexes for a database |
| `run_query` | read | Execute a SELECT query with auto-LIMIT |
| `execute_sql` | destructive | INSERT / UPDATE / DELETE / ALTER / CREATE / DROP — requires confirmation |
| `explain_query` | read | Return the EXPLAIN plan for a query |
| `dry_run` | read | Run DML in a transaction, count affected rows, ROLLBACK |
| `nl_to_sql` | read | Convert a natural-language question into SQL using the cached schema |
| `run_editor_sql` | write | Universal editor runner — auto-detects type and dispatches |

### History & Saved

| Function | Action | Description |
|----------|--------|-------------|
| `list_history` | read | Recent query history for the active connection |
| `save_query` | write | Name and save a SQL for reuse |
| `list_saved` | read | List saved queries |
| `run_saved` | read | Run a saved query by ID |
| `delete_saved` | destructive | Delete a saved query |

---

## Architecture

```
Imperal Panel
        │
        ├── Sidebar Panel (left)          ← @ext.panel("sidebar")
        │   connections + schema tree       panels.py
        │
        └── Editor Panel (center)         ← @ext.panel("editor")
            ui.Form → Action dropdown       panels_editor.py
            + TextArea → inline results
            + History / Saved tabs

Extension chat functions
        │
        ├── handlers_connections.py (connection CRUD)
        ├── handlers_query.py       (query/explain/dry_run/schema)
        ├── handlers_execute.py     (DML/DDL + universal editor)
        ├── handlers_nlq.py         (NL → SQL via ctx.ai)
        ├── handlers_history.py     (history + saved)
        └── skeleton.py             (background schema cache)

Backend API (separate service)
        │
        ├── Fernet decrypt password
        ├── SQL validator  (classify read/write, auto-LIMIT)
        └── connects to your MySQL / MariaDB
```

### File Structure

```
sql-db-extension/
├── main.py                   # Entry point — sys.modules cleanup + imports
├── app.py                    # Extension setup, HTTP helpers, Fernet, health check
├── handlers_connections.py   # Connection CRUD + test + select
├── handlers_query.py         # run_query / get_schema / explain / dry_run
├── handlers_execute.py       # execute_sql + run_editor_sql
├── handlers_nlq.py           # nl_to_sql via ctx.ai
├── handlers_history.py       # history + saved queries
├── skeleton.py               # Background schema cache
├── panels.py                 # Sidebar panel (left slot)
├── panels_editor.py          # Editor panel (center slot) — self-contained
├── system_prompt.txt         # AI assistant instructions
└── imperal.json              # Extension manifest
```

---

## Events

The extension publishes the following events for use in Automation Rules:

| Event | Trigger |
|-------|---------|
| `connection.added` | New database connection saved |
| `connection.selected` | Active connection switched |
| `connection.deleted` | Saved connection removed |
| `sql.executed` | DML/DDL executed via `execute_sql` |
| `query.saved` | Query saved for reuse |
| `query.deleted` | Saved query deleted |

---

## Environment

The extension reads these env vars on the platform:

| Variable | Purpose |
|----------|---------|
| `DB_SERVICE_URL` | URL of the backend API (e.g. `https://the backend.internal.example.com`) — required |
| `DB_SERVICE_KEY` | API key for the backend |
| `SQL_DB_ENCRYPTION_KEY` | Fernet key — MUST match the backend key (shared secret) |

---

## Development

Built with [Imperal SDK](https://github.com/imperalcloud/imperal-sdk) v1.5.7.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
imperal validate
imperal dev
```

### SDK Compliance

- All `@chat.function` handlers return `ActionResult`
- Pydantic `BaseModel` params with `Field(description=...)`
- All write/destructive functions declare `event=`
- No files exceed 300 lines (largest is `panels_editor.py` ≈ 350 w/ docstrings)
- `@ext.health_check` + `@ext.on_install` registered
- Declarative UI only via `imperal_sdk.ui` — no custom React
- No hardcoded credentials — all secrets via env vars
- Fernet-encrypted passwords in transit and at rest

---

## Links

- **Platform:** [imperal.io](https://imperal.io)
- **SDK:** [github.com/imperalcloud/imperal-sdk](https://github.com/imperalcloud/imperal-sdk)
- **PyPI:** [pypi.org/project/imperal-sdk](https://pypi.org/project/imperal-sdk/)
- **License:** [LGPL-3.0](LICENSE)

---

<div align="center">

**Built for [Imperal Cloud](https://imperal.io)**

*The AI Cloud OS.*

</div>
