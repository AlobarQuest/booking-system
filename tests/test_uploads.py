# tests/test_uploads.py
import os
from app.config import get_settings


def test_upload_missing_file_returns_404(client):
    resp = client.get("/uploads/nonexistent.jpg")
    assert resp.status_code == 404


def test_upload_serves_existing_file(client):
    upload_dir = get_settings().upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, "test.jpg")
    with open(filepath, "wb") as f:
        f.write(b"fake image data")
    try:
        resp = client.get("/uploads/test.jpg")
        assert resp.status_code == 200
        assert resp.content == b"fake image data"
    finally:
        os.remove(filepath)


def test_upload_path_traversal_rejected(client):
    # A filename containing ".." should be blocked by the realpath containment check
    resp = client.get("/uploads/..%2Fapp%2Fconfig.py")
    assert resp.status_code in (400, 404)
