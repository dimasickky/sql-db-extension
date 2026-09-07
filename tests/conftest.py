"""Shared pytest fixtures for sql-db tests."""
import os

os.environ.setdefault("DB_SERVICE_URL", "http://localhost:3306")

import imperal_sdk.testing as _testing_mod
from imperal_sdk.testing import MockContext as _RealMockContext, MockSecretStore
from cryptography.fernet import Fernet


_TEST_KEY = Fernet.generate_key().decode()


def _mock_context_with_secrets(*args, **kwargs):
    ctx = _RealMockContext(*args, **kwargs)
    ctx.secrets = MockSecretStore({
        "sql_db_api_key": "test-sql-key",
        "sql_db_encryption_key": _TEST_KEY,
    })
    return ctx


_testing_mod.MockContext = _mock_context_with_secrets
