from unittest.mock import MagicMock


def _make_request(session=None):
    req = MagicMock()
    req.session = session or {}
    return req


def test_get_csrf_token_creates_token_on_first_call():
    from app.dependencies import get_csrf_token
    req = _make_request()
    token = get_csrf_token(req)
    assert token
    assert len(token) == 64  # 32 bytes hex
    assert req.session["csrf_token"] == token


def test_get_csrf_token_returns_same_token_on_second_call():
    from app.dependencies import get_csrf_token
    req = _make_request()
    t1 = get_csrf_token(req)
    t2 = get_csrf_token(req)
    assert t1 == t2


def test_validate_csrf_passes_with_valid_token():
    from app.dependencies import get_csrf_token, validate_csrf_token
    req = _make_request()
    token = get_csrf_token(req)
    # should not raise
    validate_csrf_token(req, token)


def test_validate_csrf_raises_403_with_wrong_token():
    from fastapi import HTTPException
    from app.dependencies import get_csrf_token, validate_csrf_token
    req = _make_request()
    get_csrf_token(req)
    try:
        validate_csrf_token(req, "wrong-token")
        assert False, "Should have raised HTTPException"
    except HTTPException as e:
        assert e.status_code == 403


def test_validate_csrf_raises_403_with_empty_token():
    from fastapi import HTTPException
    from app.dependencies import get_csrf_token, validate_csrf_token
    req = _make_request()
    get_csrf_token(req)
    try:
        validate_csrf_token(req, "")
        assert False, "Should have raised HTTPException"
    except HTTPException as e:
        assert e.status_code == 403
