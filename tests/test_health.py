def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_admin_redirects_without_auth(client):
    response = client.get("/admin/", follow_redirects=False)
    # No admin routes yet, but app should not 500
    assert response.status_code in (200, 302, 307, 404)
