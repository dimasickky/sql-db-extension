"""Tests for sql-db batch execution and row deletion."""
from unittest.mock import AsyncMock, patch
import pytest
from imperal_sdk.testing import MockContext

import handlers_execute
import handlers_rows
from handlers_execute import ExecuteBatchParams
from handlers_rows import DeleteRowsParams

_CONN_DICT = {
    "id": "conn-1",
    "host": "db.test",
    "db_user": "tester",
    "password_encrypted": "encrypted",
    "database": "test_db",
}


@pytest.mark.asyncio
async def test_execute_batch_rejects_empty():
    ctx = MockContext(user_id="user-1")
    params = ExecuteBatchParams(sql="   ")
    res = await handlers_execute.fn_execute_batch(ctx, params)
    assert res.status == "error"
    assert "empty" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_execute_batch_success():
    ctx = MockContext(user_id="user-1")
    params = ExecuteBatchParams(sql="CREATE TABLE t1(id int); INSERT INTO t1 VALUES(1);")

    with patch.object(handlers_execute, "_resolve", new=AsyncMock(return_value=(_CONN_DICT, "conn-1"))):
        with patch.object(handlers_execute, "_api_post", new=AsyncMock(return_value={
            "status": "ok",
            "statements_executed": 2,
            "rows_affected": 1,
            "statements": [
                {"query": "CREATE TABLE t1(id int)", "rows_affected": 0, "status": "ok", "query_type": "CREATE"},
                {"query": "INSERT INTO t1 VALUES(1)", "rows_affected": 1, "status": "ok", "query_type": "INSERT"},
            ],
        })):
            with patch.object(handlers_execute, "invalidate_schema_cache", new=AsyncMock()):
                res = await handlers_execute.fn_execute_batch(ctx, params)
                assert res.status == "success"
                assert res.data["statements_executed"] == 2
                assert res.data["rows_affected"] == 1


@pytest.mark.asyncio
async def test_delete_rows_preview_and_confirm():
    ctx = MockContext(user_id="user-1")

    # 1. Preview mode (confirm=False)
    preview_params = DeleteRowsParams(
        table="users",
        pk_col="id",
        pk_values=["10", "11"],
        confirm=False,
    )
    with patch.object(handlers_rows, "load_schema_section", new=AsyncMock(return_value={"tables": {"users": {"columns": ["id", "name"]}}})):
        with patch.object(handlers_rows, "validate_table_exists", return_value=None):
            with patch.object(handlers_rows, "validate_columns", return_value=None):
                with patch.object(handlers_rows, "_resolve", new=AsyncMock(return_value=(_CONN_DICT, "conn-1"))):
                    res_preview = await handlers_rows.fn_delete_rows(ctx, preview_params)
                    assert res_preview.status == "success"
                    assert res_preview.data["needs_confirmation"] is True
                    assert len(res_preview.data["preview"]) == 2

    # 2. Confirm mode (confirm=True)
    confirm_params = DeleteRowsParams(
        table="users",
        pk_col="id",
        pk_values=["10", "11"],
        confirm=True,
    )
    with patch.object(handlers_rows, "load_schema_section", new=AsyncMock(return_value={"tables": {"users": {"columns": ["id", "name"]}}})):
        with patch.object(handlers_rows, "validate_table_exists", return_value=None):
            with patch.object(handlers_rows, "validate_columns", return_value=None):
                with patch.object(handlers_rows, "_resolve", new=AsyncMock(return_value=(_CONN_DICT, "conn-1"))):
                    with patch.object(handlers_rows, "_api_post", new=AsyncMock(return_value={"status": "ok", "rows_affected": 1})):
                        with patch.object(handlers_rows, "_bump_sidebar_for_dml", new=AsyncMock()):
                            res_confirm = await handlers_rows.fn_delete_rows(ctx, confirm_params)
                            assert res_confirm.status == "success"
                            assert res_confirm.data["needs_confirmation"] is False
                            assert res_confirm.data["deleted_count"] == 2
                            assert res_confirm.data["failed_count"] == 0
