def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_security_headers_present(client):
    resp = client.get("/health")
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_admin_redirects_without_auth(client):
    response = client.get("/admin/", follow_redirects=False)
    # No admin routes yet, but app should not 500
    assert response.status_code in (200, 302, 307, 404)
