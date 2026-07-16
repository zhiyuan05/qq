"""Web 面板认证"""
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

auth_router = APIRouter()

# 内存会话存储（重启即失效）
_sessions: dict[str, str] = {}


def check_auth(request: Request) -> bool:
    token = request.cookies.get("session")
    return token in _sessions


def require_auth(request: Request) -> bool:
    """检查是否需要认证。未配置 token 时放行。"""
    from web.app import _engine
    if not _engine:
        return True
    auth_token = getattr(_engine.config.web, "auth_token", "")
    if not auth_token:
        return True  # 未设置 token 时无需认证
    return check_auth(request)


LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 - 聊天机器人</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;
  display:flex;align-items:center;justify-content:center}
.card{background:#fff;border-radius:16px;padding:32px;width:340px;box-shadow:0 8px 32px rgba(0,0,0,.15)}
.card h1{text-align:center;color:#333;font-size:22px;margin-bottom:24px}
.form-group{margin-bottom:16px}
.form-group label{display:block;font-size:13px;color:#666;margin-bottom:4px}
.form-group input{width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:8px;
  font-size:15px;outline:none;transition:border-color .2s}
.form-group input:focus{border-color:#667eea}
.btn{width:100%;padding:12px;background:linear-gradient(135deg,#667eea,#764ba2);
  color:#fff;border:none;border-radius:8px;font-size:15px;cursor:pointer}
.btn:hover{opacity:.9}
.error{color:#e53e3e;font-size:13px;text-align:center;margin-top:8px;display:none}
</style></head><body>
<div class="card">
  <h1>🔐 管理面板</h1>
  <form id="loginForm">
    <div class="form-group">
      <label>访问令牌</label>
      <input type="password" id="token" placeholder="输入配置中的 auth_token" autofocus>
    </div>
    <button type="submit" class="btn">登录</button>
    <div class="error" id="error">令牌错误</div>
  </form>
</div>
<script>
document.getElementById('loginForm').onsubmit = async function(e) {
  e.preventDefault();
  const token = document.getElementById('token').value;
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({token})
  });
  if (resp.ok) {
    const target = new URLSearchParams(location.search).get('next') || '/';
    location.href = target;
  } else {
    document.getElementById('error').style.display = 'block';
  }
};
</script></body></html>"""


@auth_router.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_HTML


@auth_router.post("/api/auth/login")
async def api_login(data: dict):
    token = data.get("token", "")
    from web.app import _engine
    if not _engine:
        return JSONResponse({"error": "引擎未就绪"}, status_code=503)
    auth_token = getattr(_engine.config.web, "auth_token", "")
    if not auth_token:
        resp = JSONResponse({"ok": True})
        return resp
    if token == auth_token:
        session_token = secrets.token_hex(16)
        _sessions[session_token] = "admin"
        resp = JSONResponse({"ok": True})
        resp.set_cookie("session", session_token, httponly=True, max_age=86400)
        return resp
    return JSONResponse({"error": "令牌错误"}, status_code=403)


@auth_router.post("/api/auth/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


@auth_router.get("/api/auth/check")
async def api_auth_check(request: Request):
    return {"ok": check_auth(request)}
