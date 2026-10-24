'use strict';

const input = document.getElementById('input');
const sendButton = document.getElementById('sendBtn');
const chat = document.getElementById('chat');
const composer = document.getElementById('composer');
const darkButton = document.getElementById('darkBtn');
const statusEl = document.getElementById('status');
const sideStatus = document.getElementById('sideStatus');
const sideCard = document.getElementById('sideCard');
const sideStyle = document.getElementById('sideStyle');
const sideCount = document.getElementById('sideCount');
const historyList = document.getElementById('historyList');
const summaryBlock = document.getElementById('summaryBlock');
const sideSummary = document.getElementById('sideSummary');
const newChatButton = document.getElementById('newChatBtn');

let messageIndex = 0;
let busy = false;

function toggleDark() {
  document.documentElement.classList.toggle('dark');
  const enabled = document.documentElement.classList.contains('dark');
  localStorage.setItem('dark', enabled);
  darkButton.textContent = enabled ? '☀️' : '🌙';
}

const storedTheme = localStorage.getItem('dark');
if (storedTheme === null ? window.matchMedia('(prefers-color-scheme: dark)').matches : storedTheme === 'true') {
  document.documentElement.classList.add('dark');
  darkButton.textContent = '☀️';
}
darkButton.addEventListener('click', toggleDark);

function setStatus(text, state = '') {
  statusEl.textContent = text;
  sideStatus.textContent = text;
  statusEl.dataset.state = state;
}

function autoResize() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
}

async function populateSelect(path, elementId) {
  try {
    const response = await fetch(path);
    if (!response.ok) return;
    const values = await response.json();
    const select = document.getElementById(elementId);
    values.forEach((value) => {
      if (typeof value !== 'string') return;
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  } catch (_) {
    // 保持默认选项；聊天连接仍可独立使用。
  }
}

function setSelectValue(select, value) {
  if (!value) return;
  if (![...select.options].some((option) => option.value === value)) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
  select.value = value;
}

const clientId = localStorage.getItem('chatClientId') ||
  (self.crypto?.randomUUID?.().replaceAll('-', '') || Math.random().toString(36).slice(2));
localStorage.setItem('chatClientId', clientId);
const sessionId = `console:web_${clientId}`;

function emptyState() {
  const wrapper = document.createElement('div');
  wrapper.className = 'chat-empty';
  wrapper.id = 'chatEmpty';
  const mark = document.createElement('span');
  mark.className = 'chat-empty-mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.textContent = 'Q';
  const title = document.createElement('strong');
  title.textContent = '开始一段对话';
  const hint = document.createElement('p');
  hint.textContent = '消息会实时显示在这里';
  wrapper.append(mark, title, hint);
  return wrapper;
}

function showEmpty() {
  chat.replaceChildren(emptyState());
  messageIndex = 0;
  sideCount.textContent = '0';
  renderSideHistory([]);
}

function addMessage(role, content) {
  document.getElementById('chatEmpty')?.remove();
  const kind = role === 'user' ? 'user' : 'ai';
  const wrapper = document.createElement('div');
  wrapper.className = `msg ${kind}`;
  wrapper.id = `msg-${messageIndex}`;
  messageIndex += 1;
  const label = document.createElement('div');
  label.className = 'role';
  label.textContent = kind === 'user' ? '我' : 'AI';
  const body = document.createElement('div');
  body.className = 'content';
  body.textContent = content;
  wrapper.append(label, body);
  chat.appendChild(wrapper);
  chat.scrollTop = chat.scrollHeight;
  return wrapper;
}

function renderSideHistory(messages) {
  historyList.replaceChildren();
  const recent = messages.slice(-8);
  if (!recent.length) {
    const empty = document.createElement('p');
    empty.className = 'side-empty';
    empty.textContent = '暂无消息';
    historyList.append(empty);
    return;
  }
  const offset = messages.length - recent.length;
  recent.forEach((message, index) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'side-history-item';
    const role = document.createElement('span');
    role.className = 'side-history-role';
    role.textContent = message.role === 'user' ? '我' : 'AI';
    const text = document.createElement('span');
    text.className = 'side-history-text';
    text.textContent = (message.content || '').replace(/\s+/g, ' ').slice(0, 40);
    row.append(role, text);
    row.addEventListener('click', () => {
      document.getElementById(`msg-${offset + index}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    historyList.append(row);
  });
}

function updateSide(data) {
  sideCard.textContent = data.character_card || '默认';
  sideStyle.textContent = data.style || 'casual';
  sideCount.textContent = String((data.messages || []).length);
  if (data.summary) {
    sideSummary.textContent = data.summary;
    summaryBlock.hidden = false;
  } else {
    summaryBlock.hidden = true;
  }
  renderSideHistory(data.messages || []);
}

async function loadSession() {
  try {
    const response = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`);
    if (!response.ok) {
      showEmpty();
      return;
    }
    const data = await response.json();
    setSelectValue(document.getElementById('cardSelect'), data.character_card);
    setSelectValue(document.getElementById('styleSelect'), data.style);
    chat.replaceChildren();
    messageIndex = 0;
    const messages = data.messages || [];
    if (!messages.length) {
      chat.append(emptyState());
    } else {
      messages.forEach((message) => addMessage(message.role, message.content));
    }
    updateSide(data);
    setStatus('就绪', 'ok');
  } catch (_) {
    showEmpty();
    setStatus('离线', 'error');
  }
}

async function refreshSide() {
  try {
    const response = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`);
    if (response.ok) updateSide(await response.json());
  } catch (_) {
    // 侧栏信息属于可观测性，不应影响聊天。
  }
}

async function resetSession() {
  if (busy) return;
  setStatus('正在新建…', 'busy');
  try {
    const response = await fetch('/api/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: '/new',
        client_id: clientId,
        request_id: self.crypto?.randomUUID?.().replaceAll('-', '') || Date.now().toString(36),
      }),
    });
    if (!response.ok) throw new Error('reset failed');
    showEmpty();
    summaryBlock.hidden = true;
    setStatus('已开启新对话', 'ok');
  } catch (_) {
    setStatus('新建失败，请重试', 'error');
  }
}

async function sendMessage(textOverride = '') {
  const text = (textOverride || input.value).trim();
  if (!text || busy) return;
  busy = true;
  sendButton.disabled = true;
  input.value = '';
  autoResize();
  const userWrapper = addMessage('user', text);
  const aiWrapper = addMessage('ai', '');
  const content = aiWrapper.querySelector('.content');
  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  content.appendChild(cursor);
  setStatus('正在输入…', 'busy');

  const finish = (message, isError = false) => {
    content.textContent = message || '无回复';
    if (isError) {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'retry-button';
      retry.textContent = '重试';
      retry.addEventListener('click', () => {
        userWrapper.remove();
        aiWrapper.remove();
        busy = false;
        sendButton.disabled = false;
        sendMessage(text);
      });
      aiWrapper.append(retry);
    }
    busy = false;
    sendButton.disabled = false;
    setStatus(isError ? '发送失败' : '就绪', isError ? 'error' : 'ok');
    input.focus();
    refreshSide();
  };

  try {
    const requestId = self.crypto?.randomUUID?.().replaceAll('-', '') ||
      Date.now().toString(36) + Math.random().toString(36).slice(2);
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const payload = {
      message: text,
      card: document.getElementById('cardSelect').value,
      style: document.getElementById('styleSelect').value,
      client_id: clientId,
      request_id: requestId,
    };
    let full = '';
    let completed = false;
    let socket = null;
    let attempts = 0;
    const timeout = setTimeout(() => {
      completed = true;
      if (socket) socket.close(4000, 'timeout');
      finish('请求超时，请稍后重试', true);
    }, 120000);

    const connect = () => {
      if (attempts > 0) {
        full = '';
        content.textContent = '';
        content.appendChild(cursor);
      }
      attempts += 1;
      socket = new WebSocket(`${protocol}//${location.host}/ws/chat`);
      socket.addEventListener('open', () => socket.send(JSON.stringify(payload)));
      socket.addEventListener('message', (event) => {
        const data = JSON.parse(event.data);
        if (data.request_id && data.request_id !== requestId) return;
        if (data.chunk !== undefined) {
          full += data.chunk;
          content.textContent = full;
          content.appendChild(cursor);
          setStatus('正在回复…', 'busy');
        } else if (data.done) {
          completed = true;
          clearTimeout(timeout);
          finish(data.full);
          socket.close();
        } else if (data.error) {
          completed = true;
          clearTimeout(timeout);
          finish(data.error, true);
          socket.close();
        }
        chat.scrollTop = chat.scrollHeight;
      });
      socket.addEventListener('close', () => {
        if (!completed && attempts < 2) {
          setTimeout(connect, 500);
          return;
        }
        if (!completed) {
          completed = true;
          clearTimeout(timeout);
          finish(full || '连接已中断，请重试', true);
        }
      });
    };
    connect();
  } catch (_) {
    finish('请求失败，请稍后重试', true);
  }
}

document.getElementById('cardSelect').addEventListener('change', (event) => {
  sideCard.textContent = event.target.value || '默认';
});
document.getElementById('styleSelect').addEventListener('change', (event) => {
  sideStyle.textContent = event.target.value || 'casual';
});
newChatButton.addEventListener('click', resetSession);
composer.addEventListener('submit', (event) => {
  event.preventDefault();
  sendMessage();
});
input.addEventListener('input', autoResize);
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

async function boot() {
  await Promise.all([
    populateSelect('/api/v1/cards', 'cardSelect'),
    populateSelect('/api/v1/styles', 'styleSelect'),
  ]);
  await loadSession();
}

boot();
