"""Shared Jinja2 environment for all routers.

Previously each router built its own Jinja2Templates instance and registered
globals/filters independently; this is the single configured instance.
"""
from fastapi.templating import Jinja2Templates

from app.dependencies import get_csrf_token

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["enumerate"] = enumerate
templates.env.globals["csrf_token"] = get_csrf_token
