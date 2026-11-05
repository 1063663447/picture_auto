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
    // 与工作台一致：金额保留三位小数（0.038 / 0.150 / 15.000）
    return `${unit}${num.toFixed(3)}`;
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
    const today = t.today || {};
    const yesterday = t.yesterday || {};
    const todayImages = Number(today.images || 0);
    const yImages = Number(yesterday.images || 0);
    let delta = "";
    if (yImages > 0) {
      const pct = Math.round(((todayImages - yImages) / yImages) * 100);
      delta = `较昨日 ${pct >= 0 ? "+" : ""}${pct}%`;
    } else if (todayImages > 0) {
      delta = "昨日 0 张";
    }
    const cards = [
      { k: "总图片", v: t.images ?? 0, unit: "张", cls: "ok", sub: `任务 ${t.jobs ?? 0} 个` },
      { k: "总花费", v: money(t.cost, data.currency), cls: "ok" },
      { k: "账号数", v: t.accounts ?? 0, unit: "个", sub: `管理员 + 成员` },
      { k: "账号池额度", v: money(q.total_quota, data.currency), sub: `已用 ${money(q.used, data.currency)}` },
      {
        k: "今日",
        v: `${todayImages} 张`,
        sub: `${money(today.cost, data.currency)}${delta ? ` · ${delta}` : ""}`,
      },
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

  /* --------------------------------------------------------- 图表（7.6） */
  const C = { images: "#8f7dff", cost: "#4fd6a3", grid: "#ffffff12", axis: "#8f88a8" };

  function niceMax(value) {
    if (!value || value <= 0) return 1;
    const pow = Math.pow(10, Math.floor(Math.log10(value)));
    const norm = value / pow;
    // 1 / 2 / 2.5 / 5 / 10：206 张的轴顶就是 250，不会一跳就 500
    const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
    const raw = step * pow;
    return raw >= 10 ? Math.round(raw) : Math.round(raw * 10) / 10;
  }

  function tipOf(host) {
    let tip = host.querySelector(".tip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "tip";
      host.appendChild(tip);
    }
    return tip;
  }

  function showTip(host, html, px, py) {
    const tip = tipOf(host);
    tip.innerHTML = html;
    tip.classList.add("on");
    const box = host.getBoundingClientRect();
    const width = tip.offsetWidth || 120;
    const left = Math.min(Math.max(px, width / 2 + 4), box.width - width / 2 - 4);
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(py, 26)}px`;
  }

  function hideTip(host) {
    const tip = host.querySelector(".tip");
    if (tip) tip.classList.remove("on");
  }

  function emptyChart(host) {
    host.innerHTML = '<div class="empty-chart">还没有数据</div>';
  }

  /** 用量趋势：面积+折线（张数，左轴）配花费虚线（右轴）。 */
  function renderTrend(host, days, currency) {
    if (!days || !days.length) return emptyChart(host);
    const W = 760, H = 250, P = { t: 16, r: 52, b: 26, l: 42 };
    const iw = W - P.l - P.r, ih = H - P.t - P.b;
    const n = days.length;
    const maxImages = niceMax(Math.max(...days.map((d) => Number(d.images || 0))));
    const maxCost = niceMax(Math.max(...days.map((d) => Number(d.cost || 0))));
    const xOf = (i) => P.l + (n === 1 ? iw / 2 : (iw * i) / (n - 1));
    const yA = (v) => P.t + ih - (Math.max(0, v) / maxImages) * ih;
    const yB = (v) => P.t + ih - (Math.max(0, v) / maxCost) * ih;

    const grid = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
      const y = P.t + ih - ih * ratio;
      const left = Math.round(maxImages * ratio);
      const right = (maxCost * ratio).toFixed(maxCost < 10 ? 1 : 0);
      return `<line class="c-grid" x1="${P.l}" y1="${y.toFixed(1)}" x2="${W - P.r}" y2="${y.toFixed(1)}" />
        <text class="c-text" x="${P.l - 8}" y="${(y + 3.5).toFixed(1)}" text-anchor="end">${left}</text>
        <text class="c-text cost" x="${W - P.r + 8}" y="${(y + 3.5).toFixed(1)}">${right}</text>`;
    }).join("");

    const lineA = days.map((d, i) => `${i ? "L" : "M"}${xOf(i).toFixed(1)},${yA(Number(d.images || 0)).toFixed(1)}`).join(" ");
    const lineB = days.map((d, i) => `${i ? "L" : "M"}${xOf(i).toFixed(1)},${yB(Number(d.cost || 0)).toFixed(1)}`).join(" ");
    const base = P.t + ih;
    const area = `${lineA} L${xOf(n - 1).toFixed(1)},${base} L${xOf(0).toFixed(1)},${base} Z`;
    const dots = days.map((d, i) => `
      <circle class="c-dot-a" cx="${xOf(i).toFixed(1)}" cy="${yA(Number(d.images || 0)).toFixed(1)}" r="2.8" />
      <circle class="c-dot-b" cx="${xOf(i).toFixed(1)}" cy="${yB(Number(d.cost || 0)).toFixed(1)}" r="2.2" />`).join("");
    const ticks = days.map((d, i) => {
      const every = n > 9 ? 2 : 1;
      if (i % every !== 0 && i !== n - 1) return "";
      return `<text class="c-text" x="${xOf(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${esc(String(d.day).slice(5))}</text>`;
    }).join("");
    const cursor = `<line class="c-cursor" x1="0" y1="${P.t}" x2="0" y2="${base}" opacity="0" />`;

    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="近 ${n} 天用量趋势">
      <defs>
        <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${C.images}" stop-opacity="0.38" />
          <stop offset="100%" stop-color="${C.images}" stop-opacity="0" />
        </linearGradient>
      </defs>
      ${grid}
      <path class="c-area" d="${area}" />
      <path class="c-line-a" d="${lineA}" />
      <path class="c-line-b" d="${lineB}" />
      ${dots}
      ${ticks}
      ${cursor}
    </svg>`;

    const svg = host.querySelector("svg");
    const line = host.querySelector(".c-cursor");
    svg.addEventListener("mousemove", (event) => {
      const rect = svg.getBoundingClientRect();
      const vx = ((event.clientX - rect.left) / rect.width) * W;
      const ratio = (vx - P.l) / iw;
      const index = Math.max(0, Math.min(n - 1, Math.round(ratio * (n - 1))));
      const day = days[index];
      line.setAttribute("x1", xOf(index));
      line.setAttribute("x2", xOf(index));
      line.setAttribute("opacity", "1");
      showTip(
        host,
        `<b>${esc(day.day)}</b><br/>${day.images || 0} 张 · ${money(day.cost, currency)}`,
        (xOf(index) / W) * rect.width,
        (yA(Number(day.images || 0)) / H) * rect.height,
      );
    });
    svg.addEventListener("mouseleave", () => {
      line.setAttribute("opacity", "0");
      hideTip(host);
    });
  }

  /** 今日每小时：竖向柱状图（只画有数据的时段更密，但 X 轴固定 0-23 才直观）。 */
  function renderHourBars(host, hours, currency) {
    const rows = (hours || []).slice(0, 24);
    if (!rows.length) return emptyChart(host);
    const total = rows.reduce((sum, row) => sum + Number(row.images || 0), 0);
    const W = 520, H = 200, P = { t: 12, r: 10, b: 24, l: 26 };
    const iw = W - P.l - P.r, ih = H - P.t - P.b;
    const max = niceMax(Math.max(1, ...rows.map((row) => Number(row.images || 0))));
    const step = iw / rows.length;
    const barW = Math.max(4, step * 0.62);
    const bars = rows.map((row, i) => {
      const value = Number(row.images || 0);
      const h = value > 0 ? Math.max(2, (value / max) * ih) : 0;
      const x = P.l + step * i;
      const y = P.t + ih - h;
      const bx = (x + (step - barW) / 2).toFixed(1);
      return `<g class="hcol" data-i="${i}">
        <rect class="c-hit" x="${bx}" y="${P.t}" width="${barW.toFixed(1)}" height="${ih}" />
        ${h ? `<rect class="c-bar" x="${bx}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}"
          height="${h.toFixed(1)}" rx="2.5" />` : ""}
      </g>`;
    }).join("");
    const ticks = rows.map((row, i) => {
      if (i % 4 !== 0 && i !== rows.length - 1) return "";
      return `<text class="c-text" x="${(P.l + step * i + step / 2).toFixed(1)}" y="${H - 8}"
        text-anchor="middle" font-size="9">${String(row.hour).padStart(2, "0")}</text>`;
    }).join("");
    const axis = `<text class="c-text" x="${P.l - 8}" y="${P.t + 8}" text-anchor="end" font-size="9">${max}</text>
      <text class="c-text" x="${P.l - 8}" y="${P.t + ih}" text-anchor="end" font-size="9">0</text>
      <line class="c-grid" x1="${P.l}" y1="${P.t + ih}" x2="${W - P.r}" y2="${P.t + ih}" />`;

    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="今日每小时产出">${bars}${ticks}${axis}</svg>`;
    host.querySelectorAll(".hcol").forEach((node) => {
      node.addEventListener("mouseenter", (event) => {
        const row = rows[Number(node.dataset.i)];
        const rect = host.getBoundingClientRect();
        showTip(
          host,
          `<b>${String(row.hour).padStart(2, "0")}:00</b><br/>${row.images || 0} 张 · ${money(row.cost, currency)}`,
          event.clientX - rect.left,
          event.clientY - rect.top,
        );
      });
      node.addEventListener("mouseleave", () => hideTip(host));
    });
    return total;
  }

  /** 分类排行：横向柱状图（HTML 版，中文标签不会被 SVG 缩放成蚂蚁字）。 */
  function hbars(host, rows, labelOf, maxRows = 8) {
    const list = (rows || []).slice(0, maxRows);
    if (!list.length) return emptyChart(host);
    const max = Math.max(1, ...list.map((row) => Number(row.images || 0)));
    host.innerHTML = list.map((row) => {
      const value = Number(row.images || 0);
      const pct = Math.max(1.5, (value / max) * 100);
      const label = labelOf(row);
      return `<div class="hbar" title="${esc(label)} · ${value} 张 · ${money(row.cost)}">
        <span class="hbar-label">${esc(label)}</span>
        <span class="hbar-track"><i style="width:${pct.toFixed(1)}%"></i></span>
        <span class="hbar-value">${value}<em> 张</em></span>
        <span class="hbar-cost">${money(row.cost)}</span>
      </div>`;
    }).join("");
  }

  function renderDetail(data) {
    const accounts = (data.accounts || []).filter((row) => row.role !== "unknown");
    bars($("byAccount"), accounts.map((row) => ({ label: row.display || row.username, images: row.images, cost: row.cost })), "label", "images");
    $("usageHint").textContent = `总 ${(data.totals || {}).images || 0} 张 · ${money((data.totals || {}).cost, data.currency)}`;

    const days = data.by_day || [];
    renderTrend($("trendChart"), days, data.currency);

    const hours = data.by_hour || [];
    renderHourBars($("hourChart"), hours, data.currency);
    const hourTotal = hours.reduce((sum, row) => sum + Number(row.images || 0), 0);
    $("hourHint").textContent = `今天 ${hourTotal} 张 · 峰值 ${
      hours.reduce((best, row) => (Number(row.images || 0) > Number(best.images || 0) ? row : best), hours[0] || { hour: 0, images: 0 }).images || 0
    } 张/时`;

    hbars($("styleChart"), data.by_style || [], (row) => row.style || "—");
    hbars($("colorChart"), data.by_color || [], (row) => row.color || "—");
    hbars($("modelChart"), data.by_model || [], (row) => row.model || "—", 6);

    $("recentRows").innerHTML = (data.recent || []).map((row) => `
      <tr>
        <td>${esc(when(row.at))}</td>
        <td>${esc(row.account || "")}</td>
        <td>${esc(row.garment || "—")}</td>
        <td>${esc(row.detail || "")}</td>
        <td><code>${esc(row.model || "")}</code></td>
        <td class="num">${row.images || 0}</td>
        <td class="num">${money(row.cost)}</td>
        <td><code>${esc(row.job_id || "")}</code></td>
      </tr>`).join("") || '<tr><td colspan="8" class="hint">还没有记录</td></tr>';
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
