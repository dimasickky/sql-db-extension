"""sql-db v1.0.0 · SQL Database assistant with schema browsing + query execution."""
from __future__ import annotations

import sys, os
_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _dir)
for _m in [k for k in sys.modules if k in (
    "app", "handlers_connections", "handlers_query", "handlers_execute",
    "handlers_nlq", "handlers_history", "skeleton", "panels", "panels_editor",
)]:
    del sys.modules[_m]

from app import ext, chat  # noqa: F401

import handlers_connections    # noqa: F401
import handlers_query          # noqa: F401
import handlers_execute        # noqa: F401
import handlers_nlq            # noqa: F401
import handlers_history        # noqa: F401
import skeleton                # noqa: F401
import panels                  # noqa: F401
import panels_editor           # noqa: F401  # center overlay SQL editor
