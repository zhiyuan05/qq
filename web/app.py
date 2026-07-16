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
from web.routes import api_router, page_router  # noqa: E402
app.include_router(api_router)
app.include_router(page_router)
