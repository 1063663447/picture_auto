/* 服装改款换色工作台 · 前端 4.0
   - 模型下拉：网关全部图片模型 + 单价，无分类
   - 预计花费 + 用量芯片（今日/累计）
   - harness：DeepSeek agent（看图 / 查价 / 直接开工），SSE 流式 + 打字机
   - 成品：单张下载 / 改图 / 重试，批量选择 / 导出
*/
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TERMINAL = ["done", "partial", "failed", "canceled"];
  const STATUS = {
    pending: "等待", queued: "排队中", running: "生成中", done: "完成",
    failed: "失败", skipped: "跳过", canceled: "取消",
  };
  const TOOL_LABEL = {
    list_image_models: "查询模型与价格",
    inspect_images: "查看这批图片",
    estimate_cost: "估算花费",
    submit_generation: "提交生成任务",
    submit_rework: "提交返工任务",
    job_status: "查询任务进度",
    get_prompt: "读取当前提示词",
    set_requirements: "记录本次要求",
  };
  const QUICK_INTAKE = [
    "图案必须原样不动",
    "背景别动",
    "整体亮度提高一点",
    "这图能改吗？大概多少钱",
  ];
  const QUICK_REWORK = [
    "图案改小 10%",
    "两行文字间距收紧",
    "去掉抽绳",
    "整体提亮一档",
  ];

  const state = {
    skill: null,
    providers: [],
    ui: {},
    defaults: {},
    pricing: { currency: "USD", byId: {}, raw: {}, tierMp: { "1K": 1.6, "2K": 4.2 } },
    modelRows: [],
    streamedText: "",
    usage: null,
    // 多款式：每项 { slug, colors:Set<label> }；默认一个款式且全色选中
    styles: [],
    files: [],
    jobId: null,
    pollTimer: null,
    busy: false,
    job: null,
    selected: new Set(),
    harnessMode: null,
    harnessBusy: false,
    draftId: "",
    reworkTargets: [],
    reworkSourceJob: null,
    composedPrompt: "",
    requirements: "",
    streamBubble: null,
    session: null,
    syncingJob: "",
    pollStartedAt: 0,
    pricingAt: 0,
    accountsData: null,
    feishuRecords: [],
    feishuSelected: new Set(),
    cards: new Map(),
    cardsJob: "",
    logSig: "",
    pendingText: "",
    typerTimer: null,
    pendingBrief: "",
    view: "gallery",
    harnessHistory: [],
    assistantText: "",
    typingEl: null,
    toolRows: new Map(),
  };

  /* --------------------------------------------------------------- helpers */
  function store(key, value) {
    try { localStorage.setItem(`recolor.${key}`, JSON.stringify(value)); } catch (_) {}
  }
  function restore(key, fallback) {
    try {
      const raw = localStorage.getItem(`recolor.${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (_) { return fallback; }
  }
  function setSelect(id, value) {
    const el = $(id);
    if (!el) return;
    const exists = Array.from(el.options).some((option) => option.value === value);
    el.value = exists ? value : (el.options[0] ? el.options[0].value : "");
  }
  // 界面已收起的选项：有元素就取元素值，没有就用固定默认值
  function fixedValue(id, fallback) {
    const el = $(id);
    return el ? el.value : fallback;
  }
  function esc(text) {
    return String(text).replace(/[&<>"']/g, (ch) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
  }
  function fmtTime(ts) {
    return new Date((ts || 0) * 1000).toTimeString().slice(0, 8);
  }
  function money(value, currency) {
    if (value === null || value === undefined) return "未定价";
    const symbol = currency === "USD" ? "$" : (currency === "CNY" ? "¥" : "");
    // 报价一律保留三位小数（0.150 / 0.038 / 15.000），避免 0.15 和 0.150 看起来像两个价
    return `${symbol}${Number(value).toFixed(3)}`;
  }
  function tierFor(size) {
    const limits = state.pricing.tierMp || { "1K": 1.6, "2K": 4.2 };
    const match = /^(\d+)x(\d+)$/i.exec(size || "");
    if (!match) return "1K";
    const megapixels = (Number(match[1]) * Number(match[2])) / 1e6;
    if (megapixels <= Number(limits["1K"] || 1.6)) return "1K";
    if (megapixels <= Number(limits["2K"] || 4.2)) return "2K";
    return "4K";
  }
  function megapixelsOf(size) {
    const match = /^(\d+)x(\d+)$/i.exec(size || "");
    if (!match) return 0;
    return (Number(match[1]) * Number(match[2])) / 1e6;
  }

  // 比例 × 档位 → 实际请求的尺寸（档位只决定像素，比例只决定横竖）
  const SIZE_MATRIX = {
    "1:1": { "1K": "1024x1024", "2K": "2048x2048", "4K": "3072x3072" },
    "3:4": { "1K": "1024x1536", "2K": "1536x2048", "4K": "3072x4096" },
    "4:3": { "1K": "1536x1024", "2K": "2048x1536", "4K": "4096x3072" },
  };

  function currentAspect() {
    return $("aspect") ? $("aspect").value : "auto";
  }
  // 图案策略：界面已收起，空字符串 = 让后端按需求自动判断
  function currentPatternMode() {
    return $("patternMode") ? $("patternMode").value : "";
  }
  // 下载一律给原图（不再做压缩版）
  function currentDownloadFmt() {
    return "png";
  }
  function currentOutputFormat() {
    return fixedValue("outputFormat", "png");
  }

  // 下载链接：始终是 API 返回的原图（PNG/JPG/WEBP 由出图接口决定）
  function downloadUrl(jobId, taskId) {
    return `/api/jobs/${jobId}/image/${encodeURIComponent(taskId)}?download=true`;
  }

  function renderDownloadHint() { /* 已从界面移除：固定原图下载 */ }

  function renderPatternHint() { /* 已从界面移除：图案策略按需求自动判断 */ }
  function currentTierChoice() {
    return $("sizeTier") ? $("sizeTier").value : "1K";
  }
  function currentSize() {
    const aspect = currentAspect();
    if (aspect === "auto") return "auto";
    const row = SIZE_MATRIX[aspect] || SIZE_MATRIX["1:1"];
    return row[currentTierChoice()] || "auto";
  }
  // 旧版只有「尺寸」下拉时存的是 2048x2048 这类值，开学时映射回 比例 + 档位
  function applyLegacySize(value) {
    if (!$("aspect") || !$("sizeTier")) return;
    const match = /^(\d+)x(\d+)$/i.exec(String(value || ""));
    if (!match) {
      $("aspect").value = "auto";
      return;
    }
    const width = Number(match[1]);
    const height = Number(match[2]);
    const aspect = width === height ? "1:1" : (width < height ? "3:4" : "4:3");
    const tier = tierFor(`${width}x${height}`);
    if ([...$("aspect").options].some((o) => o.value === aspect)) $("aspect").value = aspect;
    if ([...$("sizeTier").options].some((o) => o.value === tier)) $("sizeTier").value = tier;
  }

  function priceOf(model, size) {
    // 同一个模型名在不同网关售价不一样（实测差十几倍）：接口带了覆盖价就先用它
    const provider = state.providers.find((p) => p.name === ($("provider") || {}).value);
    const override = provider && provider.model_prices && provider.model_prices[model];
    if (override !== undefined && override !== null) {
      return {
        unit: Number(override), tier: "", tiered: false, fallback: false,
        tiers: {}, other: null, override: true,
      };
    }
    const entry = (state.pricing.raw || {})[model];
    if (entry === null || entry === undefined) {
      return { unit: null, tier: "", tiered: false, fallback: false, tiers: {}, other: null };
    }
    if (typeof entry === "number") {
      return { unit: entry, tier: "", tiered: false, fallback: false, tiers: {}, other: null };
    }
    const tier = tierFor(size);
    const tiers = entry.tiers || {};
    const hit = tiers[tier];
    const unit = hit !== undefined && hit !== null ? hit : entry.other;
    return {
      unit: unit === undefined ? null : unit,
      tier,
      tiered: true,
      fallback: hit === undefined || hit === null,
      tiers,
      other: entry.other === undefined ? null : entry.other,
    };
  }

  function priceText(model, size) {
    const info = priceOf(model, size);
    if (info.unit === null) return "未定价";
    const suffix = info.tiered ? (info.fallback ? "/其他" : "/" + info.tier) : "/张";
    return `${money(info.unit, state.pricing.currency)}${suffix}`;
  }

  // 「分档价：1K ¥0.045 · 2K ¥0.065 · 4K ¥0.095 · 其他 ¥0.095」
  function tiersText(info) {
    if (!info.tiered) return "";
    const parts = [];
    for (const tier of ["1K", "2K", "4K"]) {
      if (info.tiers[tier] !== undefined && info.tiers[tier] !== null) {
        parts.push(`${tier} ${money(info.tiers[tier], state.pricing.currency)}`);
      }
    }
    if (info.other !== null && info.other !== undefined) {
      parts.push(`其他 ${money(info.other, state.pricing.currency)}`);
    }
    return parts.join(" · ");
  }

  function icon(id, size = 15) {
    return `<svg width="${size}" height="${size}" aria-hidden="true"><use href="#${id}"/></svg>`;
  }
  /* 全局请求反馈：本机接口通常 10~30ms，所以只有「明显卡住」时才亮进度条，
     既不闪也不误报；慢请求（飞书、打包、拉模型）会一直亮着。 */
  let netPending = 0;
  let netShowTimer = null;
  let netHideTimer = null;
  function netStart() {
    netPending += 1;
    if (netPending > 1) return;
    clearTimeout(netHideTimer);
    clearTimeout(netShowTimer);
    netShowTimer = setTimeout(() => {
      const bar = $("netbar");
      if (bar && netPending > 0) bar.classList.add("on");
    }, 180);
  }
  function netEnd() {
    netPending = Math.max(0, netPending - 1);
    if (netPending > 0) return;
    clearTimeout(netShowTimer);
    netHideTimer = setTimeout(() => {
      const bar = $("netbar");
      if (bar) bar.classList.remove("on");
    }, 260);
  }

  async function api(path, options) {
    netStart();
    try {
      const res = await fetch(path, options);
      let payload = null;
      try { payload = await res.json(); } catch (_) { /* ignore */ }
      if (res.status === 401 && payload && payload.login_required) {
        location.href = "/login";
        throw new Error("登录已过期，正在跳转登录…");
      }
      if (!res.ok || (payload && payload.ok === false)) {
        throw new Error((payload && payload.error) || `请求失败 HTTP ${res.status}`);
      }
      return payload;
    } finally {
      netEnd();
    }
  }

  /* 按钮级反馈：转圈 + 禁用 + 文案，结束后原样恢复 */
  async function withLoading(btn, task, label) {
    if (!btn) return task();
    const original = btn.innerHTML;
    btn.classList.add("loading");
    btn.disabled = true;
    if (label) btn.textContent = label;
    try {
      return await task();
    } finally {
      btn.classList.remove("loading");
      btn.innerHTML = original;
      btn.disabled = false;
    }
  }

  function skeletonHTML(rows = 3, height = 34) {
    return Array.from({ length: rows },
      (_, i) => `<div class="sk" style="height:${height}px;--d:${i * 90}ms"></div>`).join("");
  }
  function toast(kind, message) {
    const el = document.createElement("div");
    const life = kind === "bad" ? 6000 : 3200;
    el.className = `toast ${kind}`;
    el.style.setProperty("--life", `${life}ms`);
    el.innerHTML = `${icon(kind === "bad" ? "i-alert" : "i-check")}<span>${esc(message)}</span>`;
    el.addEventListener("click", () => el.remove());
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), life);
  }

  // 数字滚动（KPI 用；尊重 prefers-reduced-motion）
  function animateNumber(el, to, formatter) {
    if (!el) return;
    const target = Number(to) || 0;
    const from = Number(el.dataset.value || 0);
    if (from === target) {                      // 没变化就别抖，避免每次轮询都在闪
      el.textContent = formatter ? formatter(target) : String(target);
      return;
    }
    el.dataset.value = String(target);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.classList.remove("bump");
    void el.offsetWidth;                        // 重置动画
    el.classList.add("bump");
    if (reduce) {
      el.textContent = formatter ? formatter(target) : String(target);
      return;
    }
    const start = performance.now();
    const duration = 420;
    function frame(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const value = from + (target - from) * eased;
      el.textContent = formatter ? formatter(value) : String(Math.round(value));
      if (t < 1) requestAnimationFrame(frame);
      else el.textContent = formatter ? formatter(target) : String(target);
    }
    requestAnimationFrame(frame);
  }

  /* --------------------------------------------------------------- 账号 */
  async function loadSession() {
    state.session = null;
    try {
      const data = await api("/api/session");
      if (!data || !data.authenticated) return;
      state.session = data;
      $("whoChip").hidden = false;
      $("whoName").textContent = data.display || data.account;
      $("whoChip").title = `${data.display || data.account} · ${data.role === "admin" ? "管理员" : "成员"}`
        + (data.source === "feishu" ? " · 账号来自飞书表" : " · 本地兜底账号");
      $("accountsBtn").hidden = !data.admin;
      $("accountsBtn").title = data.admin ? "查看每个账号的用量" : "";
      $("adminLink").hidden = !data.admin;
    } catch (_) { /* 口令访问或接口不可用时静默 */ }
  }

  async function logout() {
    try { await api("/api/logout", { method: "POST" }); } catch (_) { /* ignore */ }
    location.href = "/login";
  }

  async function loadAccounts() {
    const list = $("accountsList");
    list.innerHTML = skeletonHTML(3, 64);
    $("accountsTotal").textContent = "读取中…";
    try {
      const data = await api("/api/accounts");
      state.accountsData = data;
      renderAccounts(data);
    } catch (err) {
      list.innerHTML = `<div class="bubble muted">${icon("i-alert")}<div>读取失败：${esc(err.message)}</div></div>`;
      $("accountsTotal").textContent = "读取失败";
    }
  }

  function renderAccounts(data) {
    const list = $("accountsList");
    const rows = (data.accounts || []).slice();
    const currency = data.currency || state.pricing.currency;
    $("accountsSub").textContent = (data.source === "feishu"
      ? "账号来自飞书「账号」表 · "
      : "本地兜底账号 · ")
      + (data.usage_source === "feishu" ? "用量取自飞书记录" : "用量取自本地账本");
    const totalImages = rows.reduce((sum, row) => sum + Number(row.images || 0), 0);
    const totalCost = rows.reduce((sum, row) => sum + Number(row.cost || 0), 0);
    $("accountsTotal").textContent = `${rows.length} 个账号 · 合计 ${totalImages} 张 · ${money(totalCost, currency)}`;
    if (!rows.length) {
      list.innerHTML = `<div class="bubble muted">${icon("i-users")}<div>还没有账号记录</div></div>`;
      return;
    }
    list.innerHTML = rows.map((row) => {
      const recent = (row.recent || []).slice(0, 3).map((item) => `
        <li><span>${esc(item.detail || item.job_id || "")}</span>
        <em>${item.images} 张 · ${money(item.cost, currency)}</em></li>`).join("");
      const role = row.role === "admin" ? "管理员" : (row.role === "unknown" ? "历史账号" : "成员");
      const quota = Number(row.quota || 0) > 0
        ? `<span class="tag ${Number(row.quota_pct || 0) >= 100 ? "bad" : Number(row.quota_pct || 0) >= 80 ? "warn" : ""}">额度 ${money(row.quota_left, currency)} / ${money(row.quota, currency)}</span>`
        : "";
      return `<article class="acc-card">
        <header>
          <b>${esc(row.display || row.username)}</b>
          <span class="tag ${row.active ? "" : "warn"}">${row.active ? role : "已停用"}</span>
          <code>${esc(row.username)}</code>
          ${quota}
        </header>
        <div class="acc-kpi">
          <span><em>${row.images || 0}</em>张</span>
          <span><em>${money(row.cost, currency)}</em>花费</span>
          <span><em>${row.jobs || 0}</em>次调用</span>
          ${row.last_at ? `<span class="hint">最近 ${new Date(row.last_at * 1000).toLocaleString("zh-CN")}</span>` : ""}
        </div>
        ${recent ? `<ul class="acc-recent">${recent}</ul>` : ""}
      </article>`;
    }).join("");
  }

  function showAccounts() {
    $("accountsScrim").hidden = false;
    $("accountsDrawer").hidden = false;
    requestAnimationFrame(() => {
      $("accountsScrim").classList.add("on");
      $("accountsDrawer").classList.add("on");
    });
    loadAccounts();
  }

  function hideAccounts() {
    closePanel("accountsDrawer", "accountsScrim");
  }

  /* ------------------------------------------------------------- bootstrap */
  async function boot() {
    await loadSession();
    try {
      const health = await api("/api/health");
      $("healthLine").textContent = `${health.types} 款 · ${health.providers_ui || health.providers} 接口`;
    } catch (_) { /* ignore */ }

    try {
      const skill = await api("/api/skill");
      state.skill = skill;
      if (skill.warnings && skill.warnings.length) {
        $("skillWarn").hidden = false;
        $("skillWarn").textContent = `解析提示 ${skill.warnings.length} 条`;
        $("skillWarn").title = skill.warnings.join("\n");
      }
      restoreStyles();
      renderStyles();
    } catch (err) {
      console.warn("skill 读取失败", err);
    }

    // 价格表必须先就位，否则模型下拉渲染时拿不到单价
    await loadPricing();

    try {
      const data = await api("/api/providers");
      state.providers = data.providers || [];
      state.ui = data.ui || {};
      state.defaults = data.defaults || {};
      renderProviders();
      restoreSettings();
      loadGate();
    } catch (_) { /* ignore */ }
    bind();
    loadHistory();
    refreshCacheInfo();
    const params = new URLSearchParams(location.search);
    if (params.get("denied") === "admin") {
      toast("bad", "管理后台只对管理员开放，请联系管理员开通");
    }
    const deep = params.get("job");
    if (deep) watchJob(deep);
    if (location.hash === "#logs") switchView("logs");
    window.addEventListener("hashchange", () => {
      if (location.hash === "#logs") switchView("logs");
      else if (location.hash === "#gallery") switchView("gallery");
    });
    updateSummary();
    if (params.get("panel") === "prompt") showPrompt();
  }

  /* ---------------------------------------------------------- styles 篇 */
  function allLabels(slug) {
    const type = state.skill && state.skill.types[slug];
    return type ? type.colors.map((c) => c.label) : [];
  }
  function styleName(slug) {
    const type = state.skill && state.skill.types[slug];
    return type ? type.display_name : slug;
  }
  function styleType(slug) {
    return (state.skill && state.skill.types[slug]) || null;
  }
  // 袖口工艺标：平口 = T 恤折边，螺纹 = 卫衣罗纹（画错是硬伤，所以摆在款式标题上）
  function cuffTag(type) {
    if (!type || !type.cuff_zh) return "";
    const plain = type.cuff === "plain";
    const tip = plain
      ? "袖口/下摆为平口折边（T 恤工艺），不是卫衣罗纹"
      : "袖口/下摆为罗纹带（卫衣工艺）";
    return ` <span class="cuff-tag ${plain ? "plain" : "ribbed"}" title="${esc(tip)}">${esc(type.cuff_zh)}</span>`;
  }

  // 恢复上次的选择；没有就用第一个款式、全色
  function restoreStyles() {
    const slugs = Object.keys(state.skill.types);
    const saved = restore("styles", null);
    const list = [];
    if (Array.isArray(saved)) {
      for (const item of saved) {
        const slug = String((item && item.slug) || "");
        if (!slugs.includes(slug)) continue;
        if (list.some((s) => s.slug === slug)) continue;
        const wanted = new Set((item.colors || []).filter((c) => allLabels(slug).includes(c)));
        list.push({ slug, colors: wanted.size ? wanted : new Set(allLabels(slug)) });
      }
    }
    if (!list.length) {
      // 兼容老版本的 garment 键
      const legacy = restore("garment", null);
      const slug = slugs.includes(legacy) ? legacy : slugs[0];
      list.push({ slug, colors: new Set(allLabels(slug)) });
    }
    state.styles = list;
  }

  function primaryStyle() {
    return state.styles[0] || { slug: "", colors: new Set() };
  }
  function totalColors() {
    return state.styles.reduce((sum, item) => sum + item.colors.size, 0);
  }
  function selectionsPayload() {
    return state.styles
      .filter((item) => item.colors.size)
      .map((item) => ({
        garment_type: item.slug,
        garment_name: styleName(item.slug),
        colors: [...item.colors],
      }));
  }

  function renderStyles() {
    const box = $("styleList");
    if (!box || !state.skill) return;
    const slugs = Object.keys(state.skill.types);
    box.innerHTML = state.styles.map((item, index) => {
      const type = styleType(item.slug);
      const colors = type ? type.colors : [];
      const options = slugs.map((slug) => {
        const t = state.skill.types[slug];
        const cuff = t.cuff_zh ? ` · ${t.cuff_zh}` : "";
        return `<option value="${esc(slug)}"${slug === item.slug ? " selected" : ""}>${esc(t.display_name)} · ${t.colors.length} 色${esc(cuff)}</option>`;
      }).join("");
      const chips = colors.map((color) => {
        const on = item.colors.has(color.label);
        // 有款式表效果图就显示效果图缩略（生图时会连同原图一起发出去），没有就退回落色块
        const marker = color.effect
          ? `<img class="chip-thumb" src="${esc(color.effect)}" alt="" loading="lazy"/>`
          : `<span class="dot" style="background:${esc(color.hex)}"></span>`;
        return `<button class="chip${on ? " sel" : ""}" type="button" data-color="${esc(color.label)}"
          title="${esc(color.name_zh)} ${esc(color.hex)}${color.effect ? " · 带效果图" : ""}" aria-pressed="${on}">
          ${marker}${esc(color.name_zh)}</button>`;
      }).join("");
      return `<div class="style-block" data-index="${index}">
        <div class="style-top">
          <label class="field style-field">
            <span class="lbl">款式 ${state.styles.length > 1 ? `#${index + 1}` : ""}
              <span class="style-tags"><span class="count">${colors.length} 色</span>${cuffTag(type)}</span></span>
            <select class="style-select" data-slug="${esc(item.slug)}" aria-label="款式 ${index + 1}">${options}</select>
          </label>
          <button class="style-drop" type="button" data-drop="${index}" title="移除这个款式"
            ${state.styles.length > 1 ? "" : "hidden"}><svg><use href="#i-x"/></svg></button>
        </div>
        <div class="style-colors">${chips}</div>
        <div class="style-foot">
          <span class="hint">已选 ${item.colors.size} / ${colors.length} 色</span>
          <span class="style-acts">
            <button class="tiny-button" type="button" data-pick="all">全选</button>
            <button class="tiny-button" type="button" data-pick="none">清空</button>
          </span>
        </div>
      </div>`;
    }).join("");
    updateSummary();
  }

  function addStyle() {
    if (!state.skill) return;
    const slugs = Object.keys(state.skill.types);
    const used = new Set(state.styles.map((s) => s.slug));
    const next = slugs.find((slug) => !used.has(slug)) || slugs[0];
    state.styles.push({ slug: next, colors: new Set(allLabels(next)) });
    saveSettings();
    renderStyles();
    const list = $("styleList");
    if (list && list.lastElementChild) {
      list.lastElementChild.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function setStyleSlug(index, slug) {
    const item = state.styles[index];
    if (!item || !styleType(slug)) return;
    // 同一款式不重复（已经是别的卡片的那款就换回来）
    const clash = state.styles.findIndex((s, i) => i !== index && s.slug === slug);
    if (clash >= 0) {
      state.styles[index].slug = state.styles[clash].slug === slug ? item.slug : slug;
    }
    item.slug = slug;
    item.colors = new Set(allLabels(slug));   // 换款式 → 默认全色
    saveSettings();
    renderStyles();
  }

  // 款式卡片上的一切交互（换款式 / 点色块 / 全选清空 / 删除）
  function bindStyleList() {
    const box = $("styleList");
    if (!box) return;
    box.addEventListener("change", (event) => {
      const select = event.target.closest(".style-select");
      if (!select) return;
      const card = select.closest(".style-block");
      setStyleSlug(Number(card.dataset.index), select.value);
    });
    box.addEventListener("click", (event) => {
      const card = event.target.closest(".style-block");
      if (!card) return;
      const index = Number(card.dataset.index);
      const chip = event.target.closest("[data-color]");
      if (chip) {
        event.preventDefault();
        toggleStyleColor(index, chip.dataset.color);
        return;
      }
      const pick = event.target.closest("[data-pick]");
      if (pick) {
        event.preventDefault();
        const item = state.styles[index];
        if (!item) return;
        item.colors = pick.dataset.pick === "all"
          ? new Set(allLabels(item.slug))
          : new Set();
        saveSettings();
        renderStyles();
        return;
      }
      const drop = event.target.closest("[data-drop]");
      if (drop && state.styles.length > 1) {
        event.preventDefault();
        state.styles.splice(index, 1);
        saveSettings();
        renderStyles();
      }
    });
  }

  function toggleStyleColor(index, label) {
    const item = state.styles[index];
    if (!item) return;
    if (item.colors.has(label)) item.colors.delete(label);
    else item.colors.add(label);
    saveSettings();
    renderStyles();
  }

  /* ------------------------------------------------------------- 全局阀门 */
  function renderGate(info) {
    const el = $("gateHint");
    if (!el) return;
    const cfg = (info && info.config) || {};
    if (state.providerUnlimited) {
      el.textContent = "该接口不排队 · 并发不限（一次全推）";
      el.dataset.state = "ok";
      el.title = "这个接口实测能扛高并发，所以不占飞书闸门的槽位，提交多少张就同时发多少张";
      return;
    }
    const cap = state.providerCap ? `接口上限 ${state.providerCap}` : "";
    const isQueue = (cfg.provider || "http") === "feishu";
    if (!cfg.enabled) {
      el.textContent = `排队：未启用（全推，靠接口自己限流）${cap ? " · " + cap : ""}`;
      el.dataset.state = "off";
      el.title = "没配闸门时不会排队，全部一起提交；想看到「前方还有几张」，配 defaults.gate 用飞书表当队列";
      return;
    }
    if (info.ok) {
      const queued = Number(info.queued || 0);
      const ahead = Number(info.ahead || 0);
      el.textContent = `${isQueue ? "队列" : "阀门"} ${info.in_use}/${info.limit}`
        + (queued ? ` · 排队 ${queued} 张` : "")
        + (ahead && isQueue ? ` · 前方还有 ${ahead} 个` : "")
        + (cap ? ` · ${cap}` : "");
      el.dataset.state = "ok";
      el.title = isQueue
        ? `飞书表当队列：正在跑 ${info.in_use}/${info.limit}，表里还有 ${queued} 张在排队；点一下刷新`
        : `全部电脑合计上限 ${info.limit}，当前占用 ${info.in_use}；点一下刷新`;
      return;
    }
    el.textContent = `队列离线${cap ? " · " + cap : ""}`;
    el.dataset.state = "bad";
    el.title = `${info.error || "连不上闸门"}（fail_open 时照常出图）`;
  }

  async function loadGate() {
    try {
      renderGate(await api("/api/gate"));
    } catch (err) {
      renderGate({ ok: false, enabled: true, error: err.message, config: { enabled: true } });
    }
  }

  function expectedCalls() {
    return state.files.length * totalColors();
  }

  function currentModel() {
    const value = $("model").value;
    if (value === "__manual__") return ($("modelManual").value || "").trim();
    return value;
  }

  function renderModelLabels() {
    renderPatternHint();
    renderDownloadHint();
    const size = currentSize();
    const tier = tierFor(size);
    const auto = currentAspect() === "auto";
    document.querySelectorAll("#model option[data-model]").forEach((option) => {
      const model = option.dataset.model;
      const price = priceText(model, size);
      option.textContent = `${model} · ${price}`;
    });
    const model = currentModel();

    // 档位下拉直接标出「这个模型的这一档多少钱」，避免只看一个统一单价
    if ($("sizeTier")) {
      for (const option of $("sizeTier").options) {
        const info = priceOf(model, SIZE_MATRIX["1:1"][option.value]);
        const label = { "1K": "1K · 1024 级", "2K": "2K · 2048 级", "4K": "4K · 3072 级" }[option.value] || option.value;
        option.textContent = info.unit === null
          ? `${label} · 未定价`
          : `${label} · ${money(info.unit, state.pricing.currency)}/张`;
      }
      $("sizeTier").disabled = auto;
      $("sizeTier").title = auto ? "比例为「自动」时由原图决定尺寸与档位；选具体比例后可手动选档" : "";
    }
    if ($("sizePreview")) {
      $("sizePreview").value = auto
        ? "auto · 跟随原图"
        : `${size.replace("x", " × ")} · ${megapixelsOf(size).toFixed(2)}MP · ${tier} 档`;
    }
  }

  function updateSummary() {
    const items = state.files.length;
    const colors = totalColors();
    const calls = expectedCalls();
    $("outputCount").textContent = state.skill
      ? `将产出 ${items * colors} 张成品（${items} 图 × ${colors} 色）· 全部一起提交，超额的自动排队`
      : "";

    const model = currentModel();
    const size = currentSize();
    const info = priceOf(model, size);
    const quality = $("quality").value;
    const line = $("estimateLine");
    if (!calls) {
      line.textContent = "预计花费 —";
      line.classList.remove("warn");
    } else if (info.unit === null) {
      line.textContent = `预计花费 — · ${model || "未选模型"} 未定价（在飞书「模型定价」表或 pricing.json 里填单价）`;
      line.classList.add("warn");
    } else {
      const tierText = info.tiered ? ` · ${info.fallback ? "其他档" : info.tier + " 档"}` : "";
      line.textContent =
        `预计花费 ${money(info.unit * calls, state.pricing.currency)} · ` +
        `${calls} 张 × ${money(info.unit, state.pricing.currency)}${tierText}` +
        `（${model} · ${size === "auto" ? "auto" : size} · ${quality}）`;
      line.classList.remove("warn");
    }
    $("startBtn").disabled = !(items > 0 && colors > 0) || state.busy;
  }

  /* ------------------------------------------------------------- providers */
  function renderProviders() {
    const select = $("provider");
    select.innerHTML = "";
    for (const provider of state.providers) {
      const option = document.createElement("option");
      option.value = provider.name;
      option.textContent = provider.label;
      select.appendChild(option);
    }
    const saved = restore("provider", null);
    const ready = state.providers.filter((p) => p.ready);
    const preferred = state.providers.find((p) => p.name === saved) || ready[0] || state.providers[0];
    if (preferred) select.value = preferred.name;
    select.addEventListener("change", () => {
      store("provider", select.value);
      applyProvider();
      loadModels(select.value);
      loadGate();
    });
    applyProvider();
  }

  function applyProvider() {
    const provider = state.providers.find((p) => p.name === $("provider").value);
    if (!provider) return;
    state.providerUnlimited = Boolean(provider.unlimited);
    state.providerCap = state.providerUnlimited ? 0 : Number(provider.max_concurrency || 20);
    $("providerState").textContent = provider.ready ? "就绪" : provider.reason;
    $("providerState").style.color = provider.ready ? "var(--primary)" : "var(--danger)";
    // 参考图与负面词策略固定，界面上不再展示说明
    loadModels(provider.name);
  }

  async function loadModels(providerName, refresh) {
    const select = $("model");
    // 慢的时候先给一个明确的「正在读取」，别让下拉长时间空白
    if (select && !select.options.length) {
      const stub = document.createElement("option");
      stub.value = "";
      stub.textContent = "正在读取模型…";
      select.appendChild(stub);
      select.disabled = true;
    }
    try {
      const data = await api(
        `/api/models?provider=${encodeURIComponent(providerName)}&refresh=${refresh ? "true" : "false"}`
      );
      const select = $("model");
      const previous = currentModel() || restore(`model.${providerName}`, "");
      select.innerHTML = "";
      const rows = (data.models || []).filter((m) => m.image);
      state.modelRows = rows;
      for (const model of rows) {
        const option = document.createElement("option");
        option.value = model.id;
        option.dataset.model = model.id;
        select.appendChild(option);
      }
      const manual = document.createElement("option");
      manual.value = "__manual__";
      manual.textContent = "其他（手填模型名）";
      select.appendChild(manual);

      const wanted = rows.find((m) => m.id === previous) ? previous : (rows[0] ? rows[0].id : "__manual__");
      select.value = wanted;
      select.disabled = false;
      $("modelManual").hidden = wanted !== "__manual__";
      renderModelLabels();
      store(`model.${providerName}`, wanted);
    } catch (err) {
      console.warn("模型拉取失败", err.message);
      const select = $("model");
      select.disabled = false;
      select.innerHTML = "";
      {
        const provider = state.providers.find((p) => p.name === $("provider").value) || {};
        const fallback = restore(`model.${$("provider").value}`, provider.model || "");
        if (fallback) {
          const option = document.createElement("option");
          option.value = fallback;
          option.dataset.model = fallback;
          select.appendChild(option);
        }
        const manual = document.createElement("option");
        manual.value = "__manual__";
        manual.textContent = "其他（手填模型名）";
        select.appendChild(manual);
        select.value = fallback || "__manual__";
        $("modelManual").hidden = select.value !== "__manual__";
        renderModelLabels();
      }
    }
    updateSummary();
  }

  async function loadPricing() {
    try {
      const data = await api("/api/pricing");
      state.pricing.payload = data;
      state.pricing.currency = data.currency || "USD";
      state.pricing.source = data.source || "file";
      state.pricing.raw = {};
      if (data.tier_mp) state.pricing.tierMp = data.tier_mp;
      for (const model of data.models || []) {
        state.pricing.byId[model.model] = { unit: model.unit, currency: data.currency || "USD" };
        if (model.tiers && Object.keys(model.tiers).length) {
          state.pricing.raw[model.model] = { tiers: model.tiers, other: model.unit };
        } else if (model.unit !== null && model.unit !== undefined) {
          state.pricing.raw[model.model] = model.unit;
        } else {
          state.pricing.raw[model.model] = null;
        }
      }
      state.usage = data.usage;
      state.pricingAt = Date.now();
      renderUsage();
      renderModelLabels();
      updateSummary();
    } catch (_) { /* ignore */ }
  }

  function renderUsage() {
    if (!state.usage) return;
    const today = state.usage.today || { images: 0, cost: 0 };
    const total = state.usage.total || { images: 0, cost: 0 };
    $("usageText").textContent =
      `今日 ${today.images} 张 · ${money(today.cost, state.usage.currency)}`;
    $("usageChip").title =
      `今日 ${today.images} 张 ${money(today.cost, state.usage.currency)} · ` +
      `累计 ${total.images} 张 ${money(total.cost, state.usage.currency)} · ` +
      `${state.usage.jobs} 个任务` +
      (state.usage.unpriced_images ? ` · ${state.usage.unpriced_images} 张未定价` : "") +
      (state.pricing.source === "feishu" ? " · 价格来自飞书「模型定价」表" : " · 价格来自 pricing.json");
  }

  /* ------------------------------------------------------------------ files */
  function addFiles(fileList) {
    for (const file of fileList) {
      if (!/^image\/(png|jpeg|webp)$/.test(file.type)) continue;
      state.files.push({ file, url: URL.createObjectURL(file), name: file.name });
    }
    renderThumbs();
  }

  function renderThumbs() {
    const box = $("thumbs");
    box.innerHTML = "";
    state.files.forEach((entry, index) => {
      const wrap = document.createElement("div");
      wrap.className = "thumb";
      wrap.innerHTML = `<img src="${entry.url}" alt=""/><span class="idx">${index + 1}</span>
        <button type="button" aria-label="移除第 ${index + 1} 张">×</button>`;
      wrap.querySelector("button").addEventListener("click", (event) => {
        event.preventDefault();
        state.files.splice(index, 1);
        state.draftId = "";
        renderThumbs();
      });
      box.appendChild(wrap);
    });
    $("fileCount").textContent = state.files.length ? `${state.files.length} 张` : "未选择";
    state.draftId = "";
    updateSummary();
  }

  /* --------------------------------------------------------------- settings */
  function restoreSettings() {
    // 比例 + 档位分开存；旧版本只存了 "size"（如 2048x2048），映射回来
    const savedAspect = restore("aspect", null);
    const savedTier = restore("sizeTier", null);
    if (savedAspect) {
      setSelect("aspect", savedAspect);
      setSelect("sizeTier", savedTier || "1K");
    } else {
      const legacy = restore("size", state.defaults.size || "auto");
      if (legacy && legacy !== "auto") {
        applyLegacySize(legacy);
      } else {
        setSelect("aspect", "auto");
        setSelect("sizeTier", "1K");
      }
    }
    setSelect("patternMode", restore("patternMode", state.defaults.pattern_mode || ""));
    setSelect("subject", restore("subject", state.defaults.subject || "auto"));
    setSelect("quality", state.defaults.quality || restore("quality", "high"));
    setSelect("outputFormat", restore("outputFormat", state.defaults.output_format || "png"));
    setSelect("downloadFmt", "png");
    renderModelLabels();
    updateSummary();
  }
  function saveSettings() {
    store("styles", state.styles.map((item) => ({
      slug: item.slug,
      colors: [...item.colors],
    })));
    store("aspect", currentAspect());
    store("sizeTier", currentTierChoice());
    store("quality", $("quality").value);
    store("patternMode", currentPatternMode());
    store("subject", $("subject") ? $("subject").value : "auto");
    store("outputFormat", currentOutputFormat());
  }

  /* ---------------------------------------------------------------- drafts */
  async function ensureDraft() {
    if (state.draftId) return state.draftId;
    const form = new FormData();
    state.files.forEach((entry) => form.append("files", entry.file, entry.file.name));
    const data = await api("/api/drafts", { method: "POST", body: form });
    state.draftId = data.draft_id;
    return state.draftId;
  }

  /* ------------------------------------------------------- job creation --- */
  async function createJob(extraRequirements) {
    if (state.busy) return;
    saveSettings();
    state.busy = true;
    updateSummary();
    $("startBtn").classList.add("loading");
    $("startBtn").textContent = "提交中…";
    try {
      const form = new FormData();
      state.files.forEach((entry) => form.append("files", entry.file, entry.file.name));
      const picked = selectionsPayload();
      form.append("selections", JSON.stringify(picked));
      const head = primaryStyle();
      form.append("garment_type", head.slug || "");
      for (const label of head.colors) form.append("colors", label);
      form.append("mode", "single_pass");
      form.append("provider", $("provider").value);
      form.append("model", currentModel());
      form.append("concurrency", "auto");
      form.append("size", currentSize());
      form.append("quality", $("quality").value);
      form.append("output_format", currentOutputFormat());
      form.append("pattern_mode", currentPatternMode());
      // 画面主体：原图只有衣服时禁止模型补模特（提示词层+否定词层都生效）
      form.append("subject", $("subject") ? $("subject").value : "auto");
      form.append("send_reference", "true");   // 默认总是带颜色参考图
      if (extraRequirements) form.append("extra_requirements", extraRequirements);
      form.append("start", "true");
      const data = await api("/api/jobs", { method: "POST", body: form });
      closeHarness();
      watchJob(data.job.id, data.job);
      loadHistory();
      loadGate();
      setTimeout(loadPricing, 1500);   // 先让结果区渲染出来，用量芯片稍后再刷
      const est = data.job.params.estimate || {};
      toast("ok", est.priced
        ? `已开工：${data.job.stats.total} 张，预计 ${money(est.total, est.currency)}`
        : "已开工");
    } catch (err) {
      toast("bad", `创建任务失败：${err.message}`);
    } finally {
      state.busy = false;
      $("startBtn").classList.remove("loading");
      $("startBtn").innerHTML = `${icon("i-spark")}开始生成`;
      updateSummary();
    }
  }

  /* ----------------------------------------------------------------- watch */
  // 轮询策略（省 Render 流量）：
  //  - 服务端 ETag + 304：任务没变化时只传响应头，几乎不花流量
  //  - 标签页在后台就**停轮询**，切回来立刻拉一次
  //  - 任务跑得久就逐步拉长间隔（1.2s → 2s → 3s）
  //  - 跑完后不再轮询
  function watchJob(jobId, seed) {
    state.jobId = jobId;
    state.selected = new Set();
    state.pollStartedAt = Date.now();
    try {
      const url = new URL(location.href);
      url.searchParams.set("job", jobId);
      history.replaceState(null, "", url);
    } catch (_) { /* ignore */ }
    if (state.pollTimer) clearTimeout(state.pollTimer);
    // POST 的响应里已经带完整任务列表 → 先渲染一帧，别让用户干等第一次轮询
    if (seed && seed.id === jobId) renderJob(seed);

    const tick = async () => {
      if (!state.jobId) return;
      if (document.hidden) {          // 后台不请求，切回来由 visibilitychange 触发
        state.pollTimer = null;
        return;
      }
      let delay = 1200;
      try {
        const data = await api(`/api/jobs/${state.jobId}`);
        renderJob(data.job);
        if (TERMINAL.includes(data.job.status)) {
          loadHistory();
          loadGate();
          maybeLoadPricing();
          state.pollTimer = null;
          return;
        }
        const total = (data.job.stats || {}).total || 0;
        const elapsed = (Date.now() - (state.pollStartedAt || Date.now())) / 1000;
        if (total > 12) delay = 2400;
        else if (total > 6) delay = 1600;
        if (elapsed > 60) delay = Math.max(delay, 2000);
        if (elapsed > 180) delay = Math.max(delay, 3000);
      } catch (err) {
        $("jobStage").textContent = `读取任务失败：${err.message}`;
        delay = 2500;
      }
      state.pollTimer = setTimeout(tick, delay);
    };
    tick();
  }

  // 价格/用量不需要每次任务结束都刷（飞书那边有 60s 缓存，刷了也不省钱）
  function maybeLoadPricing(force = false) {
    const now = Date.now();
    if (!force && state.pricingAt && now - state.pricingAt < 45000) return;
    loadPricing();
  }

  function renderJob(job) {
    state.job = job;
    const params = job.params || {};
    const isRework = params.kind === "rework";
    const styleCount = (params.selections || []).length;
    $("jobTitle").textContent = isRework
      ? `改图返工 · ${job.stats.total} 张`
      : styleCount > 1
        ? `${styleCount} 个款式 · ${job.stats.total} 张`
        : `${params.garment_name || "任务"} · ${job.stats.total} 张`;
    $("jobStage").textContent = job.stage || job.status;

    const stats = job.stats;
    animateNumber($("kpiTotal"), stats.total);
    animateNumber($("kpiDone"), stats.done + stats.skipped);
    animateNumber($("kpiFailed"), stats.failed);
    const est = params.estimate || {};
    const done = stats.done + stats.skipped;
    if (est.priced) {
      const actual = Number(est.unit) * done;
      $("kpiCost").innerHTML = `${money(actual, est.currency)}<small>/${money(est.total, est.currency)}</small>`;
    } else {
      $("kpiCost").textContent = "未定价";
    }
    const bar = $("progressBar");
    bar.setAttribute("aria-valuenow", String(Math.round(job.progress)));
    bar.querySelector("i").style.width = `${job.progress}%`;
    bar.classList.toggle("done", TERMINAL.includes(job.status));
    $("progressLabel").textContent = `${done} / ${stats.total}`;
    // 排队位置（来自飞书表轮询）：前方还有 xx 个任务
    const queue = params.queue || {};
    const ahead = Number(queue.ahead || 0);
    const queuedTotal = Number(queue.queued || 0);
    const limit = Number(queue.limit || 0);
    const running = Number(stats.running || 0);
    const waiting = Number(stats.queued || 0) + Number(stats.pending || 0);
    const queueParts = [];
    if (running) queueParts.push(`生成中 ${running}`);
    if (ahead > 0 && !TERMINAL.includes(job.status)) queueParts.push(`前方还有 ${ahead} 个任务`);
    if (queuedTotal > 0 && limit) queueParts.push(`飞书队列 ${queuedTotal} 张 / 额度 ${limit}`);
    if (!queueParts.length && waiting) queueParts.push(`排队 ${waiting}`);
    queueParts.push(`失败 ${stats.failed}`);
    if (job.error) queueParts.push(job.error);
    $("progressStats").textContent = queueParts.join(" · ");
    const chip = $("queueChip");
    if (chip) {
      const waiting = ahead > 0 && !TERMINAL.includes(job.status);
      chip.hidden = !waiting;
      if (waiting) {
        chip.innerHTML = `前方还有 <b>${ahead}</b> 个任务`;
        chip.title = `正在跑 ${running} 张${queuedTotal ? `，飞书队列里还有 ${queuedTotal} 张` : ""}${limit ? `（额度 ${limit}）` : ""}`;
      }
    }

    if ($("galleryCount")) {
      $("galleryCount").textContent = String(done).padStart(2, "0");
    }
    $("cancelBtn").hidden = TERMINAL.includes(job.status);
    $("retryBtn").hidden = !(stats.failed > 0);
    const limitedBtn = $("retryLimitedBtn");
    if (limitedBtn) {
      const limited = Number(stats.rate_limited || 0);
      limitedBtn.hidden = limited <= 0;
      limitedBtn.textContent = limited > 0 ? `重试被限流 ${limited} 张` : "重试被限流";
      limitedBtn.title = "只重试被接口并发/限流打回来的那些图（其他失败不动）";
    }
    $("downloadBtn").hidden = !job.has_zip;
    if (job.has_zip) $("downloadBtn").href = job.zip;
    // 飞书同步：跑完但没写进去时给个补写按钮
    const syncBtn = $("feishuSyncBtn");
    if (syncBtn) {
      const done = TERMINAL.includes(job.status) && (stats.done + stats.skipped) > 0;
      syncBtn.hidden = !(done && !params.feishu_rows);
      syncBtn.textContent = job.id === state.syncingJob ? "写入中…" : "补写飞书";
      syncBtn.disabled = job.id === state.syncingJob;
      if (params.feishu_table) syncBtn.title = params.feishu_table;
    }

    const grid = $("gridImgs");
    $("emptyState").hidden = job.tasks.length > 0;
    // 换了任务才重建；同一任务的轮询只增量更新，避免"出一张图就整片重载"
    if (state.cardsJob !== job.id) {
      grid.innerHTML = "";
      state.cards = new Map();
      state.cardsJob = job.id;
      state.logSig = "";
    }
    const seen = new Set();
    job.tasks.forEach((task, index) => {
      let entry = state.cards.get(task.id);
      if (!entry) {
        entry = cardShell(task, job);
        entry.el.style.setProperty("--i", String(Math.min(index, 14)));
        state.cards.set(task.id, entry);
        grid.appendChild(entry.el);
      }
      seen.add(task.id);
      updateCard(entry, task, job);
    });
    for (const [id, entry] of [...state.cards]) {
      if (!seen.has(id)) { entry.el.remove(); state.cards.delete(id); }
    }

    // 日志只在真有新增时才重绘
    const entries = job.log || [];
    const sig = `${job.id}:${entries.length}:${entries.length ? entries[entries.length - 1].t : 0}`;
    if (sig !== state.logSig) {
      const log = $("log");
      const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
      const html = entries
        .map((entry) => `<div class="${entry.level}">${fmtTime(entry.t)} ${esc(entry.msg)}</div>`)
        .join("");
      if (log.innerHTML !== html) log.innerHTML = html;
      if (stick) log.scrollTop = log.scrollHeight;
      state.logSig = sig;
    }

    // 出图多的时候自动降级毛玻璃/装饰动画，优先保住流畅度
    document.documentElement.classList.toggle("perf-lite", job.tasks.length > 8);
    // 出图过程中暂停极光动画（持续重绘整屏背景最吃 GPU），跑完自动恢复
    document.body.classList.toggle("busy", !TERMINAL.includes(job.status));

    $("resultToolbar").hidden = !job.tasks.length;
    syncSelection();
  }

  /* 成品卡：建壳 + 增量更新（只有变化的部分才动 DOM） */
  function shortName(name) {
    return String(name || "").replace(/^\d+\s*/, "") || "";
  }

  function cardShell(task, job) {
    const el = document.createElement("div");
    el.className = "cardimg";
    el.dataset.task = task.id;
    el.innerHTML = `
      <label class="pick" title="选择"><input type="checkbox" data-task="${esc(task.id)}" aria-label="选择 ${esc(task.item)} ${esc(task.color_name)}"/></label>
      <span class="state"></span>
      <div class="shot"><div class="ph"></div></div>
      <div class="meta">
        <span class="dot" style="background:${esc(task.color_hex || "#94a3b8")}"></span>
        <span class="name">${esc(task.color_name)}</span>
        <span class="garment" title="${esc(task.garment_name || "")}">${esc(shortName(task.garment_name))}</span>
        ${task.master
          ? `<span class="garment sync-tag" title="这一色是基准版型，同款其它颜色都以它为参考图改色">基准版型</span>`
          : (task.master_task ? `<span class="garment sync-tag" title="参考图用的是基准色成品，只改面料颜色">跟随基准</span>` : "")}
        <span class="file" title="${esc(task.out || task.item)}">${esc(fileLabel(task))}</span>
      </div>
      <div class="acts">
        <a class="btn btn-ghost btn-sm" data-dl>${icon("i-download", 13)}下载</a>
        <button class="btn btn-ghost btn-sm" data-rework="${esc(task.id)}">${icon("i-chat", 13)}改图</button>
      </div>`;
    return {
      el,
      shot: el.querySelector(".shot"),
      state: el.querySelector(".state"),
      input: el.querySelector("input[data-task]"),
      dl: el.querySelector("[data-dl]"),
      rework: el.querySelector("[data-rework]"),
      acts: el.querySelector(".acts"),
      file: el.querySelector(".meta .file"),
      err: null,
      retry: null,
      thumb: "",
      status: "",
      errorText: "",
      jobId: job.id,
    };
  }

  // 成品文件名（款式_颜色[_视角]）：下载前就能看到叫什么
  function fileLabel(task) {
    if (task.out) return String(task.out).split("/").pop();
    const style = String(task.garment_name || "").replace(/\s+/g, "");
    const color = String(task.color_name || "").replace(/\s+/g, "");
    return style && color ? `${style}_${color}` : task.item;
  }

  // 图片版本：优先用任务自己的版本号（完成后不再变）→ 浏览器/本地缓存能长期命中
  function taskVersion(task, job) {
    if (task.version) return task.version;
    const stamp = task.finished_at || task.started_at || 0;
    if (stamp) return Math.round(stamp * 1000);
    return Math.round(job.updated_at || 0);
  }

  // 网格直接用**原图**（不再走缩略图/IndexedDB）：本地读盘本来就快，
  // 只做两件事保证滚动流畅 —— 视口外不加载（IntersectionObserver + rootMargin）、
  // 加载完成前留位占位（.shot 已有 aspect-ratio，不会抖）。
  let imageIO = null;
  function lazyObserver() {
    if (imageIO || typeof IntersectionObserver === "undefined") return imageIO;
    imageIO = new IntersectionObserver((rows) => {
      for (const row of rows) {
        if (!row.isIntersecting) continue;
        const img = row.target;
        imageIO.unobserve(img);
        if (img.dataset.src) img.src = img.dataset.src;
      }
    }, { rootMargin: "500px 0px" });
    return imageIO;
  }

  function mountImage(entry, url, task) {
    let img = entry.shot.querySelector("img");
    if (!img) {
      img = document.createElement("img");
      img.decoding = "async";
      img.alt = `${task.color_name}成品`;
      entry.shot.innerHTML = "";
      entry.shot.appendChild(img);
    }
    // 浏览器 HTTP 缓存（immutable + 版本号）负责重复命中，不再自己存一份
    img.removeAttribute("src");
    img.dataset.src = url;
    const io = lazyObserver();
    if (io) {
      img.loading = "lazy";
      io.observe(img);
    } else {
      img.src = url;
    }
  }

  function refreshCacheInfo() { /* 已取消 IndexedDB 图片缓存 */ }

  function updateCard(entry, task, job) {
    const status = task.status;
    if (entry.status !== status) {
      entry.el.classList.remove(
        "st-pending", "st-queued", "st-running", "st-done", "st-failed", "st-skipped", "st-canceled"
      );
      entry.el.classList.add(`st-${status}`);
      entry.status = status;
    }
    entry.el.classList.toggle("sel", state.selected.has(task.id));

    // 排队中的图要说清楚「还差几个」，不能都显示成生成中
    let stateText = `${STATUS[status] || status}`;
    if (status === "queued") {
      const ahead = Number(task.queue_ahead);
      stateText = Number.isFinite(ahead) && ahead > 0 ? `排队中，还有 ${ahead} 个` : "排队中";
    } else if (task.duration) {
      stateText += ` ${task.duration}s`;
    }
    if (entry.state.textContent !== stateText) entry.state.textContent = stateText;

    // 只有真的换了图（版本变化）才动 <img>：否则进度轮询会不停重新解码
    const imageUrl = task.thumb
      ? `${task.thumb}?v=${taskVersion(task, job) || 0}`
      : "";
    if (imageUrl) {
      if (entry.thumb !== imageUrl) {
        entry.thumb = imageUrl;
        entry.el.classList.add("settled");
        entry.el.dataset.full = imageUrl;
        mountImage(entry, imageUrl, task);
      }
    } else if (entry.thumb) {
      entry.shot.innerHTML = `<div class="ph">${STATUS[status] || status}</div>`;
      entry.thumb = "";
      entry.el.classList.add("settled");
    } else {
      const ph = entry.shot.querySelector(".ph");
      const label = STATUS[status] || status;
      if (ph && ph.textContent !== label) ph.textContent = label;
    }

    if (task.error && entry.errorText !== task.error) {
      if (!entry.err) {
        entry.err = document.createElement("div");
        entry.err.className = "err";
        entry.el.insertBefore(entry.err, entry.acts);
      }
      entry.err.textContent = task.error;
      entry.err.title = task.error;
      entry.errorText = task.error;
    } else if (!task.error && entry.err) {
      entry.err.remove();
      entry.err = null;
      entry.errorText = "";
    }

    if (task.thumb) {
      const href = downloadUrl(job.id, task.id);
      if (entry.dl.getAttribute("href") !== href) {
        entry.dl.setAttribute("href", href);
        entry.dl.setAttribute("download", "");
        entry.dl.removeAttribute("aria-disabled");
        entry.dl.style.pointerEvents = "";
        entry.dl.style.opacity = "";
      }
    } else if (entry.dl.hasAttribute("href")) {
      entry.dl.removeAttribute("href");
      entry.dl.removeAttribute("download");
      entry.dl.setAttribute("aria-disabled", "true");
      entry.dl.style.pointerEvents = "none";
      entry.dl.style.opacity = "0.45";
    }

    // 出图后把文件名换成真实成品名（款式_颜色[_视角]）
    if (entry.file) {
      const label = fileLabel(task);
      if (entry.file.textContent !== label) {
        entry.file.textContent = label;
        entry.file.title = task.out || label;
      }
    }

    entry.rework.disabled = status !== "done";

    if (status === "failed") {
      if (!entry.retry) {
        entry.retry = document.createElement("button");
        entry.retry.className = "btn btn-danger btn-sm";
        entry.retry.dataset.retry = task.id;
        entry.retry.textContent = "重试";
        entry.acts.appendChild(entry.retry);
      }
    } else if (entry.retry) {
      entry.retry.remove();
      entry.retry = null;
    }

    // 选中状态：勾选框只在变化时写，避免覆盖用户正在点的那一下
    if (entry.input.checked !== state.selected.has(task.id)) {
      entry.input.checked = state.selected.has(task.id);
    }
  }

  function syncSelection() {
    const ready = (state.job?.tasks || []).filter((t) => t.out);
    document.querySelectorAll("#gridImgs .cardimg").forEach((card) => {
      const on = state.selected.has(card.dataset.task);
      card.classList.toggle("sel", on);
      const input = card.querySelector("input[data-task]");
      if (input) input.checked = on;
    });
    const count = state.selected.size;
    $("selCount").textContent = `已选 ${count} 张`;
    $("batchReworkBtn").disabled = count === 0;
    $("batchExportBtn").disabled = ready.length === 0 && count === 0;
    $("selAll").checked = ready.length > 0 && count === ready.length;
  }

  /* ---------------------------------------------------------------- history */
  async function loadHistory() {
    const box = $("history");
    if (box && !box.children.length) box.innerHTML = skeletonHTML(3);
    try {
      const data = await api("/api/jobs");
      const box = $("history");
      box.innerHTML = "";
      if (!data.jobs.length) {
        box.innerHTML = `<p class="tip" style="padding:6px 2px">还没有历史任务</p>`;
        return;
      }
      for (const job of data.jobs) {
        const item = document.createElement("div");
        item.className = "hitem" + (job.id === state.jobId ? " on" : "");
        const styles = (job.params.selections || []).length;
        const name = job.params.kind === "rework"
          ? "改图返工"
          : styles > 1
            ? `${styles} 个款式`
            : (job.params.garment_name || job.id);
        item.innerHTML = `<span class="grow">${esc(name)} · ${job.stats.total} 张</span>
          <span class="badge ${job.status}">${job.stats.done}/${job.stats.total}</span>`;
        item.title = `${job.id} · ${job.stage || ""}`;
        item.addEventListener("click", () => watchJob(job.id));
        box.appendChild(item);
      }
    } catch (_) {
      if (box && !box.children.length) box.innerHTML = `<p class="tip" style="padding:6px 2px">读取历史失败</p>`;
    }
  }

  /* --------------------------------------------------------------- actions */
  async function cancelJob() {
    if (!state.jobId) return;
    await withLoading($("cancelBtn"), async () => {
      try { await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" }); }
      catch (err) { toast("bad", err.message); }
    }, "取消中…");
  }
  async function retryJob() {
    if (!state.jobId) return;
    await withLoading($("retryBtn"), async () => {
      try {
        await api(`/api/jobs/${state.jobId}/retry`, { method: "POST" });
        watchJob(state.jobId);
      } catch (err) { toast("bad", err.message); }
    }, "重试中…");
  }
  async function retryLimited() {
    if (!state.jobId) return;
    const form = new FormData();
    form.append("only", "rate_limit");
    await withLoading($("retryLimitedBtn"), async () => {
      try {
        await api(`/api/jobs/${state.jobId}/retry`, { method: "POST", body: form });
        watchJob(state.jobId);
        toast("ok", "已重新排队：被限流的图会按闸门额度慢慢补上");
      } catch (err) { toast("bad", err.message); }
    }, "重试中…");
  }

  async function retrySingle(taskId) {
    if (!state.jobId) return;
    const form = new FormData();
    form.append("task_ids", taskId);
    try {
      await api(`/api/jobs/${state.jobId}/retry`, { method: "POST", body: form });
      watchJob(state.jobId);
    } catch (err) { toast("bad", err.message); }
  }
  function exportSelection() {
    if (!state.jobId) return;
    const ids = [...state.selected];
    const query = ids.length ? `?tasks=${encodeURIComponent(ids.join(","))}` : "";
    window.location.href = `/api/jobs/${state.jobId}/export${query}`;
    toast("ok", ids.length ? `正在导出 ${ids.length} 张原图` : "正在导出全部成品（原图）");
  }
  async function showPrompt() {
    await withLoading($("previewBtn"), async () => {
    try {
      const pick = primaryStyle();
      const color = pick.colors.values().next().value || null;
      const data = await api("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          garment_type: pick.slug,
          color,
          requirement: state.requirements || "",
          pattern_mode: currentPatternMode(),
          subject: $("subject") ? $("subject").value : "auto",
        }),
      });
      const modeLabel = (data.pattern_modes || {})[data.pattern_mode] || data.pattern_mode || "";
      const head = [
        `【一次到位】图案策略：${modeLabel}`,
        state.requirements ? `【本次需求】${state.requirements}` : "【本次需求】无（按默认规范）",
        state.styles.length > 1
          ? `【多款式】本批共 ${state.styles.length} 个款式，这里预览第 1 个：${styleName(pick.slug)}（每个款式按自己的版型短语各自组装提示词）`
          : "",
      ].filter(Boolean).join("\n");
      $("promptBody").textContent =
        `${head}\n\n${data.single_pass}\n\n【负面提示词】\n\n${data.negative}`;
      openDrawer("prompt");
    } catch (err) { toast("bad", err.message); }
    });
  }

  /* --------------------------------------------------------- 飞书历史 --- */
  async function loadFeishuRecords() {
    const box = $("feishuList");
    box.innerHTML = skeletonHTML(4, 44);
    $("feishuSub").textContent = "正在从飞书读取…";
    try {
      const data = await api("/api/feishu/records?limit=200&links=true");
      state.feishuRecords = data.records || [];
      $("feishuSub").textContent = `${data.count} 条记录 · ${data.table || ""}`;
      renderFeishuRecords();
    } catch (err) {
      box.innerHTML = `<p class="tip" style="color:var(--danger);padding:6px 2px">${esc(err.message)}</p>`;
      $("feishuSub").textContent = "读取失败";
    }
  }

  function renderFeishuRecords() {
    const box = $("feishuList");
    const rows = state.feishuRecords || [];
    box.innerHTML = "";
    if (!rows.length) {
      box.innerHTML = `<p class="tip" style="padding:6px 2px">飞书表格里还没有记录</p>`;
      $("feishuCount").textContent = "0 条";
      $("feishuExport").disabled = true;
      return;
    }
    for (const record of rows) {
      const selected = state.feishuSelected.has(record.record_id) ? "checked" : "";
      const row = document.createElement("div");
      row.className = "frow" + (selected ? " sel" : "");
      const has = record.attachment_count > 0;
      const when = record.time ? fmtTime(Math.floor(record.time / 1000)) : "";
      const money0 = record.amount === null || record.amount === undefined
        ? "" : `¥${Number(record.amount).toFixed(3)}`;
      // 直链：浏览器直接找飞书要图，不经过本机服务
      const direct = record.download_url
        ? `<a class="badge ok" href="${esc(record.download_url)}" target="_blank" rel="noopener"
              title="飞书直链下载（不经过本机服务）">直链</a>`
        : "";
      row.innerHTML = `
        <input type="checkbox" data-record="${esc(record.record_id)}" ${selected} ${has ? "" : "disabled"} />
        <span class="grow">${esc(record.name)} · ${esc(record.model || "")} ${esc(money0)}</span>
        ${direct}
        <span class="badge ${has ? "ok" : "no"}">${has ? "有图" : "无图"}</span>
        <span class="badge">${esc(when)}</span>`;
      row.querySelector("input").addEventListener("change", (event) => {
        if (event.target.checked) state.feishuSelected.add(record.record_id);
        else state.feishuSelected.delete(record.record_id);
        renderFeishuRecords();
      });
      box.appendChild(row);
    }
    const take = rows.filter((r) => r.attachment_count > 0).length;
    $("feishuCount").textContent = `已选 ${state.feishuSelected.size} / ${rows.length} 条（${take} 条有成品图）`;
    $("feishuExport").disabled = state.feishuSelected.size === 0;
    $("feishuAll").checked = state.feishuSelected.size === take && take > 0;
  }

  async function exportFromFeishu() {
    const ids = [...state.feishuSelected];
    if (!ids.length) return;
    $("feishuExport").disabled = true;
    $("feishuExport").textContent = "打包中…";
    try {
      const res = await fetch("/api/feishu/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ record_ids: ids }),
      });
      if (!res.ok) {
        let message = `HTTP ${res.status}`;
        try { message = (await res.json()).error || message; } catch (_) { /* ignore */ }
        throw new Error(message);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "feishu-history.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      toast("ok", `已从飞书打包 ${ids.length} 张历史成品`);
    } catch (err) {
      toast("bad", `打包失败：${err.message}`);
    } finally {
      $("feishuExport").disabled = state.feishuSelected.size === 0;
      $("feishuExport").textContent = "打包下载 ZIP";
    }
  }

  /* ------------------------------------------------------- drawer plumbing */
  function openFeishu() {
    $("feishuScrim").hidden = false;
    $("feishuDrawer").hidden = false;
    requestAnimationFrame(() => {
      $("feishuScrim").classList.add("on");
      $("feishuDrawer").classList.add("on");
    });
    loadFeishuRecords();
  }
  function closeFeishu() { closePanel("feishuDrawer", "feishuScrim"); }

  function openDrawer(which) {
    const isPrompt = which === "prompt";
    const scrim = isPrompt ? $("promptScrim") : $("harnessScrim");
    const panel = isPrompt ? $("promptDrawer") : $("harnessDrawer");
    (isPrompt ? $("harnessDrawer") : $("promptDrawer")).hidden = true;
    (isPrompt ? $("harnessScrim") : $("promptScrim")).hidden = true;
    scrim.hidden = false;
    panel.hidden = false;
    requestAnimationFrame(() => { scrim.classList.add("on"); panel.classList.add("on"); });
    if (!isPrompt) setTimeout(() => $("harnessInput").focus(), 240);
  }
  function closePanel(panelId, scrimId) {
    $(panelId).classList.remove("on");
    $(scrimId).classList.remove("on");
    setTimeout(() => { $(panelId).hidden = true; $(scrimId).hidden = true; }, 240);
  }
  function closeHarness() { closePanel("harnessDrawer", "harnessScrim"); }
  function closePrompt() { closePanel("promptDrawer", "promptScrim"); }

  /* ----------------------------------------------------------- harness chat */
  function chatBubble(role, text) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}`;
    wrap.innerHTML = `<span class="who">${role === "user" ? "你" : "harness"}</span><div class="bubble"></div>`;
    const bubble = wrap.querySelector(".bubble");
    bubble.textContent = text || "";
    $("chatLog").appendChild(wrap);
    $("chatLog").scrollTop = $("chatLog").scrollHeight;
    return bubble;
  }

  function chatNote(title, body, kind = "") {
    const wrap = document.createElement("div");
    wrap.className = "msg assistant";
    wrap.innerHTML = `<span class="tools">${kind === "tool" ? "· " + esc(title) : esc(title)}</span>
      ${body ? `<div class="tool-result">${esc(body)}</div>` : ""}`;
    $("chatLog").appendChild(wrap);
    scrollChat();
  }

  function scrollChat() {
    const box = $("chatLog");
    if (box) box.scrollTop = box.scrollHeight;
  }

  // 等待模型回复：三点跳动气泡（收到第一个字就撤掉）
  function showTyping() {
    if (state.typingEl) return;
    const wrap = document.createElement("div");
    wrap.className = "msg assistant typing";
    wrap.innerHTML = `<span class="who">harness</span><div class="bubble"><i></i><i></i><i></i></div>`;
    $("chatLog").appendChild(wrap);
    state.typingEl = wrap;
    scrollChat();
  }

  function hideTyping() {
    if (!state.typingEl) return;
    state.typingEl.remove();
    state.typingEl = null;
  }

  // 工具调用：先转圈，结果回来打勾 → 可点开看返回内容
  function toolStart(name) {
    const label = TOOL_LABEL[name] || name;
    const row = document.createElement("div");
    row.className = "msg assistant tool-row";
    row.dataset.tool = name;
    row.innerHTML = `
      <div class="tool-line running" role="button" tabindex="0" aria-label="${esc(label)}">
        <span class="spin" aria-hidden="true"></span>
        <span class="txt">正在${esc(label)}…</span>
        <span class="chev" hidden>▾</span>
      </div>
      <div class="tool-result" hidden></div>`;
    const line = row.querySelector(".tool-line");
    const result = row.querySelector(".tool-result");
    const toggle = () => {
      if (!result.textContent.trim()) return;
      result.hidden = !result.hidden;
      row.querySelector(".chev").textContent = result.hidden ? "▾" : "▴";
    };
    line.addEventListener("click", toggle);
    line.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
    });
    $("chatLog").appendChild(row);
    scrollChat();
    const prev = state.toolRows.get(name);
    if (prev && prev !== row && prev.isConnected && !prev.dataset.done) prev.remove();
    state.toolRows.set(name, row);
    return row;
  }

  function toolEnd(name, text) {
    let row = state.toolRows.get(name);
    const body = String(text || "").trim();
    if (!row || !row.isConnected) {
      // 没抓到开始事件（例如降级路径）→ 直接补一条完成态
      row = toolStart(name);
    }
    const line = row.querySelector(".tool-line");
    line.classList.remove("running");
    line.classList.add("done");
    line.querySelector(".spin").outerHTML = `<span class="ok">${icon("i-check", 12)}</span>`;
    line.querySelector(".txt").textContent = TOOL_LABEL[name] || name;
    const result = row.querySelector(".tool-result");
    if (body) {
      result.textContent = body;
      row.querySelector(".chev").hidden = false;
      row.querySelector(".chev").textContent = "▾";
    }
    row.dataset.open = "false";
    row.dataset.done = "1";
    scrollChat();
    return row;
  }

  function pushTypeText(text) {
    state.pendingText += text;
    if (!state.typerTimer) state.typerTimer = setInterval(() => flushType(false), 24);
  }
  function flushType(instant) {
    const bubble = state.streamBubble;
    if (!bubble) {
      state.pendingText = "";
      if (state.typerTimer) { clearInterval(state.typerTimer); state.typerTimer = null; }
      return;
    }
    if (instant) {
      bubble.textContent += state.pendingText;
      state.pendingText = "";
    } else if (state.pendingText) {
      const step = Math.max(2, Math.ceil(state.pendingText.length / 16));
      bubble.textContent += state.pendingText.slice(0, step);
      state.pendingText = state.pendingText.slice(step);
    }
    $("chatLog").scrollTop = $("chatLog").scrollHeight;
    if (!state.pendingText && state.typerTimer) {
      clearInterval(state.typerTimer);
      state.typerTimer = null;
      bubble.classList.remove("streaming");
    }
  }

  function resetChat() {
    if (state.typerTimer) { clearInterval(state.typerTimer); state.typerTimer = null; }
    state.pendingText = "";
    state.streamBubble = null;
    $("chatLog").innerHTML = "";
    state.composedPrompt = "";
    state.requirements = "";
    state.harnessHistory = [];
    state.assistantText = "";
    state.typingEl = null;
    state.toolRows.clear();
  }

  function renderQuick(list) {
    const box = $("quickChips");
    box.innerHTML = "";
    for (const text of list) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = text;
      button.addEventListener("click", () => {
        const input = $("harnessInput");
        input.value = input.value ? `${input.value}；${text}` : text;
        input.focus();
      });
      box.appendChild(button);
    }
  }

  async function openHarness(mode, taskIds) {
    state.harnessMode = mode;
    state.reworkTargets = mode === "rework" ? (taskIds || [...state.selected]) : [];
    state.reworkSourceJob = state.jobId;
    resetChat();
    if (mode === "rework") {
      if (!state.reworkTargets.length) { toast("bad", "请先选择要改图的成品"); return; }
      $("harnessTitle").textContent = "改图返工";
      $("harnessSub").textContent = `以选中的 ${state.reworkTargets.length} 张成品为参考图`;
      $("harnessApply").textContent = "确认返工";
      $("harnessDefault").textContent = "取消";
      renderQuick(QUICK_REWORK);
      await initReworkModels();
      chatBubble("assistant", `要返工 ${state.reworkTargets.length} 张。说说要改什么（哪一处、往哪个方向），我先看清楚再动手。`);
    } else {
      try { await ensureDraft(); }
      catch (err) { toast("bad", `上传草稿失败：${err.message}`); return; }
      const items = state.files.length;
      const colors = totalColors();
      const styleText = state.styles
        .map((entry) => `${styleName(entry.slug)}（${entry.colors.size} 色）`)
        .join(" + ");
      $("harnessTitle").textContent = "需求沟通";
      $("harnessSub").textContent = `${items} 图 × ${colors} 色 · 预计 ${items * colors} 次调用`;
      $("harnessApply").textContent = "按分析结果生成";
      $("harnessDefault").textContent = "按默认生成";
      renderQuick(QUICK_INTAKE);
      const model = currentModel();
      const price = state.pricing.byId[model];
      const cost = price && price.unit !== null
        ? `预计 ${money(price.unit * items * colors, price.currency)}`
        : "该模型未定价";
      chatBubble(
        "assistant",
        `这批：${styleText} · ${items} 图 × 共 ${colors} 色，${model}，${cost}。\n有特殊要求可以直接说，也可以让我先看图判断风险。`
      );
    }
    // 只有改图才显示「换模型」那一行
    $("reworkModel").hidden = mode !== "rework";
    $("harnessApply").disabled = true;
    openDrawer("harness");
    if (state.pendingBrief) {
      $("harnessInput").value = state.pendingBrief;
      state.pendingBrief = "";
      $("harnessInput").focus();
    }
  }

  /* ------------------------------------------------- 改图：换模型 ---------- */
  // 成品当参考图交给别的模型重画：默认跟原任务同接口同模型，选了别人才换
  function sourceJobMeta() {
    const job = state.job || {};
    const params = job.params || {};
    return {
      provider: String(params.provider || ""),
      model: String(params.model || ""),
      size: String(params.size || "auto"),
    };
  }

  async function initReworkModels() {
    const meta = sourceJobMeta();
    const providerSel = $("reworkProvider");
    providerSel.innerHTML = "";
    for (const provider of state.providers) {
      const option = document.createElement("option");
      option.value = provider.name;
      option.textContent = provider.label;
      providerSel.appendChild(option);
    }
    const wanted = state.providers.find((p) => p.name === meta.provider)
      || state.providers.find((p) => p.ready)
      || state.providers[0];
    if (wanted) providerSel.value = wanted.name;
    providerSel.onchange = () => loadReworkModels(providerSel.value, "");
    await loadReworkModels(providerSel.value, meta.model);
  }

  async function loadReworkModels(providerName, preferred) {
    const select = $("reworkModelSel");
    select.disabled = true;
    select.innerHTML = '<option value="">正在读取模型…</option>';
    let rows = [];
    try {
      const data = await api(
        `/api/models?provider=${encodeURIComponent(providerName)}&refresh=false`
      );
      rows = (data.models || []).filter((m) => m.image);
    } catch (err) {
      console.warn("改图模型拉取失败", err.message);
    }
    select.innerHTML = "";
    if (!rows.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "（这个接口没有可用的图像模型）";
      select.appendChild(option);
    }
    for (const model of rows) {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.id;
      select.appendChild(option);
    }
    const keep = rows.find((m) => m.id === preferred) ? preferred : (rows[0] ? rows[0].id : "");
    select.value = keep;
    select.disabled = false;
    select.onchange = () => renderReworkHint();
    renderReworkHint();
  }

  function reworkChoice() {
    const provider = $("reworkProvider").value;
    const model = $("reworkModelSel").value;
    return { provider, model };
  }

  function renderReworkHint() {
    const hint = $("reworkModelHint");
    if (!hint) return;
    const count = state.reworkTargets.length;
    const meta = sourceJobMeta();
    const { provider, model } = reworkChoice();
    const switched = (meta.provider && provider !== meta.provider)
      || (meta.model && model !== meta.model);
    const size = currentSize() || meta.size;
    const info = model ? priceOf(model, size) : { unit: null };
    const priceText = info.unit !== null && info.unit !== undefined
      ? `预计 ${money(info.unit * Math.max(1, count), state.pricing.currency)}`
        + `（${money(info.unit, state.pricing.currency)}/张）`
      : "该模型未定价";
    let head;
    if (!switched) head = `沿用 ${model || "原模型"}`;
    else if (meta.model) head = `换模型重画：${meta.model} → ${model || "未选"}`;
    else head = `换成 ${model || "未选"} 重画`;
    hint.textContent = `${head} · ${count} 张 · 成品作为参考图 · ${priceText}`;
    hint.dataset.state = switched ? "switch" : "same";
  }

  async function streamHarness(payload) {
    const res = await fetch("/api/harness/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok || !res.body) throw new Error(`harness 连接失败 HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        handleFrame(frame);
      }
    }
  }

  function handleFrame(frame) {
    let event = "message";
    let data = "";
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data) return;
    let payload;
    try { payload = JSON.parse(data); } catch (_) { return; }

    if (event === "delta") {
      hideTyping();
      if (!state.streamBubble) {
        state.streamBubble = chatBubble("assistant", "");
        state.streamBubble.classList.add("streaming");
        state.streamedText = "";
      }
      state.streamedText += payload.text || "";
      state.assistantText += payload.text || "";
      pushTypeText(payload.text || "");
    } else if (event === "message") {
      hideTyping();
      flushType(true);
      if (state.streamBubble) state.streamBubble.classList.remove("streaming");
      state.streamBubble = null;
      const text = String(payload.text || "");
      // 已经流式输出过同一段内容 → 不再重复渲染气泡
      if (text && text.trim() !== state.streamedText.trim()) {
        chatBubble("assistant", text);
        state.assistantText += (state.assistantText ? "\n" : "") + text;
      }
      state.streamedText = "";
    } else if (event === "tool") {
      hideTyping();
      flushType(true);
      if (state.streamBubble) state.streamBubble.classList.remove("streaming");
      state.streamBubble = null;
      toolStart(payload.name);
      showTyping();           // 工具跑完还要再想一轮 → 继续转圈
    } else if (event === "result") {
      toolEnd(payload.name, String(payload.text || ""));
      showTyping();
    } else if (event === "prompt") {
      hideTyping();
      flushType(true);
      if (state.streamBubble) state.streamBubble.classList.remove("streaming");
      state.streamBubble = null;
      state.composedPrompt = payload.prompt || "";
      state.requirements = payload.requirements || "";
      $("harnessApply").disabled = state.harnessMode === "rework"
        ? !state.composedPrompt
        : !state.requirements;
      if (payload.job_id) {
        watchJob(payload.job_id);
        loadHistory();
        closeHarness();          // 定价/用量等任务跑完再刷，别在这儿抢带宽和事件循环
        toast("ok", "harness 已提交任务");
      }
    } else if (event === "done") {
      hideTyping();
      flushType(true);
      if (state.streamBubble) state.streamBubble.classList.remove("streaming");
      state.streamBubble = null;
    } else if (event === "job") {
      if (payload.job_id) {
        watchJob(payload.job_id);
        loadHistory();
        closeHarness();          // 定价/用量等任务跑完再刷，别在这儿抢带宽和事件循环
        toast("ok", "harness 已提交任务");
      }
    } else if (event === "done") {
      flushType(true);
      if (state.streamBubble) state.streamBubble.classList.remove("streaming");
      state.streamBubble = null;
      state.streamedText = "";
      $("harnessSend").disabled = false;
      state.harnessBusy = false;
    }
  }

  function harnessPayload(text) {
    const payload = {
      mode: state.harnessMode === "rework" ? "rework" : "intake",
      instruction: text,
    };
    if (state.harnessMode === "rework") {
      // 改图必须带上对话框里选的那个接口/模型，否则会退回左侧主面板（= 原接口）
      const choice = reworkChoice();
      payload.job_id = state.reworkSourceJob;
      payload.task_ids = state.reworkTargets;
      payload.provider = choice.provider || $("provider").value;
      payload.model = choice.model || currentModel();
    } else {
      payload.draft_id = state.draftId;
      const head = primaryStyle();
      payload.garment_type = head.slug || "";
      payload.colors = [...head.colors];
      payload.selections = selectionsPayload();
      payload.provider = $("provider").value;
      payload.model = currentModel();
      payload.count = expectedCalls();
      payload.size = currentSize();
      payload.quality = $("quality").value;
      payload.output_format = currentOutputFormat();
    }
    payload.pattern_mode = currentPatternMode();
    payload.history = state.harnessHistory.slice(-10);
    return payload;
  }

  async function sendHarnessMessage() {
    if (state.harnessBusy) return;
    const text = ($("harnessInput").value || "").trim();
    if (!text) { toast("bad", "先写一句需求，或直接点「按默认生成」"); return; }
    chatBubble("user", text);
    state.harnessHistory.push({ role: "user", content: text });
    state.assistantText = "";
    $("harnessInput").value = "";
    state.harnessBusy = true;
    $("harnessSend").disabled = true;
    $("harnessSend").classList.add("loading");
    $("harnessApply").disabled = true;
    showTyping();
    try {
      await streamHarness(harnessPayload(text));
    } catch (err) {
      hideTyping();
      toast("bad", `harness 出错：${err.message}`);
    } finally {
      hideTyping();
      state.harnessBusy = false;
      $("harnessSend").disabled = false;
      $("harnessSend").classList.remove("loading");
      const answer = state.assistantText.trim();
      if (answer) state.harnessHistory.push({ role: "assistant", content: answer });
      state.harnessHistory = state.harnessHistory.slice(-12);
      state.assistantText = "";
    }
  }

  async function harnessDefaultAction() {
    if (state.harnessMode === "rework") { closeHarness(); return; }
    await createJob("");
  }

  function briefText() {
    return ($("briefInput") ? $("briefInput").value : "").trim();
  }

  async function harnessApplyAction() {
    if (state.harnessMode === "rework") { await submitRework(); return; }
    await createJob(state.requirements);
  }

  async function submitRework() {
    if (!state.reworkTargets.length) { toast("bad", "没有选中成品"); return; }
    const instruction = ($("harnessInput").value || "").trim() || state.requirements ||
      "按 harness 分析结果调整";
    const choice = reworkChoice();
    const provider = choice.provider || $("provider").value;
    const model = choice.model || currentModel();
    if (!provider || !model) { toast("bad", "请选择改图用的接口和模型"); return; }
    const form = new FormData();
    for (const id of state.reworkTargets) form.append("task_ids", id);
    form.append("instruction", instruction);
    form.append("provider", provider);
    form.append("model", model);
    form.append("size", currentSize());
    form.append("quality", $("quality").value);
    form.append("output_format", currentOutputFormat());
    form.append("pattern_mode", currentPatternMode());
    if (state.composedPrompt) form.append("prompt_override", state.composedPrompt);
    form.append("start", "true");
    $("harnessApply").disabled = true;
    $("harnessApply").classList.add("loading");
    $("harnessApply").textContent = "提交中…";
    try {
      const data = await api(`/api/jobs/${state.reworkSourceJob}/rework`, { method: "POST", body: form });
      closeHarness();
      watchJob(data.job.id, data.job);
      loadHistory();
      loadGate();
      setTimeout(loadPricing, 1500);
      toast("ok", `已提交 ${state.reworkTargets.length} 张返工`);
    } catch (err) {
      toast("bad", `提交返工失败：${err.message}`);
    } finally {
      $("harnessApply").classList.remove("loading");
      $("harnessApply").disabled = false;
      $("harnessApply").textContent = "确认返工";
    }
  }

  /* ------------------------------------------------------------------ bind */
  function startBlockedReason() {
    if (!state.files.length) return "请先上传至少一张原图";
    if (!totalColors()) return "请至少选择一个颜色";
    if (!currentModel()) return "请选择模型";
    if (!$("provider").value) return "请选择生图接口";
    return "";
  }

  function bind() {
    const drop = $("drop");
    const input = $("fileInput");
    bindStyleList();
    drop.addEventListener("click", () => input.click());
    input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });
    ["dragenter", "dragover"].forEach((type) =>
      drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((type) =>
      drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (event) => {
      if (event.dataTransfer && event.dataTransfer.files) addFiles(event.dataTransfer.files);
    });

    const gateHint = $("gateHint");
    if (gateHint) gateHint.addEventListener("click", () => loadGate());

    const addStyleBtn = $("addStyle");
    if (addStyleBtn) addStyleBtn.addEventListener("click", () => addStyle());

    ["aspect", "sizeTier", "patternMode", "quality", "outputFormat", "downloadFmt"]
      .filter((id) => $(id))
      .forEach((id) =>
        $(id).addEventListener("change", () => {
          saveSettings();
          renderModelLabels();
          updateSummary();
        })
      );
    $("model").addEventListener("change", () => {
      const manual = $("model").value === "__manual__";
      $("modelManual").hidden = !manual;
      store(`model.${$("provider").value}`, $("model").value);
      renderModelLabels();
      updateSummary();
    });
    $("modelManual").addEventListener("input", () => {
      renderModelLabels();
      updateSummary();
    });

    $("startBtn").addEventListener("click", () => {
      const reason = startBlockedReason();
      if (reason) { toast("bad", reason); return; }
      const brief = ($("briefInput") ? $("briefInput").value : "").trim();
      state.pendingBrief = brief;
      openHarness("intake", []);
    });
    $("previewBtn").addEventListener("click", showPrompt);
    $("cancelBtn").addEventListener("click", cancelJob);
    $("retryBtn").addEventListener("click", retryJob);
    if ($("retryLimitedBtn")) $("retryLimitedBtn").addEventListener("click", retryLimited);
    $("feishuSyncBtn").addEventListener("click", async () => {
      const jobId = state.jobId || (state.job && state.job.id);
      if (!jobId) return;
      state.syncingJob = jobId;
      if (state.job) renderJob(state.job);
      $("feishuSyncBtn").classList.add("loading");
      try {
        const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/feishu`, { method: "POST" });
        toast(
          data.rows ? "ok" : "bad",
          data.rows ? `已补写 ${data.rows} 行到飞书` : "写入 0 行（检查飞书配置或权限）"
        );
        if (state.job) {
          state.job.params.feishu_rows = data.rows || state.job.params.feishu_rows;
          state.job.params.feishu_table = data.table || state.job.params.feishu_table;
          renderJob(state.job);
        }
      } catch (err) {
        toast("bad", `补写失败：${err.message}`);
      } finally {
        state.syncingJob = "";
        $("feishuSyncBtn").classList.remove("loading");
      }
    });
    $("selAll").addEventListener("change", (event) => {
      const ready = (state.job?.tasks || []).filter((t) => t.out);
      state.selected = event.target.checked ? new Set(ready.map((t) => t.id)) : new Set();
      syncSelection();
    });
    $("batchReworkBtn").addEventListener("click", () => openHarness("rework", [...state.selected]));
    $("batchExportBtn").addEventListener("click", exportSelection);
    $("usageChip").addEventListener("click", async () => {
      await loadPricing();      // 用户主动点，强制刷新一次用量
      if (!state.usage) return;
      const today = state.usage.today || {};
      const total = state.usage.total || {};
      toast(
        "ok",
        `今日 ${today.images || 0} 张 ${money(today.cost, state.usage.currency)} · ` +
        `累计 ${total.images || 0} 张 ${money(total.cost, state.usage.currency)} · ` +
        `${state.usage.jobs || 0} 个任务`
      );
    });

    $("openHarnessBtn").addEventListener("click", () => {
      if (state.jobId && state.selected.size) { openHarness("rework", [...state.selected]); return; }
      if (!state.files.length) { toast("bad", "先上传原图，或先在右侧选中成品再改图"); return; }
      openHarness("intake", []);
    });

    $("harnessSend").addEventListener("click", sendHarnessMessage);
    $("harnessInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        sendHarnessMessage();
      }
    });
    $("harnessDefault").addEventListener("click", harnessDefaultAction);
    $("harnessApply").addEventListener("click", harnessApplyAction);
    $("harnessClose").addEventListener("click", closeHarness);
    $("harnessScrim").addEventListener("click", closeHarness);
    $("feishuBtn").addEventListener("click", openFeishu);
    $("feishuClose").addEventListener("click", closeFeishu);
    $("feishuScrim").addEventListener("click", closeFeishu);
    $("feishuReload").addEventListener("click", () =>
      withLoading($("feishuReload"), async () => {
        state.feishuSelected = new Set();
        await loadFeishuRecords();
      }, "读取中…")
    );
    $("feishuAll").addEventListener("change", (event) => {
      const rows = (state.feishuRecords || []).filter((r) => r.attachment_count > 0);
      state.feishuSelected = event.target.checked
        ? new Set(rows.map((r) => r.record_id))
        : new Set();
      renderFeishuRecords();
    });
    $("feishuExport").addEventListener("click", exportFromFeishu);
    $("promptClose").addEventListener("click", closePrompt);
    $("promptScrim").addEventListener("click", closePrompt);
    $("accountsBtn").addEventListener("click", showAccounts);
    $("accountsClose").addEventListener("click", hideAccounts);
    $("accountsScrim").addEventListener("click", hideAccounts);
    $("accountsReload").addEventListener("click", () =>
      withLoading($("accountsReload"), loadAccounts, "读取中…")
    );
    $("logoutBtn").addEventListener("click", logout);
    $("usageChip").addEventListener("click", () => {
      if (state.session && state.session.admin) showAccounts();
    });

    // 成品卡用事件委托：卡片是增量更新的，不再每次轮询重复绑定监听
    const grid = $("gridImgs");
    grid.addEventListener("change", (event) => {
      const input = event.target.closest("input[data-task]");
      if (!input) return;
      if (input.checked) state.selected.add(input.dataset.task);
      else state.selected.delete(input.dataset.task);
      syncSelection();
    });
    grid.addEventListener("click", (event) => {
      const rework = event.target.closest("[data-rework]");
      if (rework) {
        event.preventDefault();
        if (!rework.disabled) openHarness("rework", [rework.dataset.rework]);
        return;
      }
      const retry = event.target.closest("[data-retry]");
      if (retry) {
        event.preventDefault();
        retrySingle(retry.dataset.retry);
        return;
      }
      const dl = event.target.closest("[data-dl]");
      if (dl && dl.getAttribute("aria-disabled") === "true") event.preventDefault();
      // 点图看大图：星雾灯箱（原图尺寸，可下载）
      const shot = event.target.closest(".cardimg .shot");
      const card = shot && shot.closest(".cardimg");
      if (card && card.dataset.full) {
        const name = card.querySelector(".meta .name");
        openLightbox(card.dataset.full, name ? `${name.textContent} · 成品原图` : "成品原图");
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("harnessDrawer").hidden) closeHarness();
      if (!$("promptDrawer").hidden) closePrompt();
      if (!$("feishuDrawer").hidden) closeFeishu();
      if (!$("accountsDrawer").hidden) hideAccounts();
    });

    bindAurora();
    bindMotion();

    // 切回标签页时立刻追一次进度（后台是完全不轮询的）
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
        return;
      }
      if (state.jobId && !state.pollTimer) watchJob(state.jobId, state.job || null);
      if (state.session && state.session.admin) refreshCacheInfo();
    });
  }

  /* ------------------------------------------------------------ 星雾交互 7.0 */
  function switchView(name) {
    const which = name === "logs" ? "logs" : "gallery";
    state.view = which;
    const gallery = $("galleryPane");
    const logs = $("logPane");
    if (gallery) gallery.hidden = which !== "gallery";
    if (logs) logs.hidden = which !== "logs";
    document.querySelectorAll("[data-view]").forEach((btn) => {
      const on = btn.dataset.view === which;
      if (btn.classList.contains("view-tab")) btn.classList.toggle("active", on);
      else if (btn.classList.contains("toplinks") || btn.closest(".toplinks")) btn.classList.toggle("current", on);
      else if (btn.classList.contains("nav-icon")) btn.classList.toggle("on", on);
    });
    try {
      const next = `${location.pathname}${location.search}${which === "logs" ? "#logs" : ""}`;
      history.replaceState(null, "", next);
    } catch (_) { /* ignore */ }
    if (which === "logs") loadHistory();
  }

  function openLightbox(url, title) {
    const dialog = $("lightbox");
    if (!dialog) { window.open(url, "_blank", "noopener"); return; }
    $("largeImage").src = url;
    $("lightboxTitle").textContent = title || "成品大图";
    const link = $("sampleDownload");
    if (link) link.href = `${url}${url.includes("?") ? "&" : "?"}download=true`;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  const BRIEF_IDEAS = [
    "图案必须原样不动，只换面料底色，保留印花颗粒感。",
    "背景保持干净纯色，光线柔和自然，电商棚拍质感。",
    "整体提亮半档，保留人物与构图，不要改脸。",
    "每个颜色一套独立的印花配色，别复用同一张图。",
  ];

  function bindAurora() {
    // 视图切换（顶栏 / 左侧图标栏 / 画布标签共用 data-view）
    document.querySelectorAll("[data-view]").forEach((btn) =>
      btn.addEventListener("click", () => switchView(btn.dataset.view))
    );

    // 上传：点击整块 drop / 图标栏 / 键盘 Enter 都走同一个 input
    const dropZone = $("drop");
    if (dropZone) {
      dropZone.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          $("fileInput").click();
        }
      });
    }

    const upload = $("railUpload");
    if (upload) upload.addEventListener("click", () => $("fileInput").click());
    const chat = $("railChat");
    if (chat) chat.addEventListener("click", () => $("openHarnessBtn").click());
    const usage = $("railUsage");
    if (usage) usage.addEventListener("click", () => showAccounts());
    const avatarBtn = $("railAvatar");
    if (avatarBtn) {
      avatarBtn.addEventListener("click", () => {
        const who = state.session ? (state.session.display || state.session.account) : "未登录";
        toast("ok", `${who} · 右上角 × 可退出登录`);
      });
    }
    // 顶栏的「账号用量 / 管理后台」按权限显示，图标栏跟着同步
    const syncRail = () => {
      const adminHidden = $("adminLink") ? $("adminLink").hidden : true;
      const usageHidden = $("accountsBtn") ? $("accountsBtn").hidden : true;
      if ($("railAdmin")) $("railAdmin").hidden = adminHidden;
      if ($("railUsage")) $("railUsage").hidden = usageHidden;
      const name = $("whoName") ? $("whoName").textContent.trim() : "";
      if (avatarBtn && name) avatarBtn.textContent = name.slice(0, 1).toUpperCase();
    };
    ["adminLink", "accountsBtn", "whoChip"].forEach((id) => {
      const el = $(id);
      if (el && typeof MutationObserver !== "undefined") {
        new MutationObserver(syncRail).observe(el, { attributes: true, attributeFilter: ["hidden"] });
      }
    });
    syncRail();

    // composer：快捷灵感 + 换一个灵感 + Ctrl/Cmd+Enter 开工
    const chips = $("briefChips");
    if (chips) {
      chips.innerHTML = BRIEF_IDEAS.slice(0, 3).map((text, index) =>
        `<button class="prompt-chip" type="button" data-brief="${index}">${esc(text.slice(0, 8))}…</button>`
      ).join("");
      chips.addEventListener("click", (event) => {
        const btn = event.target.closest("[data-brief]");
        if (!btn) return;
        const input = $("briefInput");
        input.value = BRIEF_IDEAS[Number(btn.dataset.brief)];
        input.focus();
      });
    }
    let ideaIndex = 0;
    const random = $("randomBrief");
    if (random) {
      random.addEventListener("click", () => {
        const input = $("briefInput");
        input.value = BRIEF_IDEAS[ideaIndex++ % BRIEF_IDEAS.length];
        input.focus();
        toast("ok", "灵感已填入，可以继续改");
      });
    }
    const brief = $("briefInput");
    if (brief) {
      brief.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
          event.preventDefault();
          $("startBtn").click();
        }
      });
    }
    const direct = $("directStartBtn");
    if (direct) {
      direct.addEventListener("click", async () => {
        const reason = startBlockedReason();
        if (reason) { toast("bad", reason); return; }
        await withLoading(direct, () => createJob(""), "提交中…");
      });
    }

    const feishuHistory = $("feishuHistoryBtn");
    if (feishuHistory) feishuHistory.addEventListener("click", () => openFeishu());

    // 灯箱
    const dialog = $("lightbox");
    if (dialog) {
      const close = $("closeModal");
      if (close) close.addEventListener("click", () => dialog.close());
      dialog.addEventListener("click", (event) => {
        if (event.target !== dialog) return;
        const rect = dialog.getBoundingClientRect();
        const out = event.clientX < rect.left || event.clientX > rect.right
          || event.clientY < rect.top || event.clientY > rect.bottom;
        if (out) dialog.close();
      });
    }

    // 鼠标跟随光斑（只在精细指针 + 未开启减弱动效时）
    if (window.matchMedia("(pointer: fine)").matches
      && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      let waiting = false;
      document.addEventListener("pointermove", (event) => {
        if (waiting) return;
        waiting = true;
        requestAnimationFrame(() => {
          document.documentElement.style.setProperty("--mx", `${event.clientX}px`);
          document.documentElement.style.setProperty("--my", `${event.clientY}px`);
          waiting = false;
        });
      });
    }
  }

  /* ------------------------------------------------------- 视觉动效（5.0） */
  function bindMotion() {
    const bar = $("progressBar");
    if (bar) bar.classList.add("ready");
    const appbar = document.querySelector(".appbar");
    let ticking = false;
    function onScroll() {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        const doc = document.documentElement;
        const max = Math.max(1, doc.scrollHeight - doc.clientHeight);
        const ratio = Math.min(1, Math.max(0, doc.scrollTop / max));
        doc.style.setProperty("--scroll", ratio.toFixed(4));
        if (appbar) appbar.classList.toggle("scrolled", doc.scrollTop > 6);
        ticking = false;
      });
    }
    document.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    // 拖放区：拖入高亮 + 离开恢复（已有 over 类，这里补键盘/触控的即时反馈）
    const drop = $("drop");
    if (drop) {
      drop.addEventListener("pointerdown", () => drop.classList.add("pressed"));
      ["pointerup", "pointerleave", "pointercancel"].forEach((type) =>
        drop.addEventListener(type, () => drop.classList.remove("pressed"))
      );
    }
  }

  boot();
})();
