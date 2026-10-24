"""Web routes - mounted by app.py"""
from web.routes.api import api_router as api_router
from web.routes.auth import auth_api_router as auth_api_router
from web.routes.auth import auth_page_router as auth_page_router
from web.routes.chat import chat_api_router as chat_api_router
from web.routes.chat import chat_page_router as chat_page_router
from web.routes.pages import page_router as page_router
from web.routes.ws import ws_router as ws_router

__all__ = [
    "api_router",
    "auth_api_router",
    "auth_page_router",
    "chat_api_router",
    "chat_page_router",
    "page_router",
    "ws_router",
]
