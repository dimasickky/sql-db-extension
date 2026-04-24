"""sql-db v2.0.0 · MySQL / MariaDB assistant.

SDK v2.0.0 / Webbee Single Voice — class-based tool surface, no ChatExtension,
no per-extension system prompt. Webbee Narrator composes all user-facing
prose kernel-side from the typed output schemas in ``schemas.py``.
"""
from __future__ import annotations

import os
import sys

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

for _m in [
    k for k in list(sys.modules)
    if k in (
        "app", "tools", "schemas",
        "schema_guard", "sql_parser",
        "skeleton",
        "panels",
        "panels_editor", "panels_editor_results", "panels_editor_tabs",
        "panels_editor_row_form",
        "_editor_result_renderers", "_row_form_inputs", "_row_form_submit",
    )
]:
    del sys.modules[_m]

from app import ext  # noqa: F401,E402  (loader discovers this)

import skeleton       # noqa: F401,E402
import panels         # noqa: F401,E402
import panels_editor  # noqa: F401,E402
