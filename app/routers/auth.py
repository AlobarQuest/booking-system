from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings

router = APIRouter()
_settings = get_settings()

oauth = OAuth()
oauth.register(
    name="authentik",
    server_metadata_url=_settings.alobar_id_issuer + ".well-known/openid-configuration",
    client_id=_settings.alobar_id_client_id,
    client_secret=_settings.alobar_id_client_secret,
    client_kwargs={"scope": "openid profile email", "timeout": 30},
)


@router.get("/login")
async def login(request: Request):
    redirect_uri = str(request.url_for("auth_callback"))
    return await oauth.authentik.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    token = await oauth.authentik.authorize_access_token(request)
    user_info = token.get("userinfo") or await oauth.authentik.userinfo(token=token)
    request.session["user_sub"] = user_info["sub"]
    return RedirectResponse("/admin/", status_code=302)


@router.get("/admin/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(
        _settings.alobar_id_issuer + "end-session/",
        status_code=302,
    )
