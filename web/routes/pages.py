"""Page routes for web管理面板"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from web.app import _engine

page_router = APIRouter()

# ── Mobile-first CSS (inline for zero dependency) ─────────────
MOBILE_CSS = """\
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif;
  background:#f0f2f5;color:#333;font-size:15px;-webkit-text-size-adjust:100%}
.header{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;
  padding:20px 16px 16px;position:sticky;top:0;z-index:10}
.header h1{font-size:20px;font-weight:600}
.header .subtitle{font-size:12px;opacity:.8;margin-top:2px}
.container{padding:12px;max-width:600px;margin:0 auto}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}
.stat-card{background:#fff;border-radius:12px;padding:16px;text-align:center;
  box-shadow:0 1px 4px rgba(0,0,0,.06)}
.stat-card .num{font-size:28px;font-weight:700;color:#667eea}
.stat-card .label{font-size:12px;color:#999;margin-top:4px}
.section{background:#fff;border-radius:12px;margin-bottom:12px;
  box-shadow:0 1px 4px rgba(0,0,0,.06);overflow:hidden}
.section-title{font-size:14px;font-weight:600;color:#666;padding:14px 16px 8px;
  display:flex;align-items:center;gap:6px}
.item{display:flex;align-items:center;padding:12px 16px;border-bottom:1px solid #f5f5f5;
  cursor:pointer;transition:background .15s}
.item:last-child{border-bottom:none}
.item:active{background:#f0f2f5}
.item .icon{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;
  justify-content:center;font-size:18px;margin-right:12px;flex-shrink:0}
.item .info{flex:1;min-width:0}
.item .name{font-weight:500;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item .desc{font-size:12px;color:#999;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item .badge{background:#e0e7ff;color:#667eea;padding:2px 8px;border-radius:10px;font-size:11px;flex-shrink:0}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;background:#e0e7ff;color:#4f46e5}
.tag-green{background:#d1fae5;color:#059669}
.tag-orange{background:#fef3c7;color:#d97706}
.msg{padding:8px 16px;border-bottom:1px solid #f8f8f8}
.msg-user{background:#f0f7ff}
.msg-assistant{background:#fff}
.msg .role{font-size:11px;color:#999;margin-bottom:2px}
.msg .content{font-size:14px;line-height:1.5;word-break:break-all}
.back-btn{display:inline-block;padding:6px 12px;color:#667eea;font-size:14px;
  text-decoration:none;cursor:pointer}
.empty{text-align:center;padding:40px;color:#ccc;font-size:14px}
.nav{display:flex;background:#fff;border-top:1px solid #eee;position:fixed;
  bottom:0;left:0;right:0;z-index:10;padding:6px 0 env(safe-area-inset-bottom,6px)}
.nav a{flex:1;text-align:center;padding:8px 0;color:#999;text-decoration:none;font-size:11px}
.nav a.active{color:#667eea}
.nav a .nav-icon{font-size:20px;display:block}
"""

# ── HTML templates ───────────────────────────────────────────

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>聊天机器人</title>
<style>""" + MOBILE_CSS + """
</style></head><body>
<div class="header">
  <h1>🤖 聊天机器人</h1>
  <div class="subtitle" id="uptime">运行中...</div>
</div>
<div class="container">
  <div class="stats" id="stats"></div>
  <div class="section">
    <div class="section-title">💬 最近会话</div>
    <div id="sessions"></div>
  </div>
  <div class="section">
    <div class="section-title">🎭 角色卡</div>
    <div id="cards"></div>
  </div>
</div>
<div class="nav">
  <a href="/" class="active"><span class="nav-icon">🏠</span>首页</a>
  <a href="/sessions"><span class="nav-icon">💬</span>会话</a>
  <a href="/cards"><span class="nav-icon">🎭</span>角色</a>
  <a href="/settings"><span class="nav-icon">⚙️</span>设置</a>
</div>
<script>
async function load(){
  try{
    const [health,stats,sessions,cards] = await Promise.all([
      fetch('/api/health').then(r=>r.json()),
      fetch('/api/stats').then(r=>r.json()),
      fetch('/api/sessions').then(r=>r.json()),
      fetch('/api/cards').then(r=>r.json())
    ]);
    const h = Math.floor(health.uptime_seconds/3600);
    const m = Math.floor((health.uptime_seconds%3600)/60);
    document.getElementById('uptime').textContent = `运行 ${h}h ${m}m | 状态: ${health.status}`;
    document.getElementById('stats').innerHTML = `
      <div class="stat-card"><div class="num">${stats.session_count}</div><div class="label">活跃会话</div></div>
      <div class="stat-card"><div class="num">${stats.entity_count}</div><div class="label">记忆实体</div></div>
      <div class="stat-card"><div class="num">${stats.card_count}</div><div class="label">角色卡</div></div>
      <div class="stat-card"><div class="num">${stats.style_count}</div><div class="label">风格</div></div>
    `;
    const platformIcons = {wechat:'💚',qq:'🐧',console:'💻'};
    document.getElementById('sessions').innerHTML = sessions.length ?
      sessions.slice(0,8).map(s=>`
        <div class="item" onclick="location.href='/sessions/${s.id}'">
          <div class="icon">${platformIcons[s.platform]||'📱'}</div>
          <div class="info">
            <div class="name">${s.id}</div>
            <div class="desc">${s.last_message||'暂无消息'}</div>
          </div>
          <span class="badge">${s.message_count}条</span>
        </div>
      `).join('') : '<div class="empty">暂无会话</div>';
    document.getElementById('cards').innerHTML = cards.map(c=>`
      <div class="item" onclick="location.href='/cards/${c}'">
        <div class="icon" style="background:#f0f7ff">🎭</div>
        <div class="info"><div class="name">${c}</div></div>
      </div>
    `).join('');
  }catch(e){
    document.getElementById('stats').innerHTML = '<div class="empty">加载失败</div>';
  }
}
load();
setInterval(load, 30000);
</script></body></html>"""

SESSIONS_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>会话列表</title>
<style>""" + MOBILE_CSS + """
</style></head><body>
<div class="header">
  <h1>💬 会话管理</h1>
</div>
<div class="container">
  <div class="section">
    <div id="list"></div>
  </div>
</div>
<div class="nav">
  <a href="/"><span class="nav-icon">🏠</span>首页</a>
  <a href="/sessions" class="active"><span class="nav-icon">💬</span>会话</a>
  <a href="/cards"><span class="nav-icon">🎭</span>角色</a>
  <a href="/settings"><span class="nav-icon">⚙️</span>设置</a>
</div>
<script>
async function load(){
  const sessions = await fetch('/api/sessions').then(r=>r.json());
  const icons = {wechat:'💚',qq:'🐧',console:'💻'};
  document.getElementById('list').innerHTML = sessions.length ?
    sessions.map(s=>`
      <div class="item" onclick="location.href='/sessions/${s.id}'">
        <div class="icon">${icons[s.platform]||'📱'}</div>
        <div class="info">
          <div class="name">${s.id}</div>
          <div class="desc">${s.character_card} · ${s.style} · ${s.message_count}条</div>
        </div>
      </div>
    `).join('') : '<div class="empty">暂无会话</div>';
}
load();
</script></body></html>"""

CARDS_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>角色卡</title>
<style>""" + MOBILE_CSS + """
</style></head><body>
<div class="header">
  <h1>🎭 角色卡管理</h1>
</div>
<div class="container">
  <div class="section" id="list"></div>
</div>
<div class="nav">
  <a href="/"><span class="nav-icon">🏠</span>首页</a>
  <a href="/sessions"><span class="nav-icon">💬</span>会话</a>
  <a href="/cards" class="active"><span class="nav-icon">🎭</span>角色</a>
  <a href="/settings"><span class="nav-icon">⚙️</span>设置</a>
</div>
<script>
async function load(){
  const cards = await fetch('/api/cards').then(r=>r.json());
  document.getElementById('list').innerHTML = cards.map(c=>`
    <div class="item" onclick="location.href='/cards/'+c">
      <div class="icon" style="background:#f0f7ff">🎭</div>
      <div class="info"><div class="name">${c}</div></div>
    </div>
  `).join('');
}
load();
</script></body></html>"""

SETTINGS_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>设置</title>
<style>""" + MOBILE_CSS + """
.setting-item{padding:14px 16px;border-bottom:1px solid #f5f5f5;display:flex;justify-content:space-between;align-items:center}
.setting-item .label{font-size:14px}
.setting-item .value{font-size:14px;color:#667eea}
</style></head><body>
<div class="header">
  <h1>⚙️ 设置</h1>
</div>
<div class="container">
  <div class="section" id="info"></div>
</div>
<div class="nav">
  <a href="/"><span class="nav-icon">🏠</span>首页</a>
  <a href="/sessions"><span class="nav-icon">💬</span>会话</a>
  <a href="/cards"><span class="nav-icon">🎭</span>角色</a>
  <a href="/settings" class="active"><span class="nav-icon">⚙️</span>设置</a>
</div>
<script>
async function load(){
  const [health,stats] = await Promise.all([
    fetch('/api/health').then(r=>r.json()),
    fetch('/api/stats').then(r=>r.json())
  ]);
  const h = Math.floor(health.uptime_seconds/3600);
  const m = Math.floor((health.uptime_seconds%3600)/60);
  document.getElementById('info').innerHTML = `
    <div class="setting-item"><span class="label">运行状态</span><span class="value" style="color:#059669">正常</span></div>
    <div class="setting-item"><span class="label">运行时间</span><span class="value">${h}h ${m}m</span></div>
    <div class="setting-item"><span class="label">活跃会话</span><span class="value">${stats.session_count}</span></div>
    <div class="setting-item"><span class="label">角色卡数</span><span class="value">${stats.card_count}</span></div>
    <div class="setting-item"><span class="label">风格数</span><span class="value">${stats.style_count}</span></div>
    <div class="setting-item"><span class="label">记忆实体</span><span class="value">${stats.entity_count}</span></div>
  `;
}
load();
</script></body></html>"""


# ── Dynamic page helpers ──────────────────────────────────────

def _session_detail_html(session_id: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>会话详情</title>
<style>{MOBILE_CSS}
.chat{{padding:8px 0}}
</style></head><body>
<div class="header">
  <a class="back-btn" href="/sessions">← 返回</a>
  <h1 style="margin-top:8px">{session_id}</h1>
  <div class="subtitle" id="meta"></div>
</div>
<div class="container">
  <div class="section chat" id="messages"></div>
</div>
<script>
async function load(){{
  const d = await fetch('/api/sessions/{session_id}').then(r=>r.json());
  if(d.error){{document.getElementById('messages').innerHTML='<div class="empty">'+d.error+'</div>';return;}}
  document.getElementById('meta').textContent = `角色卡:${{d.character_card}} 风格:${{d.style}} ${{d.summary?'摘要:'+d.summary.slice(0,30):''}}`;
  document.getElementById('messages').innerHTML = d.messages.map(m=>`
    <div class="msg msg-${{m.role}}">
      <div class="role">${{m.role==='user'?'👤 我':'🤖 AI'}}</div>
      <div class="content">${{m.content}}</div>
    </div>
  `).join('');
}}
load();
</script></body></html>"""


def _card_detail_html(card_name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>{card_name}</title>
<style>{MOBILE_CSS}
.detail{{padding:16px;line-height:1.6;font-size:14px}}
.detail h3{{color:#667eea;margin:16px 0 8px;font-size:15px}}
.detail .rules{{background:#f8f9fa;padding:12px;border-radius:8px;margin:8px 0}}
.detail .rules li{{margin:4px 0 4px 16px}}
</style></head><body>
<div class="header">
  <a class="back-btn" href="/cards">← 返回</a>
  <h1 style="margin-top:8px">🎭 {card_name}</h1>
</div>
<div class="container">
  <div class="section">
    <div class="detail" id="detail"></div>
  </div>
</div>
<script>
async function load(){{
  const d = await fetch('/api/cards/{card_name}').then(r=>r.json());
  if(d.error){{document.getElementById('detail').innerHTML=d.error;return;}}
  let html = `<h3>描述</h3><p>${{d.description||'无'}}</p>`;
  if(d.rules && d.rules.length){{
    html += '<h3>规则</h3><ul class="rules">';
    d.rules.forEach(r=>{{html+=`<li>${{r}}</li>`}});
    html += '</ul>';
  }}
  html += `<h3>系统提示词(预览)</h3><p style="color:#666;font-size:13px">${{d.system_prompt||'无'}}</p>`;
  document.getElementById('detail').innerHTML = html;
}}
load();
</script></body></html>"""


# ── Routes ────────────────────────────────────────────────────

@page_router.get("/", response_class=HTMLResponse)
async def index():
    return INDEX_HTML


@page_router.get("/sessions", response_class=HTMLResponse)
async def sessions_page():
    return SESSIONS_HTML


@page_router.get("/sessions/{session_id}", response_class=HTMLResponse)
async def session_detail_page(session_id: str):
    return _session_detail_html(session_id)


@page_router.get("/cards", response_class=HTMLResponse)
async def cards_page():
    return CARDS_HTML


@page_router.get("/cards/{card_name}", response_class=HTMLResponse)
async def card_detail_page(card_name: str):
    return _card_detail_html(card_name)


@page_router.get("/settings", response_class=HTMLResponse)
async def settings_page():
    return SETTINGS_HTML
