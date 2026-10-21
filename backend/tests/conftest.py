"""Test harness: isolated SQLite database, deterministic settings, no model loading."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Must be set before app.core.config is imported anywhere.
_TMP = Path(tempfile.mkdtemp(prefix="scai-tests-"))
os.environ.setdefault("SCAI_ENVIRONMENT", "test")
os.environ["SCAI_DATA_DIR"] = str(_TMP / "data")
os.environ["SCAI_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["SCAI_DETECTOR_BACKEND"] = "none"  # never load real weights in tests
os.environ["SCAI_ALLOW_MODEL_AUTODOWNLOAD"] = "false"
os.environ["SCAI_SECRET_KEY"] = "test-secret-key-not-for-production-use"
os.environ["SCAI_BOOTSTRAP_ADMIN_PASSWORD"] = "AdminPass123"
os.environ["SCAI_BOOTSTRAP_SUPERVISOR_PASSWORD"] = "SuperPass123"
os.environ["SCAI_RATE_LIMIT_ENABLED"] = "false"
os.environ["SCAI_LOG_LEVEL"] = "WARNING"

from app.db.models import Base  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed()
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def app_client(_database):
    from fastapi.testclient import TestClient

    from app.main import create_app

    # TestClient without the context manager skips lifespan: no camera workers,
    # no detector loading. Seed data is created by the fixture above.
    client = TestClient(create_app())
    yield client


def _login(client, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def admin_headers(app_client) -> dict[str, str]:
    return _login(app_client, "admin", "AdminPass123")


@pytest.fixture()
def supervisor_headers(app_client) -> dict[str, str]:
    return _login(app_client, "supervisor", "SuperPass123")


@pytest.fixture()
def reset_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture()
def fake_detector():
    """Detector stub returning preset detections (list of Detection)."""
    from app.vision.detector import Detection

    class StubDetector:
        name = "stub"
        phone_capable = True

        def __init__(self) -> None:
            self.queue: list[list[Detection]] = []
            self.default: list[Detection] = []
            self.calls = 0

        def push(self, detections: list[Detection]) -> None:
            self.queue.append(detections)

        def detect(self, frame):  # noqa: ANN001
            self.calls += 1
            if self.queue:
                return self.queue.pop(0)
            return self.default

        def close(self) -> None:
            pass

    stub = StubDetector()
    yield stub
    # nothing to clean up


def person(x: float, y: float, w: float = 60.0, h: float = 140.0, conf: float = 0.9):
    from app.vision.detector import Detection
    import numpy as np

    return Detection("person", conf, np.array([x, y, x + w, y + h], dtype=np.float32), 0)


def phone(x: float, y: float, w: float = 18.0, h: float = 30.0, conf: float = 0.8):
    from app.vision.detector import Detection
    import numpy as np

    return Detection("cell phone", conf, np.array([x, y, x + w, y + h], dtype=np.float32), 67)
