/* Chat BI 前端逻辑（M3）：会话管理 / 对话渲染 / clarify 快捷选项 / Excel 导出轮询。
   无框架依赖：消息按会话持久化到 localStorage，刷新后可回看。 */
"use strict";

const $ = (sel) => document.querySelector(sel);

const state = {
  sessions: JSON.parse(localStorage.getItem("chatbi.sessions") || "[]"), // [{id, title, ts}]
  currentId: null,
  userId: Number(localStorage.getItem("chatbi.uid") || 1),
  busy: false,
  typingTimer: null,
};

/* 空状态模板在首次渲染前捕获（renderMessages 会清空 #messages） */
const EMPTY_HTML = document.getElementById("emptyState")?.innerHTML || "";

/* ---------- 基础工具 ---------- */

function persist() {
  localStorage.setItem("chatbi.sessions", JSON.stringify(state.sessions));
  if (state.currentId) localStorage.setItem("chatbi.current", state.currentId);
}

function loadMsgs(sessionId) {
  return JSON.parse(localStorage.getItem("chatbi.msgs." + sessionId) || "[]");
}
function saveMsgs(sessionId, msgs) {
  localStorage.setItem("chatbi.msgs." + sessionId, JSON.stringify(msgs));
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* 结论文本轻量渲染：转义 → **粗体** → 列表/换行（不引外部 md 库） */
function mdLite(text) {
  const esc = escapeHtml(text);
  return esc
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .split("\n")
    .map((line) => {
      const t = line.trim();
      if (/^[-•·]\s+/.test(t)) return `<div class="md-li">• ${t.replace(/^[-•·]\s+/, "")}</div>`;
      if (/^\d+[.、)]\s*/.test(t)) {
        const n = t.match(/^\d+/)[0];
        return `<div class="md-li">${n}. ${t.replace(/^\d+[.、)]\s*/, "")}</div>`;
      }
      return t ? `<div>${t}</div>` : "";
    })
    .join("");
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 3200);
}

function scrollBottom() {
  const box = $("#messages");
  box.scrollTop = box.scrollHeight;
}

/* ---------- 会话管理 ---------- */

function currentMsgs() {
  if (!state.currentId) return [];
  if (!state.msgsCache) state.msgsCache = {};
  if (!state.msgsCache[state.currentId]) state.msgsCache[state.currentId] = loadMsgs(state.currentId);
  return state.msgsCache[state.currentId];
}

function newSession() {
  state.currentId = null;
  $("#convTitle").textContent = "新对话";
  $("#convMeta").textContent = "";
  renderMessages();
  renderSessions();
  $("#input").focus();
}

function openSession(id) {
  state.currentId = id;
  const s = state.sessions.find((x) => x.id === id);
  $("#convTitle").textContent = s ? s.title : "对话";
  $("#convMeta").textContent = s ? new Date(s.ts).toLocaleString("zh-CN") : "";
  renderMessages();
  renderSessions();
  persist();
}

function registerSession(id, title) {
  if (state.sessions.find((x) => x.id === id)) return;
  state.sessions.unshift({ id, title, ts: Date.now() });
  if (state.sessions.length > 50) state.sessions.pop(); // 原型阶段上限 50 个
  $("#convTitle").textContent = title;
  $("#convMeta").textContent = new Date().toLocaleString("zh-CN");
  persist();
  renderSessions();
}

function deleteSession(id, ev) {
  ev.stopPropagation();
  state.sessions = state.sessions.filter((x) => x.id !== id);
  localStorage.removeItem("chatbi.msgs." + id);
  if (state.currentId === id) newSession();
  else renderSessions();
  persist();
}

function renderSessions() {
  const nav = $("#sessionList");
  nav.innerHTML = "";
  for (const s of state.sessions) {
    const item = document.createElement("div");
    item.className = "session-item" + (s.id === state.currentId ? " active" : "");
    item.innerHTML =
      `<svg viewBox="0 0 24 24" fill="none"><path d="M21 12a8 8 0 01-8 8H6l-3 3V12a8 8 0 018-8h2a8 8 0 018 8z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>` +
      `<span>${escapeHtml(s.title)}</span>` +
      `<span class="del" title="删除会话"><svg viewBox="0 0 24 24"><path d="M5 5l14 14M19 5L5 19" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg></span>`;
    item.addEventListener("click", () => openSession(s.id));
    item.querySelector(".del").addEventListener("click", (e) => deleteSession(s.id, e));
    nav.appendChild(item);
  }
}

/* ---------- 消息渲染 ---------- */

function renderMessages() {
  const box = $("#messages");
  box.innerHTML = "";
  const msgs = currentMsgs();
  if (!msgs.length) {
    box.appendChild(buildEmptyState());
    return;
  }
  for (const m of msgs) box.appendChild(m.role === "user" ? buildUserMsg(m) : buildAssistantMsg(m));
  scrollBottom();
}

function buildEmptyState() {
  const tpl = document.createElement("template");
  tpl.innerHTML = EMPTY_HTML;
  const el = tpl.content.firstElementChild;
  el.querySelectorAll(".es-suggestions button").forEach((b) =>
    b.addEventListener("click", () => send(b.dataset.q))
  );
  return el;
}

function buildUserMsg(m) {
  const div = document.createElement("div");
  div.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = m.text;
  div.appendChild(bubble);
  return div;
}

const AVATAR_SVG =
  `<div class="avatar"><svg viewBox="0 0 24 24" fill="none"><path d="M12 3l1.9 4.8L18.7 9.7l-4.8 1.9L12 16.4l-1.9-4.8L5.3 9.7l4.8-1.9z" fill="currentColor"/><circle cx="18.3" cy="17.8" r="1.9" fill="currentColor" opacity=".6"/></svg></div>`;

function buildAssistantMsg(m) {
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.insertAdjacentHTML("beforeend", AVATAR_SVG);

  if (m.type === "clarify") wrap.appendChild(buildClarifyCard(m));
  else if (m.type === "error") wrap.appendChild(buildErrorCard(m));
  else wrap.appendChild(buildDataCard(m));
  return wrap;
}

function cardHead(m, extra = "") {
  const chips = (m.tables || [])
    .map((t) => `<span class="chip tables">${escapeHtml(t)}</span>`)
    .join("");
  const retry = m.retry_count > 0 ? `<span class="badge-retry">🛠 自愈 ×${m.retry_count}</span>` : "";
  return `<div class="card-head">${chips}${retry}${extra}</div>`;
}

function buildClarifyCard(m) {
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML =
    cardHead(m, `<span class="chip">需要澄清</span>`) +
    `<div class="card-body">
       <div class="clarify-q">${escapeHtml(m.clarify_question)}</div>
       <div class="clarify-opts"></div>
     </div>`;
  const opts = card.querySelector(".clarify-opts");
  for (const o of m.clarify_options || []) {
    const b = document.createElement("button");
    b.className = "opt-btn";
    b.textContent = o;
    b.addEventListener("click", () => send(o));
    opts.appendChild(b);
  }
  if (!(m.clarify_options || []).length) {
    opts.innerHTML = `<span style="font-size:12.5px;color:var(--ink-faint)">直接输入你的补充即可</span>`;
  }
  return card;
}

function buildErrorCard(m) {
  const card = document.createElement("div");
  card.className = "card error";
  card.innerHTML =
    `<div class="card-head"><span class="error-code">${escapeHtml(m.error_code || "ERROR")}</span>
       ${m.retry_count > 0 ? `<span class="badge-retry">自愈尝试 ×${m.retry_count}</span>` : ""}</div>` +
    `<div class="card-body">${escapeHtml(m.error || "未知错误")}` +
    (m.sql ? `<div class="sql-echo">${escapeHtml(m.sql)}</div>` : "") +
    `</div>`;
  return card;
}

function buildDataCard(m) {
  const card = document.createElement("div");
  const hasRows = (m.rows || []).length > 0;

  const tableHtml = hasRows
    ? `<div class="tbl-wrap"><table>
         <thead><tr>${m.columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr></thead>
         <tbody>${m.rows
           .map(
             (r) =>
               `<tr>${r.map((v) => `<td>${v === null || v === undefined ? "—" : escapeHtml(v)}</td>`).join("")}</tr>`
           )
           .join("")}</tbody>
       </table></div>`
    : `<div class="tbl-empty">查询执行成功，但没有匹配的数据行</div>`;

  const conclusionHtml = m.conclusion
    ? `<div class="conclusion">
         <div class="c-title"><svg viewBox="0 0 24 24"><path d="M12 2.5l2.3 5.9 5.9 2.3-5.9 2.3L12 18.9l-2.3-5.9-5.9-2.3 5.9-2.3z" fill="currentColor"/></svg>分析结论</div>
         ${mdLite(m.conclusion)}
       </div>`
    : "";

  const sqlHtml = m.sql
    ? `<details class="sql"><summary>SQL 语句</summary><pre>${escapeHtml(m.sql)}</pre></details>`
    : "";

  card.className = "card";
  card.innerHTML =
    cardHead(m) +
    `<div class="card-body">${conclusionHtml}${tableHtml}${sqlHtml}</div>` +
    `<div class="card-foot">
       <span class="digest">${escapeHtml(m.digest || "")}</span>
       ${m.sql ? `<button class="btn-export" data-session="${escapeHtml(m.session_id || "")}">
         <svg viewBox="0 0 24 24"><path d="M12 3v11m0 0l-4-4m4 4l4-4M5 20h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>
         导出 Excel</button>` : ""}
     </div>`;

  const btn = card.querySelector(".btn-export");
  if (btn) btn.addEventListener("click", () => exportFlow(btn.dataset.session, btn));
  return card;
}

/* ---------- 发送 / 加载动画 ---------- */

const STEPS = ["改写问题", "检索表结构", "生成 SQL", "安全校验", "执行查询"];
function showTyping() {
  const box = $("#messages");
  const div = document.createElement("div");
  div.className = "msg assistant typing";
  div.id = "typing";
  div.innerHTML =
    AVATAR_SVG +
    `<div class="card"><div class="steps">
       <span class="step-label">${STEPS[0]}</span>
       <span class="dots"><i></i><i></i><i></i></span>
     </div></div>`;
  box.appendChild(div);
  scrollBottom();
  let i = 0;
  state.typingTimer = setInterval(() => {
    i = (i + 1) % STEPS.length;
    const label = div.querySelector(".step-label");
    if (label) label.textContent = STEPS[i];
  }, 900);
}
function hideTyping() {
  clearInterval(state.typingTimer);
  $("#typing")?.remove();
}

function setBusy(b) {
  state.busy = b;
  $("#sendBtn").disabled = b || !$("#input").value.trim();
}

async function send(text) {
  text = (text || "").trim();
  if (!text || state.busy) return;

  // 客户端预生成会话 id：首条消息即可归位（后端接受任意 session_id）
  const isNew = !state.currentId;
  if (isNew) {
    state.currentId = crypto.randomUUID().replace(/-/g, "");
    $("#convMeta").textContent = new Date().toLocaleString("zh-CN");
  }
  const sessionId = state.currentId;

  pushMsg({ role: "user", text });
  if (isNew) registerSession(sessionId, text.slice(0, 22)); // 失败也会话归档，避免孤儿消息
  $("#input").value = "";
  autoResize();
  setBusy(true);
  showTyping();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(state.userId) },
      body: JSON.stringify({ question: text, session_id: sessionId }),
    });
    if (!resp.ok) {
      const det = await resp.json().catch(() => ({}));
      throw new Error(det.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();

    let msg;
    if (data.ok === false) {
      msg = { role: "assistant", type: "error", error_code: data.error_code, error: data.error, sql: data.sql, retry_count: data.retry_count || 0, tables: [] };
    } else if (data.output_signal === "clarify") {
      msg = { role: "assistant", type: "clarify", clarify_question: data.clarify_question, clarify_options: data.clarify_options || [], tables: [] };
    } else {
      msg = { role: "assistant", type: "data", sql: data.sql, columns: data.columns, rows: data.rows, row_count: data.row_count, conclusion: data.conclusion, tables: data.tables || [], retry_count: data.retry_count || 0, digest: data.digest, session_id: data.session_id };
    }
    pushMsg(msg);
  } catch (e) {
    pushMsg({ role: "assistant", type: "error", error_code: "NETWORK", error: e.message, tables: [] });
  } finally {
    hideTyping();
    setBusy(false);
    $("#input").focus();
  }
}

function pushMsg(m) {
  const msgs = currentMsgs();
  msgs.push(m);
  saveMsgs(state.currentId || "draft", msgs);
  $("#emptyState")?.remove();
  $("#messages").appendChild(m.role === "user" ? buildUserMsg(m) : buildAssistantMsg(m));
  scrollBottom();
}

/* ---------- Excel 导出（入队 + 轮询状态） ---------- */

async function triggerDownload(url, filename) {
  /* blob 方式触发浏览器下载：跨过弹出式链接点击的自动下载限制 */
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const blob = await r.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  } catch (e) {
    toast("下载失败：" + e.message);
  }
}

async function exportFlow(sessionId, btn) {
  if (!sessionId) return toast("缺少会话 ID，无法导出");
  btn.disabled = true;
  btn.textContent = "导出中…";
  try {
    const r = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": String(state.userId) },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);

    const jobId = j.job_id;
    const poll = setInterval(async () => {
      try {
        const s = await fetch("/api/export/" + jobId);
        const sj = await s.json();
        if (!s.ok) throw new Error(sj.detail || `HTTP ${s.status}`);
        if (sj.state === "done") {
          clearInterval(poll);
          const a = document.createElement("a");
          a.className = "btn-export";
          a.href = `/api/export/${jobId}/download`;
          a.textContent = `重新下载 Excel（${sj.rows} 行${sj.truncated ? "，已截断" : ""}）`;
          btn.replaceWith(a);
          // 主动触发一次下载（replaceWith 只换出链接，不会自动下载）
          triggerDownload(a.href, `chatbi-export-${jobId.slice(0, 8)}.xlsx`);
        } else if (sj.state === "failed") {
          clearInterval(poll);
          toast("导出失败：" + (sj.error || "未知原因"));
          btn.disabled = false;
          btn.textContent = "导出 Excel";
        }
      } catch (e) {
        clearInterval(poll);
        toast("状态查询失败：" + e.message);
        btn.disabled = false;
        btn.textContent = "导出 Excel";
      }
    }, 1000);
  } catch (e) {
    toast(e.message);
    btn.disabled = false;
    btn.textContent = "导出 Excel";
  }
}

/* ---------- 健康检查 ---------- */

async function pingHealth() {
  const dot = $("#healthDot");
  const text = $("#healthText");
  try {
    const r = await fetch("/api/health");
    if (!r.ok) throw new Error();
    dot.className = "dot ok";
    text.textContent = "服务正常";
  } catch {
    dot.className = "dot bad";
    text.textContent = "服务未连接";
  }
}

/* ---------- 初始化 ---------- */

function autoResize() {
  const el = $("#input");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
  setBusy(state.busy);
}

function init() {
  // 恢复用户 ID 与上次会话
  $("#userIdInput").value = state.userId;
  $("#userIdInput").addEventListener("change", () => {
    const v = Math.max(1, Number($("#userIdInput").value) || 1);
    state.userId = v;
    $("#userIdInput").value = v;
    localStorage.setItem("chatbi.uid", String(v));
    toast(`已切换为用户 ${v}（X-User-Id）`);
  });

  $("#newChatBtn").addEventListener("click", newSession);
  $("#sendBtn").addEventListener("click", () => send($("#input").value));

  const input = $("#input");
  input.addEventListener("input", autoResize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input.value);
    }
  });

  const last = localStorage.getItem("chatbi.current");
  if (last && state.sessions.find((x) => x.id === last)) openSession(last);
  else renderSessions();

  pingHealth();
  setInterval(pingHealth, 30000);
  input.focus();
}

init();
