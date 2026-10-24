"use strict";

const API = "/api/v1";
const app = document.getElementById("app");
const title = document.getElementById("page-title");
const subtitle = document.getElementById("page-subtitle");
const backLink = document.getElementById("back-link");
const themeToggle = document.getElementById("theme-toggle");
let logTimer = null;
let dashboardSocket = null;
let dashboardHeartbeat = null;
let reconnectTimer = null;
let reconnectAttempt = 0;

function element(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = String(text);
  return node;
}

function link(href, className, text) {
  const node = element("a", className, text);
  node.href = href;
  return node;
}

function button(text, className = "button", handler = null) {
  const node = element("button", className, text);
  node.type = "button";
  if (handler) node.addEventListener("click", handler);
  return node;
}

function setHeader(pageTitle, pageSubtitle = "", backHref = "") {
  title.textContent = pageTitle;
  subtitle.textContent = pageSubtitle;
  document.title = `${pageTitle} - 聊天机器人`;
  backLink.classList.toggle("hidden", !backHref);
  if (backHref) backLink.href = backHref;
}

const secondaryNav = new Set(["persona", "stickers", "usage", "settings", "logs"]);

function setActiveNav(name) {
  document.querySelectorAll("[data-nav]").forEach((item) => {
    const active = item.dataset.nav === name;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  const moreToggle = document.getElementById("nav-more-toggle");
  if (moreToggle) {
    const active = secondaryNav.has(name);
    moreToggle.classList.toggle("active", active);
    if (active) moreToggle.setAttribute("aria-current", "page");
    else moreToggle.removeAttribute("aria-current");
  }
}

function section(label, children = []) {
  const wrapper = element("section", "section");
  if (label) wrapper.append(element("h2", "section-title", label));
  const body = element("div", "section-body");
  body.append(...children);
  wrapper.append(body);
  return wrapper;
}

function empty(text) {
  return element("div", "empty", text);
}

function errorMessage(payload, fallback = "请求失败") {
  if (!payload) return fallback;
  if (typeof payload.detail === "string") return payload.detail;
  if (typeof payload.error === "string") return payload.error;
  if (Array.isArray(payload.detail)) return payload.detail.map((entry) => entry.msg || fallback).join("；");
  return fallback;
}

async function fetchJSON(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorMessage(payload, `请求失败 (${response.status})`));
  return payload;
}

function showError(error, retry) {
  const box = element("div", "error-box");
  box.setAttribute("role", "alert");
  box.append(element("div", "", error instanceof Error ? error.message : "加载失败"));
  if (retry) box.append(button("重试", "button secondary", retry));
  app.replaceChildren(box);
}

function itemRow({ icon, name, description = "", href, badge = "", action = null }) {
  const row = element("div", "item");
  row.append(element("div", "item-icon", icon));
  const target = link(href, "item-link", "");
  const info = element("div", "item-info");
  info.append(element("div", "item-name", name));
  if (description) info.append(element("div", "item-description", description));
  target.append(info);
  row.append(target);
  if (badge) row.append(element("span", "badge", badge));
  if (action) row.append(action);
  return row;
}

function applyTheme(dark) {
  document.documentElement.classList.toggle("dark", dark);
  themeToggle.textContent = dark ? "☀️" : "🌙";
  themeToggle.setAttribute("aria-label", dark ? "切换浅色模式" : "切换深色模式");
}

themeToggle.addEventListener("click", () => {
  const dark = !document.documentElement.classList.contains("dark");
  localStorage.setItem("dark", String(dark));
  applyTheme(dark);
});
const storedTheme = localStorage.getItem("dark");
applyTheme(storedTheme === null ? window.matchMedia("(prefers-color-scheme: dark)").matches : storedTheme === "true");

const navMoreToggle = document.getElementById("nav-more-toggle");
const navMore = document.getElementById("nav-more");
const navBackdrop = document.getElementById("nav-backdrop");

function setMoreOpen(open) {
  if (!navMore || !navMoreToggle) return;
  navMore.classList.toggle("open", open);
  navMore.setAttribute("aria-hidden", String(!open));
  navMoreToggle.setAttribute("aria-expanded", String(open));
  navBackdrop?.classList.toggle("show", open);
  document.body.classList.toggle("nav-open", open);
}

if (navMoreToggle && navMore) {
  navMoreToggle.addEventListener("click", () => {
    setMoreOpen(!navMore.classList.contains("open"));
  });
  navBackdrop?.addEventListener("click", () => setMoreOpen(false));
  navMore.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => setMoreOpen(false));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setMoreOpen(false);
  });
}

function formatUptime(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

async function loadDashboard() {
  try {
    const [health, stats, sessionData, cards] = await Promise.all([
      fetchJSON("/health"),
      fetchJSON("/stats"),
      fetchJSON("/sessions?limit=8"),
      fetchJSON("/cards"),
    ]);
    setHeader("聊天机器人", `运行 ${formatUptime(health.uptime_seconds)} · 状态 ${health.status}`);
    setActiveNav("home");
    const statsGrid = element("div", "stats-grid");
    [
      [stats.session_count, "活跃会话"],
      [stats.entity_count, "记忆实体"],
      [stats.card_count, "角色卡"],
      [stats.style_count, "风格"],
    ].forEach(([value, labelText]) => {
      const card = element("div", "stat-card");
      card.append(element("div", "stat-value", value), element("div", "stat-label", labelText));
      statsGrid.append(card);
    });
    const sessionRows = sessionData.sessions.map((session) => itemRow({
      icon: { wechat: "💚", qq: "🐧", console: "💻", telegram: "✈️" }[session.platform] || "📱",
      name: session.id,
      description: session.last_message || "暂无消息",
      href: `/sessions/${encodeURIComponent(session.id)}`,
      badge: `${session.message_count} 条`,
    }));
    const cardRows = cards.map((name) => itemRow({ icon: "🎭", name, href: `/cards/${encodeURIComponent(name)}` }));
    app.replaceChildren(
      statsGrid,
      section("最近会话", sessionRows.length ? sessionRows : [empty("暂无会话")]),
      section("角色卡", cardRows.length ? cardRows : [empty("暂无角色卡")]),
    );
    connectDashboardSocket();
  } catch (error) {
    showError(error, loadDashboard);
  }
}

function clearDashboardSocket() {
  if (dashboardHeartbeat) window.clearInterval(dashboardHeartbeat);
  if (reconnectTimer) window.clearTimeout(reconnectTimer);
  dashboardHeartbeat = null;
  reconnectTimer = null;
  if (dashboardSocket) {
    dashboardSocket.onclose = null;
    dashboardSocket.close();
    dashboardSocket = null;
  }
}

function connectDashboardSocket() {
  if (dashboardSocket || location.pathname !== "/") return;
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  dashboardSocket = new WebSocket(`${protocol}//${location.host}/ws`);
  dashboardSocket.onopen = () => {
    reconnectAttempt = 0;
    dashboardHeartbeat = window.setInterval(() => {
      if (dashboardSocket?.readyState === WebSocket.OPEN) dashboardSocket.send("ping");
    }, 25000);
  };
  dashboardSocket.onmessage = () => loadDashboard();
  dashboardSocket.onclose = () => {
    dashboardSocket = null;
    if (dashboardHeartbeat) window.clearInterval(dashboardHeartbeat);
    dashboardHeartbeat = null;
    if (document.visibilityState === "hidden") return;
    const delay = Math.min(30000, 1000 * (2 ** reconnectAttempt)) + Math.random() * 500;
    reconnectAttempt += 1;
    reconnectTimer = window.setTimeout(connectDashboardSocket, delay);
  };
}

async function loadSessions() {
  setHeader("会话管理", "浏览最近会话");
  setActiveNav("sessions");
  const list = element("div");
  const loadMore = button("加载更多", "button secondary");
  let offset = 0;
  const limit = 20;
  let total = 0;

  async function appendPage() {
    loadMore.disabled = true;
    try {
      const data = await fetchJSON(`/sessions?limit=${limit}&offset=${offset}`);
      total = data.total;
      data.sessions.forEach((session) => list.append(itemRow({
        icon: { wechat: "💚", qq: "🐧", console: "💻", telegram: "✈️" }[session.platform] || "📱",
        name: session.id,
        description: `${session.character_card} · ${session.style} · ${session.message_count} 条`,
        href: `/sessions/${encodeURIComponent(session.id)}`,
      })));
      offset += data.sessions.length;
      loadMore.classList.toggle("hidden", offset >= total);
      if (!total) list.replaceChildren(empty("暂无会话"));
    } catch (error) {
      showError(error, loadSessions);
    } finally {
      loadMore.disabled = false;
    }
  }
  loadMore.addEventListener("click", appendPage);
  const controls = element("div", "toolbar");
  controls.append(loadMore);
  app.replaceChildren(section("全部会话", [list, controls]));
  await appendPage();
}

async function loadSessionDetail(sessionId) {
  setHeader(sessionId, "会话详情", "/sessions");
  setActiveNav("sessions");
  try {
    const data = await fetchJSON(`/sessions/${encodeURIComponent(sessionId)}`);
    subtitle.textContent = `角色卡：${data.character_card} · 风格：${data.style}${data.summary ? ` · 摘要：${data.summary.slice(0, 30)}` : ""}`;
    const messages = element("div", "messages");
    data.messages.forEach((message) => {
      const row = element("article", `message ${message.role === "user" ? "user" : "assistant"}`);
      row.append(
        element("div", "message-role", message.role === "user" ? "👤 我" : "🤖 AI"),
        element("div", "message-content", message.content),
      );
      messages.append(row);
    });
    if (!data.messages.length) messages.append(empty("暂无消息"));
    const exportButton = button("📥 导出对话", "button secondary", async () => {
      try {
        const exported = await fetchJSON(`/sessions/${encodeURIComponent(sessionId)}/export`);
        const url = URL.createObjectURL(new Blob([exported.messages.join("\n\n")], { type: "text/plain;charset=utf-8" }));
        const anchor = link(url, "hidden", "下载");
        anchor.download = `chat-${sessionId.replace(/[^\w\u4e00-\u9fff-]/g, "_")}.txt`;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
      } catch (error) {
        window.alert(error.message);
      }
    });
    const controls = element("div", "toolbar");
    controls.append(exportButton);
    app.replaceChildren(section("消息记录", [messages, controls]));
  } catch (error) {
    showError(error, () => loadSessionDetail(sessionId));
  }
}

async function loadCards() {
  setHeader("角色卡管理", "创建和维护角色设定");
  setActiveNav("cards");
  try {
    const cards = await fetchJSON("/cards");
    const rows = cards.map((name) => {
      const edit = link(`/cards/edit?edit=${encodeURIComponent(name)}`, "button secondary small", "编辑");
      edit.setAttribute("aria-label", `编辑角色卡 ${name}`);
      return itemRow({ icon: "🎭", name, href: `/cards/${encodeURIComponent(name)}`, action: edit });
    });
    const controls = element("div", "toolbar");
    controls.append(link("/cards/edit", "button", "+ 新建角色卡"));
    app.replaceChildren(section("角色卡", [controls, ...(rows.length ? rows : [empty("暂无角色卡")])]));
  } catch (error) {
    showError(error, loadCards);
  }
}

async function loadCardDetail(cardName) {
  setHeader(cardName, "角色卡详情", "/cards");
  setActiveNav("cards");
  try {
    const card = await fetchJSON(`/cards/${encodeURIComponent(cardName)}`);
    const detail = element("div", "detail");
    detail.append(element("h2", "", "描述"), element("p", "", card.description || "无"));
    if (card.rules.length) {
      const rules = element("ul", "rules");
      card.rules.forEach((rule) => rules.append(element("li", "", rule)));
      detail.append(element("h2", "", "规则"), rules);
    }
    detail.append(element("h2", "", "人格设定（Markdown）"), element("p", "", card.system_prompt || "无"));
    const controls = element("div", "toolbar");
    controls.append(link(`/cards/edit?edit=${encodeURIComponent(cardName)}`, "button", "编辑角色卡"));
    app.replaceChildren(section("角色设定", [detail, controls]));
  } catch (error) {
    showError(error, () => loadCardDetail(cardName));
  }
}

function formGroup(labelText, control) {
  const group = element("div", "form-group");
  const labelNode = element("label", "", labelText);
  labelNode.htmlFor = control.id;
  group.append(labelNode, control);
  return group;
}

async function loadCardEditor() {
  const editName = new URLSearchParams(location.search).get("edit");
  setHeader(editName ? "编辑角色卡" : "新建角色卡", editName || "填写角色设定", "/cards");
  setActiveNav("cards");
  const form = element("form", "form");
  const name = element("input"); name.id = "card-name"; name.required = true; name.maxLength = 64;
  const description = element("textarea"); description.id = "card-description"; description.maxLength = 4000;
  const prompt = element("textarea"); prompt.id = "card-prompt"; prompt.maxLength = 50000; prompt.rows = 9;
  prompt.placeholder = "用 Markdown 写身份、性格、说话方式、关系和边界；不要写工具用法。";
  const rules = element("textarea"); rules.id = "card-rules"; rules.rows = 4;
  const greeting = element("input"); greeting.id = "card-greeting"; greeting.maxLength = 4000;
  const status = element("div", "form-status"); status.setAttribute("role", "status");
  const saveButton = button("保存", "button"); saveButton.type = "submit";
  const controls = element("div", "button-row");
  controls.append(saveButton, button("取消", "button secondary", () => history.back()));
  form.append(
    formGroup("名称", name),
    formGroup("描述", description),
    formGroup("人格设定（Markdown）", prompt),
    formGroup("规则（每行一条）", rules),
    formGroup("问候语", greeting),
    controls,
    status,
  );
  app.replaceChildren(section("编辑器", [form]));
  if (editName) {
    try {
      const card = await fetchJSON(`/cards/${encodeURIComponent(editName)}`);
      name.value = card.name;
      name.readOnly = true;
      description.value = card.description || "";
      prompt.value = card.system_prompt || "";
      rules.value = card.rules.join("\n");
      greeting.value = card.greeting || "";
    } catch (error) {
      showError(error, loadCardEditor);
      return;
    }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    saveButton.disabled = true;
    status.className = "form-status";
    status.textContent = "保存中…";
    try {
      await fetchJSON(editName ? `/cards/${encodeURIComponent(editName)}` : "/cards", {
        method: editName ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.value,
          description: description.value,
          system_prompt: prompt.value,
          rules: rules.value.split("\n").map((rule) => rule.trim()).filter(Boolean),
          greeting: greeting.value,
        }),
      });
      status.className = "form-status success";
      status.textContent = "保存成功，正在返回角色列表…";
      window.setTimeout(() => { location.href = "/cards"; }, 500);
    } catch (error) {
      status.className = "form-status error";
      status.textContent = error.message;
      saveButton.disabled = false;
    }
  });
}

async function loadSettings() {
  setHeader("设置", "运行状态概览");
  setActiveNav("settings");
  try {
    const [health, stats, metrics] = await Promise.all([
      fetchJSON("/health"),
      fetchJSON("/stats"),
      fetchJSON("/metrics"),
    ]);
    const persistenceReady = metrics.persistence?.status !== "unavailable";
    const ready = Boolean(metrics.engine?.ready) && persistenceReady;
    const rows = [
      ["存活状态", health.status === "ok" ? "正常" : health.status, true],
      ["就绪状态", ready ? "就绪" : "未就绪", ready],
      ["运行时间", formatUptime(health.uptime_seconds)],
      ["活跃会话", stats.session_count],
      ["角色卡数", stats.card_count],
      ["风格数", stats.style_count],
      ["记忆实体", stats.entity_count],
    ].map(([labelText, value, healthy = false]) => {
      const row = element("div", "setting-row");
      row.append(element("span", "", labelText), element("span", `setting-value${healthy ? " healthy" : ""}`, value));
      return row;
    });
    app.replaceChildren(section("系统信息", rows));
  } catch (error) {
    showError(error, loadSettings);
  }
}

async function loadLogs() {
  setHeader("运行日志", "每 10 秒自动刷新", "/");
  setActiveNav("");
  const level = element("select");
  level.setAttribute("aria-label", "日志级别");
  [["", "所有级别"], ["ERROR", "ERROR"], ["WARNING", "WARNING"], ["INFO", "INFO"]].forEach(([value, labelText]) => {
    const option = element("option", "", labelText); option.value = value; level.append(option);
  });
  const count = element("span", "toolbar-count");
  const logs = element("div", "logs");
  const refresh = button("🔄 刷新", "button small");
  const toolbar = element("div", "toolbar");
  toolbar.append(refresh, level, count);
  app.replaceChildren(section("最近 200 行", [toolbar, logs]));
  async function refreshLogs() {
    try {
      const data = await fetchJSON("/logs?lines=200");
      const filtered = level.value ? data.lines.filter((lineText) => lineText.split(" | ")[1]?.trim() === level.value) : data.lines;
      logs.replaceChildren(...(filtered.length ? filtered.map((lineText) => element("div", "log-line", lineText)) : [empty("暂无日志")]));
      count.textContent = `${filtered.length} 行`;
    } catch (error) {
      logs.replaceChildren(element("div", "error-box", error.message));
    }
  }
  refresh.addEventListener("click", refreshLogs);
  level.addEventListener("change", refreshLogs);
  await refreshLogs();
  logTimer = window.setInterval(refreshLogs, 10000);
}

async function loadPersona() {
  setHeader("人格", "全局特质需管理员确认后生效");
  setActiveNav("persona");
  const scope = element("select");
  [["global", "全局"], ["user", "用户"]].forEach(([value, labelText]) => {
    const option = element("option", "", labelText);
    option.value = value;
    scope.append(option);
  });
  const scopeKey = element("input");
  scopeKey.placeholder = "用户主体键，例如 qq:123";
  scopeKey.className = "input";
  const refresh = button("刷新");
  const rollback = button("回滚一步", "button secondary");
  const keyword = element("input");
  keyword.placeholder = "删除含关键词的特质";
  keyword.className = "input";
  const forget = button("删除", "button secondary");
  const status = element("div", "toolbar-count");
  const toolbar = element("div", "toolbar");
  toolbar.append(scope, scopeKey, refresh, rollback, keyword, forget, status);
  const list = element("div", "list");
  app.replaceChildren(section("人格特质", [toolbar, list]));

  function resolvedKey() {
    return scope.value === "global" ? "global" : scopeKey.value.trim();
  }
  async function refreshList() {
    const key = resolvedKey();
    if (!key) {
      list.replaceChildren(empty("请输入用户主体键，例如 qq:123"));
      return;
    }
    try {
      const data = await fetchJSON(`/persona?scope=${scope.value}&scope_key=${encodeURIComponent(key)}`);
      status.textContent = `${data.traits.length} 条`;
      if (!data.traits.length) {
        list.replaceChildren(empty("暂无特质"));
        return;
      }
      list.replaceChildren(...data.traits.map((trait) => {
        const row = element("div", "item");
        const info = element("div", "item-info");
        info.append(element("div", "item-name", `${trait.category} · ${trait.value}`));
        info.append(element("div", "item-description", `#${trait.id} · ${trait.status}/${trait.tier} · 置信度 ${trait.confidence}`));
        row.append(info);
        if (trait.status === "candidate" && trait.scope === "global") {
          row.append(button("批准", "button small", async () => {
            await fetchJSON(`/persona/approve/${trait.id}`, { method: "POST" });
            await refreshList();
          }));
        }
        return row;
      }));
    } catch (error) {
      list.replaceChildren(element("div", "error-box", error.message));
    }
  }
  async function mutate(path, options) {
    try {
      await fetchJSON(path, options);
      await refreshList();
    } catch (error) {
      status.textContent = error.message;
    }
  }
  refresh.addEventListener("click", refreshList);
  scope.addEventListener("change", refreshList);
  scopeKey.addEventListener("change", refreshList);
  rollback.addEventListener("click", () => {
    const key = resolvedKey();
    if (!key) return;
    mutate("/persona/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: scope.value, scope_key: key, steps: 1 }),
    });
  });
  forget.addEventListener("click", () => {
    const key = resolvedKey();
    const value = keyword.value.trim();
    if (!key || !value) return;
    mutate(`/persona/traits?keyword=${encodeURIComponent(value)}&scope=${scope.value}&scope_key=${encodeURIComponent(key)}`, { method: "DELETE" });
  });
  await refreshList();
}

async function loadStickers() {
  setHeader("表情包", "审核自动收集的表情");
  setActiveNav("stickers");
  const status = element("select");
  [["", "全部"], ["active", "已启用"], ["candidate", "待审核"], ["rejected", "已拒绝"]].forEach(([value, labelText]) => {
    const option = element("option", "", labelText);
    option.value = value;
    status.append(option);
  });
  const count = element("span", "toolbar-count");
  const toolbar = element("div", "toolbar");
  toolbar.append(status, count);
  const list = element("div", "list");
  app.replaceChildren(section("表情包库", [toolbar, list]));

  async function refreshList() {
    try {
      const data = await fetchJSON(`/stickers?status=${encodeURIComponent(status.value)}`);
      count.textContent = `${data.stickers.length} 张`;
      if (!data.stickers.length) {
        list.replaceChildren(empty("暂无表情包"));
        return;
      }
      list.replaceChildren(...data.stickers.map((sticker) => {
        const row = element("div", "item");
        const info = element("div", "item-info");
        info.append(element("div", "item-name", sticker.tags.join("、") || "未标注"));
        info.append(element("div", "item-description", `${sticker.hash.slice(0, 12)} · ${sticker.status} · 使用 ${sticker.used_count} 次`));
        row.append(info);
        if (sticker.status !== "active") {
          row.append(button("通过", "button small", async () => {
            await fetchJSON(`/stickers/${sticker.hash}/approve`, { method: "POST" });
            await refreshList();
          }));
        }
        if (sticker.status !== "rejected") {
          row.append(button("拒绝", "button secondary small", async () => {
            await fetchJSON(`/stickers/${sticker.hash}/reject`, { method: "POST" });
            await refreshList();
          }));
        }
        return row;
      }));
    } catch (error) {
      list.replaceChildren(element("div", "error-box", error.message));
    }
  }
  status.addEventListener("change", refreshList);
  await refreshList();
}

async function loadUsage() {
  setHeader("用量", "Token 与费用统计");
  setActiveNav("usage");
  const days = element("select");
  [["1", "今天"], ["7", "最近 7 天"], ["30", "最近 30 天"]].forEach(([value, labelText]) => {
    const option = element("option", "", labelText);
    option.value = value;
    days.append(option);
  });
  days.value = "7";
  const toolbar = element("div", "toolbar");
  toolbar.append(days);
  const list = element("div", "list");
  app.replaceChildren(section("用量汇总", [toolbar, list]));

  async function refreshList() {
    try {
      const data = await fetchJSON(`/usage?days=${days.value}`);
      const rows = [
        ["对话轮次", data.turns],
        ["缓存未命中输入", data.cache_miss_tokens],
        ["缓存命中输入", data.cache_hit_tokens],
        ["缓存写入", data.cache_write_tokens],
        ["输出", data.output_tokens],
        ["费用", `${(data.cost_micros / 1000000).toFixed(4)} ${data.currency}`],
      ];
      if (data.daily_budget_micros > 0) {
        rows.push(["每日预算", `${(data.daily_budget_micros / 1000000).toFixed(4)} ${data.currency}`]);
        rows.push(["今日剩余", `${(data.remaining_micros / 1000000).toFixed(4)} ${data.currency}`]);
      }
      const rendered = rows.map(([labelText, value]) => {
        const row = element("div", "setting-row");
        row.append(element("span", "", labelText), element("span", "setting-value", value));
        return row;
      });
      list.replaceChildren(...rendered);
    } catch (error) {
      list.replaceChildren(element("div", "error-box", error.message));
    }
  }
  days.addEventListener("change", refreshList);
  await refreshList();
}

function route() {
  setMoreOpen(false);
  const path = location.pathname;
  if (path === "/") return loadDashboard();
  if (path === "/sessions") return loadSessions();
  if (path.startsWith("/sessions/")) return loadSessionDetail(decodeURIComponent(path.slice(10)));
  if (path === "/cards") return loadCards();
  if (path === "/cards/edit") return loadCardEditor();
  if (path.startsWith("/cards/")) return loadCardDetail(decodeURIComponent(path.slice(7)));
  if (path === "/persona") return loadPersona();
  if (path === "/stickers") return loadStickers();
  if (path === "/usage") return loadUsage();
  if (path === "/settings") return loadSettings();
  if (path === "/logs") return loadLogs();
  showError(new Error("页面不存在"));
}

document.addEventListener("visibilitychange", () => {
  if (location.pathname === "/" && document.visibilityState === "visible" && !dashboardSocket) connectDashboardSocket();
});
window.addEventListener("beforeunload", () => {
  clearDashboardSocket();
  if (logTimer) window.clearInterval(logTimer);
});
route();
