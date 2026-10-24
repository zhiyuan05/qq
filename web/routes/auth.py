from __future__ import annotations

"""Web 面板认证。"""
import hashlib
import hmac
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from web.schemas import OkResponse

auth_page_router = APIRouter()
auth_api_router = APIRouter(tags=["auth"])

SESSION_MAX_AGE = 86400
_LOGIN_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "login.html"


class LoginRequest(BaseModel):
    token: str = Field(max_length=4096)


def _get_auth_token() -> str:
    from web.app import get_engine

    engine = get_engine()
    return engine.config.web.auth_token if engine else ""


def _make_session(auth_token: str, issued_at: int | None = None) -> str:
    timestamp = str(issued_at or int(time.time()))
    signature = hmac.new(auth_token.encode(), timestamp.encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}.{signature}"


def _valid_session(session: str, auth_token: str) -> bool:
    try:
        timestamp_text, signature = session.split(".", 1)
        timestamp = int(timestamp_text)
    except (TypeError, ValueError):
        return False
    if timestamp > time.time() + 60 or time.time() - timestamp > SESSION_MAX_AGE:
        return False
    expected = _make_session(auth_token, timestamp).split(".", 1)[1]
    return secrets.compare_digest(signature, expected)


def check_auth(request: Request) -> bool:
    auth_token = _get_auth_token()
    if not auth_token:
        return True
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() == "bearer" and secrets.compare_digest(credential, auth_token):
        return True
    return _valid_session(request.cookies.get("session", ""), auth_token)


def require_auth(request: Request) -> bool:
    """检查是否需要认证。未配置 token 时放行。"""

    return check_auth(request)


def check_websocket_auth(websocket: WebSocket) -> bool:
    auth_token = _get_auth_token()
    if not auth_token:
        return True
    authorization = websocket.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() == "bearer" and secrets.compare_digest(credential, auth_token):
        return True
    return _valid_session(websocket.cookies.get("session", ""), auth_token)


@auth_page_router.get("/login")
async def login_page():
    return FileResponse(_LOGIN_TEMPLATE, media_type="text/html")


@auth_api_router.post("/auth/login", response_model=OkResponse)
async def api_login(data: LoginRequest, request: Request):
    from web.app import get_engine

    engine = get_engine()
    if not engine:
        return JSONResponse({"error": "引擎未就绪"}, status_code=503)
    auth_token = engine.config.web.auth_token
    if not auth_token:
        return JSONResponse({"ok": True})
    if secrets.compare_digest(data.token, auth_token):
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "session",
            _make_session(auth_token),
            httponly=True,
            max_age=SESSION_MAX_AGE,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return response
    return JSONResponse({"error": "令牌错误"}, status_code=403)


@auth_api_router.post("/auth/logout", response_model=OkResponse)
async def api_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("session", path="/", samesite="strict")
    return response


@auth_api_router.get("/auth/check", response_model=OkResponse)
async def api_auth_check(request: Request):
    return {"ok": check_auth(request)}
