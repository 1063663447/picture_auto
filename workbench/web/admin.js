/* 管理后台 4.5：账号 / 额度 / 用量总览（数据来自飞书多维表格） */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = { session: null, data: null, currency: "CNY", busy: false };

  const esc = (text) =>
    String(text == null ? "" : text).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));

  function money(value, currency) {
    const unit = (currency || state.currency) === "USD" ? "$" : "¥";
    if (value === null || value === undefined) return "—";
    const num = Number(value);
    if (!Number.isFinite(num)) return "—";
    return `${unit}${num.toLocaleString("zh-CN", { maximumFractionDigits: 4 })}`;
  }

  function when(ts) {
    if (!ts) return "—";
    const d = new Date(Number(ts) * 1000);
    if (Number.isNaN(d.getTime())) return "—";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function toast(kind, message) {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), kind === "bad" ? 6000 : 3000);
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let payload = null;
    try { payload = await res.json(); } catch (_) { /* ignore */ }
    if (res.status === 401) {
      location.href = "/login?next=%2Fadmin";
      throw new Error("未登录");
    }
    if (res.status === 403) {
      location.href = "/";
      throw new Error("需要管理员权限");
    }
    if (!res.ok || (payload && payload.ok === false)) {
      throw new Error((payload && payload.error) || `请求失败 HTTP ${res.status}`);
    }
    return payload;
  }

  /* ------------------------------------------------------------------ 渲染 */
  function renderKpis(data) {
    const t = data.totals || {};
    const q = data.quota || {};
    const cards = [
      { k: "总图片", v: t.images ?? 0, unit: "张", cls: "ok" },
      { k: "总花费", v: money(t.cost, data.currency), cls: "ok" },
      { k: "账号数", v: t.accounts ?? 0, unit: "个" },
      { k: "账号池额度", v: money(q.total_quota, data.currency), sub: `已用 ${money(q.used, data.currency)}` },
      { k: "今日", v: `${(t.today || {}).images ?? 0} 张`, sub: money((t.today || {}).cost, data.currency) },
    ];
    $("kpis").innerHTML = cards.map((card) => `
      <div class="kpi ${card.cls || ""}">
        <div class="k">${esc(card.k)}</div>
        <div class="v">${esc(card.v)}${card.unit ? `<small>${esc(card.unit)}</small>` : ""}</div>
        ${card.sub ? `<div class="k" style="margin-top:3px">${esc(card.sub)}</div>` : ""}
      </div>`).join("");
  }

  function quotaCell(row) {
    if (!Number(row.quota || 0)) return `<span class="hint">不限</span>`;
    const pct = Math.min(100, Number(row.quota_pct || 0));
    const cls = pct >= 100 ? "bad" : pct >= 80 ? "warn" : "";
    return `<div style="display:grid;gap:3px;min-width:90px">
      <div class="quota-bar ${cls}"><i style="width:${pct}%"></i></div>
      <span class="hint">剩 ${money(row.quota_left)} / ${money(row.quota)}</span></div>`;
  }

  function renderAccounts(data) {
    const rows = data.accounts || [];
    $("accountsHint").textContent =
      `${rows.length} 个账号 · 账号表 ${data.account_source === "feishu" ? "飞书" : "本地"} · `
      + `用量 ${data.usage_source === "feishu" ? "飞书记录" : "本地账本"} · 价目 ${data.pricing_source === "feishu" ? "飞书" : "pricing.json"}`;
    $("accountRows").innerHTML = rows.map((row) => {
      const off = !row.active;
      const legacy = row.role === "unknown";
      const roleCls = row.role === "admin" ? "admin" : "";
      const ops = legacy
        ? '<span class="hint">历史账号</span>'
        : `<button class="btn btn-quiet btn-sm" data-act="role">${row.role === "admin" ? "降为成员" : "设为管理员"}</button>
           <button class="btn btn-quiet btn-sm" data-act="toggle">${off ? "启用" : "停用"}</button>
           <button class="btn btn-quiet btn-sm" data-act="passwd">改密码</button>
           <button class="btn btn-quiet btn-sm" data-act="del">删除</button>`;
      return `<tr data-user="${esc(row.username)}">
        <td><code>${esc(row.username)}</code>${legacy ? ' <span class="badge-role">历史</span>' : ""}</td>
        <td title="${esc(row.display || row.username)}">${esc(row.display || row.username)}</td>
        <td><span class="badge-role ${roleCls}">${row.role === "admin" ? "管理员" : legacy ? "—" : "成员"}</span></td>
        <td><span class="badge-role ${off ? "off" : ""}">${off ? "停用" : "启用"}</span></td>
        <td class="num">
          <input type="number" step="0.01" min="0" value="${Number(row.quota || 0)}" data-act="quota" ${legacy ? "disabled" : ""}/>
        </td>
        <td class="num">${money(row.cost)}</td>
        <td>${quotaCell(row)}</td>
        <td class="num">${row.images || 0}</td>
        <td class="num">${row.jobs || 0}</td>
        <td><div class="ops">${ops}</div></td>
      </tr>`;
    }).join("");

    $("accountRows").querySelectorAll("tr").forEach((tr) => {
      const username = tr.dataset.user;
      const row = rows.find((item) => item.username === username);
      if (!row || row.role === "unknown") return;
      tr.querySelector('[data-act="role"]').addEventListener("click", async () => {
        const nextRole = row.role === "admin" ? "member" : "admin";
        await patch(username, { role: nextRole });
      });
      tr.querySelector('[data-act="toggle"]').addEventListener("click", async () => {
        await patch(username, { status: row.active ? "停用" : "启用" });
      });
      tr.querySelector('[data-act="passwd"]').addEventListener("click", async () => {
        const pwd = prompt(`给 ${username} 设置新密码（至少 6 位）`);
        if (!pwd) return;
        try {
          await api(`/api/accounts/${encodeURIComponent(username)}/password`, {
            method: "POST", body: JSON.stringify({ password: pwd }),
          });
          toast("ok", `${username} 的密码已重置`);
        } catch (err) { toast("bad", err.message); }
      });
      tr.querySelector('[data-act="del"]').addEventListener("click", async () => {
        if (!confirm(`删除账号 ${username}？\n（飞书账号表里那一行会被删掉，历史记录保留）`)) return;
        try {
          await api(`/api/accounts/${encodeURIComponent(username)}`, { method: "DELETE" });
          toast("ok", `已删除 ${username}`);
          await load();
        } catch (err) { toast("bad", err.message); }
      });
      const quotaInput = tr.querySelector('[data-act="quota"]');
      quotaInput.addEventListener("change", async () => {
        await patch(username, { quota: Number(quotaInput.value || 0) });
      });
    });
  }

  async function patch(username, body) {
    try {
      await api(`/api/accounts/${encodeURIComponent(username)}`, {
        method: "PATCH", body: JSON.stringify(body),
      });
      toast("ok", "已保存");
      await load();
    } catch (err) { toast("bad", err.message); }
  }

  function bars(target, rows, labelKey, valueKey) {
    const max = Math.max(1, ...rows.map((row) => Number(row[valueKey] || 0)));
    if (!rows.length) {
      target.innerHTML = '<span class="hint">还没有数据</span>';
      return;
    }
    target.innerHTML = rows.map((row) => {
      const value = Number(row[valueKey] || 0);
      const pct = Math.max(2, Math.round((value / max) * 100));
      return `<div class="row">
        <span title="${esc(row[labelKey])}">${esc(row[labelKey] || "—")}</span>
        <span class="track"><i style="width:${pct}%"></i></span>
        <span class="amt">${value.toLocaleString("zh-CN")}${valueKey === "cost" ? "" : " 张"}</span>
      </div>`;
    }).join("");
  }

  function renderDetail(data) {
    const accounts = (data.accounts || []).filter((row) => row.role !== "unknown");
    bars($("byAccount"), accounts.map((row) => ({ label: row.display || row.username, images: row.images, cost: row.cost })), "label", "images");
    bars($("byModel"), (data.by_model || []).map((row) => ({ label: row.model, images: row.images, cost: row.cost })), "label", "images");
    $("usageHint").textContent = `总 ${(data.totals || {}).images || 0} 张 · ${money((data.totals || {}).cost, data.currency)}`;

    const days = data.by_day || [];
    const max = Math.max(1, ...days.map((row) => Number(row.images || 0)));
    $("byDay").innerHTML = days.length
      ? days.map((row) => {
        const h = Math.max(2, Math.round((Number(row.images || 0) / max) * 82));
        return `<div class="col" title="${esc(row.day)} · ${row.images} 张 · ${money(row.cost)}">
          <i style="height:${h}px"></i><em>${esc(String(row.day).slice(5))}</em></div>`;
      }).join("")
      : '<span class="hint">还没有数据</span>';

    $("recentRows").innerHTML = (data.recent || []).map((row) => `
      <tr>
        <td>${esc(when(row.at))}</td>
        <td>${esc(row.account || "")}</td>
        <td>${esc(row.detail || "")}</td>
        <td><code>${esc(row.model || "")}</code></td>
        <td class="num">${row.images || 0}</td>
        <td class="num">${money(row.cost)}</td>
        <td><code>${esc(row.job_id || "")}</code></td>
      </tr>`).join("") || '<tr><td colspan="7" class="hint">还没有记录</td></tr>';
  }

  /* ------------------------------------------------------------------ 数据 */
  async function load() {
    if (state.busy) return;
    state.busy = true;
    try {
      state.data = await api("/api/admin/overview");
      state.currency = state.data.currency || "CNY";
      renderKpis(state.data);
      renderAccounts(state.data);
      renderDetail(state.data);
      $("subLine").textContent =
        `账号表：${state.data.account_source === "feishu" ? "飞书" : "本地"} · `
        + `用量：${state.data.usage_source === "feishu" ? "飞书记录" : "本地账本"} · `
        + `价目：${state.data.pricing_source === "feishu" ? "飞书定价表" : "pricing.json"}`;
    } catch (err) {
      if (!/未登录|管理员权限/.test(err.message)) toast("bad", err.message);
    } finally {
      state.busy = false;
    }
  }

  async function loadSession() {
    try {
      const data = await api("/api/session");
      if (!data || !data.authenticated) {
        location.href = "/login?next=%2Fadmin";
        return;
      }
      state.session = data;
      $("whoName").textContent = data.display || data.account;
      $("whoChip").title = `${data.display || data.account} · 管理员`;
    } catch (_) { /* ignore */ }
  }

  async function logout() {
    try { await api("/api/logout", { method: "POST" }); } catch (_) { /* ignore */ }
    location.href = "/login";
  }

  function bind() {
    $("logoutBtn").addEventListener("click", logout);
    $("reloadBtn").addEventListener("click", load);
    $("createForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      const body = {
        username: String(form.get("username") || "").trim(),
        password: String(form.get("password") || ""),
        display: String(form.get("display") || "").trim(),
        role: String(form.get("role") || "member"),
        quota: Number(form.get("quota") || 0),
        note: String(form.get("note") || "").trim(),
        status: "启用",
      };
      try {
        await api("/api/accounts", { method: "POST", body: JSON.stringify(body) });
        toast("ok", `已创建 ${body.username}`);
        event.target.reset();
        await load();
      } catch (err) { toast("bad", err.message); }
    });
  }

  (async function boot() {
    await loadSession();
    bind();
    await load();
  })();
})();
