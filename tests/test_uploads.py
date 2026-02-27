# tests/test_uploads.py
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.main import app


@pytest.fixture
def upload_client(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    os.makedirs(tmp_path / "uploads", exist_ok=True)
    from app.config import get_settings
    get_settings.cache_clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, tmp_path
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_upload_missing_file_returns_404(upload_client):
    client, _ = upload_client
    resp = client.get("/uploads/nonexistent.jpg")
    assert resp.status_code == 404


def test_upload_serves_existing_file(upload_client):
    client, tmp_path = upload_client
    upload_dir = tmp_path / "uploads"
    (upload_dir / "test.jpg").write_bytes(b"fake image data")
    resp = client.get("/uploads/test.jpg")
    assert resp.status_code == 200
    assert resp.content == b"fake image data"


def test_upload_path_traversal_rejected(upload_client):
    client, _ = upload_client
    resp = client.get("/uploads/../app/config.py")
    assert resp.status_code in (400, 404)
