import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models import Setting


def make_authed_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    hashed = bcrypt.hashpw(b"testpass123", bcrypt.gensalt()).decode()
    db.add(Setting(key="admin_password_hash", value=hashed))
    db.commit()
    db.close()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app, raise_server_exceptions=True)


def test_login_page_loads(client):
    response = client.get("/admin/login")
    assert response.status_code == 200
    assert "password" in response.text.lower()


def test_login_redirects_to_setup_when_no_password(client):
    response = client.get("/admin/setup", follow_redirects=False)
    # No password set in test fixture — setup page should show
    assert response.status_code in (200, 302)


def test_login_with_wrong_password():
    import re
    c = make_authed_client()
    get_resp = c.get("/admin/login")
    match = re.search(r'name="_csrf" value="([^"]+)"', get_resp.text)
    csrf = match.group(1) if match else ""
    response = c.post("/admin/login", data={"password": "wrongpass", "_csrf": csrf}, follow_redirects=False)
    assert response.status_code in (200, 401)
    app.dependency_overrides.clear()


def test_login_with_correct_password_redirects():
    import re
    c = make_authed_client()
    get_resp = c.get("/admin/login")
    match = re.search(r'name="_csrf" value="([^"]+)"', get_resp.text)
    csrf = match.group(1) if match else ""
    response = c.post("/admin/login", data={"password": "testpass123", "_csrf": csrf}, follow_redirects=False)
    assert response.status_code == 302
    assert "/admin" in response.headers["location"]
    app.dependency_overrides.clear()


def test_login_post_rejects_missing_csrf(client):
    resp = client.post("/admin/login", data={"password": "anything"})
    assert resp.status_code == 403


def test_login_post_accepts_valid_csrf(client):
    # GET the page first to establish session and get CSRF token
    get_resp = client.get("/admin/login")
    assert get_resp.status_code == 200
    # Extract CSRF token from the response HTML
    import re
    match = re.search(r'name="_csrf" value="([^"]+)"', get_resp.text)
    assert match, "CSRF token not found in login form"
    csrf_token = match.group(1)
    resp = client.post("/admin/login", data={"password": "wrongpassword", "_csrf": csrf_token})
    # Should reach the handler (password wrong, but not 403)
    assert resp.status_code in (200, 401)


def test_logout_clears_session():
    import re
    c = make_authed_client()
    get_resp = c.get("/admin/login")
    match = re.search(r'name="_csrf" value="([^"]+)"', get_resp.text)
    csrf = match.group(1) if match else ""
    c.post("/admin/login", data={"password": "testpass123", "_csrf": csrf})
    response = c.get("/admin/logout", follow_redirects=False)
    assert response.status_code == 302
    assert "login" in response.headers["location"]
    app.dependency_overrides.clear()
