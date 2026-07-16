"""Web 管理面板 - 移动端优先"""
import time

from fastapi import FastAPI

app = FastAPI(title="聊天机器人管理面板", version="2.0.0")

_engine = None
_start_time = time.time()


def set_engine(engine):
    global _engine
    _engine = engine


# 挂载路由（import 必须放在 app 定义之后，避免循环导入）
from web.routes import api_router, page_router, auth_router, ws_router, chat_router  # noqa: E402
app.include_router(api_router)
app.include_router(page_router)
app.include_router(auth_router)
app.include_router(ws_router)
app.include_router(chat_router)

# API v1 版本化 - 用简单的方式注册 v1 路由
# 现有的 /api/* 路由保持不变，v1 路由添加 prefix
from fastapi import APIRouter as APIRouterAlias
_api_v1 = APIRouterAlias(prefix="/api/v1")

# 重新注册主要 API 端点
@_api_v1.get("/health")
async def _v1_health():
    from web.routes.api import api_health
    return await api_health()

@_api_v1.get("/stats")
async def _v1_stats():
    from web.routes.api import api_stats
    return await api_stats()

@_api_v1.get("/cards")
async def _v1_cards():
    from web.routes.api import api_cards
    return await api_cards()

@_api_v1.get("/styles")
async def _v1_styles():
    from web.routes.api import api_styles
    return await api_styles()

app.include_router(_api_v1)
logger = __import__("logging").getLogger("web")
logger.info("API v1 路由已挂载: /api/v1/*")

