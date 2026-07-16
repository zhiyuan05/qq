"""Web 面板流式对话"""
import json
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from web.app import _engine
from platforms.models import UnifiedMessage, Platform, MessageType

chat_router = APIRouter()

CHAT_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>对话 - 聊天机器人</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#f0f2f5;--card-bg:#fff;--text:#333;--border:#eee}
.dark{--bg:#1a1a2e;--card-bg:#16213e;--text:#e0e0e0;--border:#2a2a4a}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',sans-serif;
  background:var(--bg);color:var(--text);font-size:15px;height:100vh;display:flex;flex-direction:column}
.header{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;
  padding:16px;display:flex;align-items:center;gap:12px}
.header h1{font-size:18px;font-weight:600;flex:1}
.back{color:#fff;text-decoration:none;font-size:14px}
.chat-area{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.msg{max-width:85%;padding:10px 14px;border-radius:12px;line-height:1.5;font-size:14px;animation:fadeIn .2s}
.msg.user{background:#667eea;color:#fff;align-self:flex-end;border-bottom-right-radius:4px}
.msg.ai{background:var(--card-bg);color:var(--text);align-self:flex-start;border-bottom-left-radius:4px;
  box-shadow:0 1px 4px rgba(0,0,0,.06)}
.msg .role{font-size:11px;opacity:.7;margin-bottom:2px}
.msg .content{white-space:pre-wrap;word-break:break-word}
.msg .cursor{display:inline-block;width:2px;height:1em;background:#667eea;animation:blink .8s infinite;vertical-align:text-bottom}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.input-area{display:flex;padding:12px;gap:8px;background:var(--card-bg);border-top:1px solid var(--border)}
.input-area input{flex:1;padding:10px 14px;border:1px solid #ddd;border-radius:20px;font-size:14px;
  outline:none;background:var(--bg);color:var(--text)}
.input-area input:focus{border-color:#667eea}
.input-area button{padding:10px 20px;background:linear-gradient(135deg,#667eea,#764ba2);
  color:#fff;border:none;border-radius:20px;font-size:14px;cursor:pointer}
.input-area button:disabled{opacity:.5;cursor:not-allowed}
.toolbar{display:flex;gap:8px;padding:8px 12px;background:var(--card-bg);border-bottom:1px solid var(--border);font-size:13px}
.toolbar select{padding:4px 8px;border:1px solid #ddd;border-radius:6px;font-size:13px;background:var(--bg);color:var(--text)}
.dark-toggle{position:fixed;top:16px;right:16px;z-index:20;background:rgba(255,255,255,.2);
  border:none;color:#fff;padding:4px 8px;border-radius:6px;cursor:pointer;font-size:14px}
</style></head><body>
<div class="header">
  <a class="back" href="/">&larr; 返回</a>
  <h1>💬 对话</h1>
  <button class="dark-toggle" onclick="toggleDark()" id="darkBtn">🌙</button>
</div>
<div class="toolbar">
  <select id="cardSelect"><option value="">角色卡...</option></select>
  <select id="styleSelect"><option value="">风格...</option></select>
</div>
<div class="chat-area" id="chat"></div>
<div class="input-area">
  <input id="input" placeholder="输入消息..." autofocus
    onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}">
  <button id="sendBtn" onclick="send()">发送</button>
</div>
<script>
function toggleDark(){document.documentElement.classList.toggle('dark');
  localStorage.setItem('dark',document.documentElement.classList.contains('dark'));
  document.getElementById('darkBtn').textContent=document.documentElement.classList.contains('dark')?'☀️':'🌙';}
if(localStorage.getItem('dark')==='true'){document.documentElement.classList.add('dark');}

// 加载角色卡和风格列表
fetch('/api/cards').then(r=>r.json()).then(cards=>{
  const sel=document.getElementById('cardSelect');
  cards.forEach(c=>{const o=document.createElement('option');o.value=c;o.text=c;sel.appendChild(o)});
});
fetch('/api/styles').then(r=>r.json()).then(styles=>{
  const sel=document.getElementById('styleSelect');
  styles.forEach(s=>{const o=document.createElement('option');o.value=s;o.text=s;sel.appendChild(o)});
});

let chatHistory=[];
function addMsg(role,content,isStreaming=false){
  const chat=document.getElementById('chat');
  const div=document.createElement('div');
  div.className='msg '+role;
  div.innerHTML='<div class="role">'+(role==='user'?'👤 我':'🤖 AI')+'</div><div class="content">'+content+'</div>';
  if(isStreaming)div.id='streaming';
  chat.appendChild(div);
  chat.scrollTop=chat.scrollHeight;
}

async function send(){
  const input=document.getElementById('input');
  const text=input.value.trim();
  if(!text)return;
  input.value='';document.getElementById('sendBtn').disabled=true;
  addMsg('user',text);
  chatHistory.push({role:'user',content:text});

  // 先添加一个空的AI消息用于流式显示
  const chat=document.getElementById('chat');
  const aiDiv=document.createElement('div');
  aiDiv.className='msg ai';aiDiv.id='streaming';
  aiDiv.innerHTML='<div class="role">🤖 AI</div><div class="content"><span class="cursor">|</span></div>';
  chat.appendChild(aiDiv);

  try{
    const card=document.getElementById('cardSelect').value;
    const style=document.getElementById('styleSelect').value;
    const resp=await fetch('/api/chat',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text,card,style})
    });
    const data=await resp.json();
    if(data.response){
      aiDiv.innerHTML='<div class="role">🤖 AI</div><div class="content">'+data.response+'</div>';
      chatHistory.push({role:'assistant',content:data.response});
    }else{
      aiDiv.innerHTML='<div class="role">🤖 AI</div><div class="content" style="color:#e53e3e">'+(data.error||'无回复')+'</div>';
    }
  }catch(e){
    document.getElementById('streaming').innerHTML='<div class="role">🤖 AI</div><div class="content" style="color:#e53e3e">请求失败</div>';
  }
  document.getElementById('sendBtn').disabled=false;
  chat.scrollTop=chat.scrollHeight;
}
</script></body></html>"""


@chat_router.get("/chat", response_class=HTMLResponse)
async def chat_page():
    return CHAT_PAGE_HTML


@chat_router.post("/api/chat")
async def api_chat(data: dict, request: Request):
    if not _engine:
        return {"error": "引擎未就绪"}
    message = data.get("message", "").strip()
    if not message:
        return {"error": "消息不能为空"}

    # 获取用户真实IP作为user_id
    user_id = request.client.host if request.client else "web_user"
    card = data.get("card") or ""
    style = data.get("style") or ""

    msg = UnifiedMessage(
        platform=Platform.CONSOLE,
        user_id=f"web_{user_id}",
        content=message,
        message_type=MessageType.TEXT,
    )

    try:
        # 如果指定了角色卡或风格，先切换会话配置
        session = _engine.session_manager.get_or_create(
            msg,
            default_character=_engine.config.bot.default_character,
            default_style=_engine.config.bot.default_style,
        )
        if card and _engine.character_manager.get(card):
            session.character_card = card
        if style and _engine.style_engine.get(style):
            session.style = style

        response = await _engine.process_message(msg)
        return {"response": response or ""}
    except Exception as e:
        return {"error": str(e)}
