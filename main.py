"""sql-db v1.1.0 · SQL Database assistant with schema browsing + query execution + row CRUD.

Build: 2026-04-16 ghost-hunt
"""
from __future__ import annotations

import sys, os
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)
for _m in [k for k in sys.modules if k in (
    "app", "handlers_connections", "handlers_query", "handlers_execute",
    "handlers_nlq", "handlers_history", "handlers_rows",
    "skeleton", "panels", "panels_editor",
    "panels_editor_results", "panels_editor_tabs", "panels_editor_row_form",
    "sql_parser",
)]:
    del sys.modules[_m]

from app import ext, chat  # noqa: F401

import handlers_connections    # noqa: F401
import handlers_query          # noqa: F401
import handlers_execute        # noqa: F401
import handlers_nlq            # noqa: F401
import handlers_history        # noqa: F401
import handlers_rows           # noqa: F401  # row CRUD (insert/update/delete)
import skeleton                # noqa: F401
import panels                  # noqa: F401
import panels_editor           # noqa: F401  # center overlay SQL editor
