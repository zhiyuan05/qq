"""Web routes - mounted by app.py"""
from web.routes.api import api_router
from web.routes.pages import page_router
from web.routes.auth import auth_router
from web.routes.ws import ws_router
from web.routes.chat import chat_router
