"""Web 边界的纯 ASGI 中间件。

不使用 ``BaseHTTPMiddleware``，避免给流式响应额外套入任务组和取消域。
"""
from __future__ import annotations

import re
import time
import uuid
from urllib.parse import quote

from fastapi.responses import JSONResponse, RedirectResponse
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from utils.logger import reset_request_id, set_request_id

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self'; connect-src 'self' ws: wss:; "
        "img-src 'self' data:; frame-ancestors 'none'"
    ),
}


def _request_id(scope: Scope) -> str:
    request = Request(scope)
    candidate = request.headers.get("x-request-id", "")
    return candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else uuid.uuid4().hex[:16]


class SecurityAndAuthMiddleware:
    """认证、请求追踪、安全响应头和 HTTP 指标的一体化纯 ASGI 层。"""

    public_paths = {
        "/login",
        "/api/auth/login",
        "/api/auth/check",
        "/api/v1/auth/login",
        "/api/v1/auth/check",
        "/api/health",
        "/api/health/ready",
        "/api/v1/health",
        "/api/v1/health/ready",
    }
    public_prefixes = ("/static/",)

    def __init__(self, app: ASGIApp, *, fastapi_app) -> None:
        self.app = app
        self.fastapi_app = fastapi_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        context_token = set_request_id(request_id)
        metrics = self.fastapi_app.state.metrics
        metrics.request_started()
        started = time.perf_counter()
        status_code = 500
        completed = False

        def finish(code: int) -> None:
            nonlocal completed
            if not completed:
                completed = True
                metrics.request_finished(code, time.perf_counter() - started)

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                if "x-request-id" not in headers:
                    headers["X-Request-ID"] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
                if scope.get("path", "").startswith("/static/"):
                    headers.setdefault("Cache-Control", "no-cache")
                else:
                    headers.setdefault("Cache-Control", "no-store, private")
                if scope.get("scheme") == "https":
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finish(status_code)

        try:
            request = Request(scope)
            path = scope.get("path", "")
            is_public = path in self.public_paths or any(
                path.startswith(prefix) for prefix in self.public_prefixes
            )
            if not is_public:
                from web.routes.auth import require_auth

                if not require_auth(request):
                    if scope.get("path", "").startswith("/api/"):
                        response = JSONResponse({"error": "未认证"}, status_code=401)
                    else:
                        query = scope.get("query_string", b"").decode("latin-1")
                        path = scope.get("path", "/")
                        target = quote(path + (f"?{query}" if query else ""), safe="")
                        response = RedirectResponse(f"/login?next={target}", status_code=303)
                    await response(scope, receive, send_with_headers)
                else:
                    await self.app(scope, receive, send_with_headers)
            else:
                await self.app(scope, receive, send_with_headers)

            finish(status_code)
        except BaseException:
            # 响应头发出后无法再改变 HTTP 状态；指标记录实际已发送的状态。
            finish(status_code)
            raise
        finally:
            reset_request_id(context_token)
