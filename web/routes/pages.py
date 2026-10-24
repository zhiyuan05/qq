"""管理端页面路由。

页面结构与行为由静态资源承载；路由层只负责稳定 URL 和文件响应，避免把
HTML/CSS/JavaScript 混入 Python 业务模块。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

page_router = APIRouter()
_ADMIN_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "admin.html"


@page_router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


async def _admin_page() -> FileResponse:
    return FileResponse(_ADMIN_TEMPLATE, media_type="text/html")


page_router.add_api_route("/", _admin_page, methods=["GET"], name="index")
page_router.add_api_route("/sessions", _admin_page, methods=["GET"], name="sessions_page")
page_router.add_api_route(
    "/sessions/{session_id}",
    _admin_page,
    methods=["GET"],
    name="session_detail_page",
)
page_router.add_api_route("/cards", _admin_page, methods=["GET"], name="cards_page")
page_router.add_api_route("/cards/edit", _admin_page, methods=["GET"], name="card_edit_page")
page_router.add_api_route(
    "/cards/{card_name}",
    _admin_page,
    methods=["GET"],
    name="card_detail_page",
)
page_router.add_api_route("/settings", _admin_page, methods=["GET"], name="settings_page")
page_router.add_api_route("/logs", _admin_page, methods=["GET"], name="logs_page")
page_router.add_api_route("/persona", _admin_page, methods=["GET"], name="persona_page")
page_router.add_api_route("/stickers", _admin_page, methods=["GET"], name="stickers_page")
page_router.add_api_route("/usage", _admin_page, methods=["GET"], name="usage_page")
