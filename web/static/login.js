'use strict';

(function initTheme() {
  // NapCat's WebUI starts light; only an explicit toggle persists a dark choice.
  document.documentElement.classList.toggle('dark', localStorage.getItem('dark') === 'true');
})();

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('loginForm');
  const input = document.getElementById('token');
  const button = document.getElementById('loginBtn');
  const clear = document.getElementById('clearToken');
  const toast = document.getElementById('error');
  const toastText = toast.querySelector('.toast-text');
  const statusLine = document.getElementById('statusLine');
  const themeToggle = document.getElementById('themeToggle');
  const card = document.getElementById('tiltCard');
  const glow = document.getElementById('tiltGlow');
  let toastTimer = 0;

  function showToast(message) {
    toastText.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 3500);
  }

  function nextUrl() {
    const requested = new URLSearchParams(location.search).get('next') || '/';
    return requested.startsWith('/') && !requested.startsWith('//') ? requested : '/';
  }

  function setLoading(loading) {
    button.classList.toggle('is-loading', loading);
    button.disabled = loading;
    input.disabled = loading;
  }

  themeToggle.addEventListener('click', () => {
    const dark = !document.documentElement.classList.contains('dark');
    localStorage.setItem('dark', String(dark));
    document.documentElement.classList.toggle('dark', dark);
  });

  clear.addEventListener('click', () => {
    input.value = '';
    clear.hidden = true;
    input.focus();
  });
  input.addEventListener('input', () => {
    clear.hidden = !input.value;
  });

  async function submit() {
    if (button.disabled) return;
    const token = input.value.trim();
    if (!token) {
      showToast('请输入token');
      input.focus();
      return;
    }
    setLoading(true);
    try {
      const response = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
      if (!response.ok) {
        let message = '登录失败：令牌错误';
        try {
          const data = await response.json();
          if (data && data.error) message = data.error;
        } catch (_) { /* keep default message */ }
        throw new Error(message);
      }
      location.href = nextUrl();
    } catch (error) {
      showToast(error instanceof Error ? error.message : '登录失败');
      setLoading(false);
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    submit();
  });
  button.addEventListener('click', (event) => {
    event.preventDefault();
    submit();
  });

  // ?token=xxxx auto-submits, matching the NapCat WebUI behavior.
  const preset = new URLSearchParams(location.search).get('token');
  if (preset) {
    input.value = preset;
    clear.hidden = false;
  }

  fetch('/api/v1/auth/check', { headers: { Accept: 'application/json' } })
    .then((response) => (response.ok ? response.json() : { ok: false }))
    .then((data) => {
      if (data && data.ok) {
        statusLine.textContent = '🔐 已验证，正在进入…';
        location.replace(nextUrl());
        return;
      }
      statusLine.hidden = true;
    })
    .catch(() => {
      statusLine.hidden = true;
    });

  if (preset) {
    statusLine.hidden = true;
    submit();
  }

  // Tilt + cursor glow, same interaction as the NapCat login card.
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduceMotion && window.matchMedia('(pointer: fine)').matches) {
    card.addEventListener('mouseenter', () => {
      card.style.transition = 'transform .3s ease-out';
    });
    card.addEventListener('mousemove', (event) => {
      const rect = card.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const rotateX = ((y - rect.height / 2) / (rect.height / 2)) * -3;
      const rotateY = ((x - rect.width / 2) / (rect.width / 2)) * 3;
      card.style.transition = 'transform .1s';
      card.style.transform = `perspective(1000px) rotateX(${rotateX.toFixed(2)}deg) rotateY(${rotateY.toFixed(2)}deg)`;
      glow.style.left = `${x - 75}px`;
      glow.style.top = `${y - 75}px`;
    });
    card.addEventListener('mouseleave', () => {
      card.style.transition = 'transform .5s';
      card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg)';
      glow.style.opacity = '';
    });
  }
});
