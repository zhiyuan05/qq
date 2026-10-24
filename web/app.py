from __future__ import annotations

"""Web 管理面板 - 移动端优先"""
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from observability import RuntimeMetrics
from utils.version import __version__
from web.middleware import SecurityAndAuthMiddleware


def _operation_id(route: APIRoute) -> str:
    """让生成客户端的函数名不随 URL 前缀变化。"""

    return route.name


app = FastAPI(
    title="聊天机器人管理面板",
    version=__version__,
    generate_unique_id_function=_operation_id,
    docs_url=None,
    redoc_url=None,
)
_start_time = time.time()
app.state.engine = None
app.state.metrics = RuntimeMetrics()
app.add_middleware(SecurityAndAuthMiddleware, fastapi_app=app)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)


def set_engine(engine):
    app.state.engine = engine


def get_engine():
    return app.state.engine


def get_metrics() -> RuntimeMetrics:
    return app.state.metrics


# 挂载路由（import 必须放在 app 定义之后，避免循环导入）
from web.routes import (  # noqa: E402
    api_router,
    auth_api_router,
    auth_page_router,
    chat_api_router,
    chat_page_router,
    page_router,
    ws_router,
)

# 旧 `/api` 保持兼容但不再写入 OpenAPI；所有正式契约统一发布在 `/api/v1`。
for router in (api_router, auth_api_router, chat_api_router):
    app.include_router(router, prefix="/api", include_in_schema=False)
    app.include_router(router, prefix="/api/v1")

app.include_router(page_router, include_in_schema=False)
app.include_router(auth_page_router, include_in_schema=False)
app.include_router(chat_page_router, include_in_schema=False)
app.include_router(ws_router)

