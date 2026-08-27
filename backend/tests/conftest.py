import os
import tempfile

import pytest

# Use a temp SQLite DB for the whole test session before importing app modules.
_TMPDIR = tempfile.mkdtemp(prefix="eaglex-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR}/test.db"
os.environ["ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["REDIS_URL"] = "redis://localhost:6379/9"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c