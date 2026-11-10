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
  // 基准图审核期的话术：都是「对着基准图说一句」，点一下就能填进输入框
  const QUICK_MASTER = [
    "可以，就按这张基准图改其它颜色",
    "领口再小一点，别太大",
    "颜色偏亮了，往效果图的颜色靠",
    "印花和文字必须原样，不要重画",
    "这条要记住，以后这个款都这样做",
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
    pollEpoch: 0,
    pollController: null,
    jobLoading: false,
    busy: false,
    job: null,
    selected: new Set(),
    harnessMode: null,
    harnessBusy: false,
    harnessOpening: false,
    harnessSubmittedJob: "",
    harnessStreamDone: false,
    harnessHasResult: false,
    harnessContextKey: "",
    reworkMeta: null,
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

  function priceOf(model, size, providerName) {
    const provider=state.providers.find(p=>p.name===(providerName||$("provider").value));
    if(provider?.fixed_unit_price!==undefined&&provider.fixed_unit_price!==null){
      return {unit:Number(provider.fixed_unit_price),tier:"",tiered:false,fallback:false,tiers:{},other:null,override:true};
    }
    const override=provider?.model_prices?.[model];
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
    if (!reduce && el.animate) el.animate([{opacity:.5,transform:"translateY(3px)"},{opacity:1,transform:"none"}],{duration:180,easing:"ease-out"});
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
      $("whoRole").textContent = data.admin ? "管理员" : "成员";
      $("profileSession").textContent = "已登录";
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
    bind();
    window.WorkbenchUI.ready({state,updateSummary,switchView,showPrompt,toast,downloadUrl,jobPhase});
    window.AIWorkflow?.bind({open:()=>openDrawer("harness"),close:closeHarness,results:()=>{switchView("gallery");window.WorkbenchUI.mobilePane("results");closeHarness();}});
    const pricingReady=loadPricing();
    const jobs=[
      loadSession(),
      api("/api/health").then(health=>{
        $("healthLine").textContent=`${health.types} 款 · ${health.providers_ui||health.providers} 接口`;
        $("connectionLabel").textContent="服务已连接";
      }).catch(()=>{$("connectionLabel").textContent="服务连接失败";}),
      api("/api/skill").then(skill=>{
        state.skill=skill;restoreStyles();renderStyles();
        if(skill.warnings?.length){$("skillWarn").hidden=false;$("skillWarn").textContent=`解析提示 ${skill.warnings.length} 条`;$("skillWarn").title=skill.warnings.join("\n");}
      }).catch(err=>{toast("bad",`款式库读取失败：${err.message}`);$("styleList").textContent="款式库读取失败，请刷新页面重试";}),
      api("/api/providers").then(async data=>{
        state.providers=data.providers||[];state.ui=data.ui||{};state.defaults=data.defaults||{};
        await pricingReady;renderProviders();restoreSettings();loadGate();
      }).catch(err=>toast("bad",`生图接口读取失败：${err.message}`)),
      pricingReady,
    ];
    await Promise.allSettled(jobs);
    document.body.classList.remove("booting");
    loadHistory();refreshCacheInfo();
    const params=new URLSearchParams(location.search);
    if(params.get("denied")==="admin")toast("bad","管理后台只对管理员开放，请联系管理员开通");
    const deep=params.get("job");if(deep)watchJob(deep);
    if(location.hash==="#logs")switchView("logs");
    window.addEventListener("hashchange",()=>{if(location.hash==="#logs")switchView("logs");else if(location.hash==="#gallery")switchView("gallery");});
    updateSummary();if(params.get("panel")==="prompt")showPrompt();
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
    if (!type) return "";
    // 背心没有袖子、短裤没有袖口：这两类标「无袖 / 下装」，别再用平口/罗纹那套上衣说法。
    // 注意顺序：短裤的 sleeve 也是 none，必须先判「下装」，否则会被标成「无袖」。
    if (type.kind === "bottom") {
      return ` <span class="cuff-tag plain" title="下装款：松紧腰 + 两条裤腿">下装</span>`;
    }
    if (type.kind === "vest" || type.sleeve === "none") {
      return ` <span class="cuff-tag plain" title="无袖款：袖笼是包边，没有任何袖子">无袖</span>`;
    }
    if (!type.cuff_zh) return "";
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

  let openStyle=0;
  function miniSwatches(item) {
    return (styleType(item.slug)?.colors||[]).filter(c=>item.colors.has(c.label)).slice(0,5)
      .map(c=>`<i style="background:${esc(c.hex)}" title="${esc(c.name_zh)}"></i>`).join("");
  }
  function renderStyles() {
    const box=$("styleList");if(!box||!state.skill)return;
    const slugs=Object.keys(state.skill.types);
    box.innerHTML=state.styles.map((item,index)=>{
      const type=styleType(item.slug),colors=type?.colors||[],expanded=index===openStyle;
      // 27 个款式按品类分组（上衣 / 背心 / 下装），下拉菜单不再是三十条平铺
      const groups=[["上衣",[]],["背心",[]],["下装",[]]];
      slugs.forEach(slug=>{
        const t=styleType(slug),used=state.styles.some((s,i)=>i!==index&&s.slug===slug);
        const group=groups.find(g=>g[0]===(t.kind_zh||"上衣"))||groups[0];
        group[1].push(`<option value="${esc(slug)}"${slug===item.slug?" selected":""}${used?" disabled":""}>${esc(t.display_name)} · ${t.colors.length} 色</option>`);
      });
      const options=groups.filter(g=>g[1].length).map(g=>`<optgroup label="${g[0]}（${g[1].length}）">${g[1].join("")}</optgroup>`).join("");
      const chips=colors.map(color=>`<button class="chip${item.colors.has(color.label)?" sel":""}" type="button" data-color="${esc(color.label)}" title="${esc(color.name_zh)} ${esc(color.hex)}${color.effect?" · 有官方效果参考":""}" aria-pressed="${item.colors.has(color.label)}"><span class="dot" style="background:${esc(color.hex)}"></span>${esc(color.name_zh)}</button>`).join("");
      return `<div class="style-block${expanded?" expanded":""}" data-index="${index}">
        <button class="style-summary" type="button" data-toggle-style="${index}" aria-expanded="${expanded}" aria-controls="style-editor-${index}"><span class="style-mini style-number">${index+1}</span><span class="style-summary-name"><b>${esc(type?.display_name||item.slug)}</b><span class="mini-swatches">${miniSwatches(item)}</span></span><span class="style-count">${item.colors.size} 色</span><span class="chevron" aria-hidden="true">⌄</span></button>
        <div class="style-editor" id="style-editor-${index}"${expanded?"":" hidden"}><div class="style-top"><label class="field style-field"><span class="lbl">更换款式 ${cuffTag(type)}</span><select class="style-select" aria-label="款式 ${index+1}">${options}</select></label><button class="style-drop" type="button" data-drop="${index}" aria-label="移除${esc(type?.display_name||item.slug)}"${state.styles.length>1?"":" hidden"}><svg><use href="#i-x"/></svg></button></div><div class="style-colors">${chips}</div><div class="style-foot"><span class="hint">${item.colors.size} / ${colors.length} 色</span><span class="style-acts"><button class="tiny-button" type="button" data-pick="all">全选</button><button class="tiny-button" type="button" data-pick="none">清空</button></span></div></div></div>`;
    }).join("");
    updateSummary();
  }
  function syncStyleColors(index) {
    const item=state.styles[index],card=$("styleList").querySelector(`[data-index="${index}"]`);if(!item||!card)return;
    card.querySelectorAll("[data-color]").forEach(chip=>{const on=item.colors.has(chip.dataset.color);chip.classList.toggle("sel",on);chip.setAttribute("aria-pressed",String(on));});
    card.querySelector(".style-count").textContent=`${item.colors.size} 色`;
    card.querySelector(".mini-swatches").innerHTML=miniSwatches(item);
    card.querySelector(".style-foot .hint").textContent=`${item.colors.size} / ${allLabels(item.slug).length} 色`;
    updateSummary();
  }

  function addStyle() {
    if (!state.skill) return;
    const slugs = Object.keys(state.skill.types);
    const used = new Set(state.styles.map((s) => s.slug));
    const next=slugs.find(slug=>!used.has(slug));
    if(!next){toast("ok","所有款式已添加");return;}
    openStyle=state.styles.length;
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
      toast("bad","这个款式已经在列表中");renderStyles();return;
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
      if(event.target.closest("[data-toggle-style]")){
        openStyle=openStyle===index?-1:index;
        box.querySelectorAll(".style-block").forEach(row=>{const on=Number(row.dataset.index)===openStyle;row.classList.toggle("expanded",on);row.querySelector(".style-summary").setAttribute("aria-expanded",String(on));row.querySelector(".style-editor").hidden=!on;});
        return;
      }
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
        syncStyleColors(index);
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
    syncStyleColors(index);
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
    $("outputCount").textContent=state.skill?`${items} 张原图 × ${colors} 个款色 = ${items*colors} 张成品`:"";

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
    $("startBtn").disabled = Boolean(startBlockedReason());
    syncActionControls();
    window.WorkbenchUI?.syncConfig();
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
    const primary=state.providers.find(p=>p.default)||state.providers[0];
    const policy="primary-v1",migrate=restore("providerPolicy","")!==policy;
    const saved=migrate?primary?.name:restore("provider",primary?.name);
    if(migrate){store("providerPolicy",policy);store("provider",saved);}
    const ready=state.providers.filter(p=>p.ready);
    const preferred=state.providers.find(p=>p.name===saved)||primary||ready[0]||state.providers[0];
    if (preferred) select.value = preferred.name;
    select.addEventListener("change", () => {
      store("provider", select.value);
      applyProvider();
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

  let modelRequest=0;
  async function loadModels(providerName,refresh) {
    const request=++modelRequest,select=$("model"),provider=state.providers.find(p=>p.name===providerName)||{};
    const wantedBefore=restore(`model.${providerName}`,provider.model||"");
    $("modelManual").value=restore(`modelManual.${providerName}`,"");
    select.disabled=true;updateSummary();
    try {
      const data=await api(`/api/models?provider=${encodeURIComponent(providerName)}&refresh=${refresh?"true":"false"}`);
      if(request!==modelRequest||$("provider").value!==providerName)return;
      const rows=(data.models||[]).filter(m=>m.image);state.modelRows=rows;select.replaceChildren();
      rows.forEach(model=>{const option=document.createElement("option");option.value=model.id;option.dataset.model=model.id;select.appendChild(option);});
      const manual=document.createElement("option");manual.value="__manual__";manual.textContent="其他（手填模型名）";select.appendChild(manual);
      const choices=[wantedBefore,provider.model,data.current];
      const wanted=wantedBefore==="__manual__"?"__manual__":choices.find(id=>rows.some(row=>row.id===id))||(rows[0]?.id||"__manual__");
      select.value=wanted;$("modelManual").hidden=wanted!=="__manual__";store(`model.${providerName}`,wanted);renderModelLabels();
    } catch(err) {
      if(request!==modelRequest||$("provider").value!==providerName)return;
      select.replaceChildren();state.modelRows=[];
      if(wantedBefore&&wantedBefore!=="__manual__"){const option=document.createElement("option");option.value=wantedBefore;option.dataset.model=wantedBefore;select.appendChild(option);}
      const manual=document.createElement("option");manual.value="__manual__";manual.textContent="其他（手填模型名）";select.appendChild(manual);
      select.value=wantedBefore||"__manual__";$("modelManual").hidden=select.value!=="__manual__";renderModelLabels();
      toast("bad",`模型列表读取失败，已保留上次设置：${err.message}`);
    } finally {
      if(request===modelRequest&&$("provider").value===providerName){select.disabled=false;updateSummary();}
    }
  }

  async function loadPricing() {
    try {
      const [data,usage]=await Promise.all([api("/api/pricing"),api("/api/usage").catch(()=>null)]);
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
      if(usage)state.usage=usage;
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
  async function addFiles(fileList) {
    const entries=[];
    for(const file of [...fileList]){
      if(!/^image\/(png|jpeg|webp)$/.test(file.type)){toast("bad",`${file.name}：请选择 PNG、JPG 或 WebP`);continue;}
      if(file.size>20*1024*1024){toast("bad",`${file.name}：原图不能超过 20 MB`);continue;}
      const entry={file,url:"",name:file.name,loading:true};state.files.push(entry);entries.push(entry);
    }
    renderThumbs();
    for(const entry of entries){
      try{
        const bitmap=await createImageBitmap(entry.file),ratio=Math.min(160/bitmap.width,200/bitmap.height,1);
        const canvas=document.createElement("canvas");canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
        canvas.getContext("2d").drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();
        const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",.8));if(!blob)throw new Error("预览生成失败");
        if(!state.files.includes(entry))continue;
        entry.url=URL.createObjectURL(blob);entry.loading=false;
      }catch(_){const index=state.files.indexOf(entry);if(index>=0)state.files.splice(index,1);toast("bad",`${entry.name}：图片无法读取，请重新选择`);}
      renderThumbs();
    }
  }
  function pasteImages(event) {
    const editing=element=>element instanceof Element&&(element.isContentEditable||element.closest('textarea,select,[role="textbox"],#harnessDrawer')||element.closest('input:not([type="button"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="range"]):not([type="submit"]):not([type="reset"]):not([type="color"])'));
    if(event.defaultPrevented||editing(event.target)||editing(document.activeElement)||!event.clipboardData)return;
    const files=[],mirrors=new Map(),key=file=>JSON.stringify([file.name,file.type,file.size,file.lastModified]);
    for(const item of [...(event.clipboardData.items||[])]){
      if(item.kind!=="file"||!item.type.startsWith("image/"))continue;
      const file=item.getAsFile();if(!file)continue;
      files.push(file);const id=key(file);mirrors.set(id,(mirrors.get(id)||0)+1);
    }
    for(const file of [...(event.clipboardData.files||[])]){
      if(!file.type.startsWith("image/"))continue;
      const id=key(file),remaining=mirrors.get(id)||0;
      if(remaining){mirrors.set(id,remaining-1);continue;}
      files.push(file);
    }
    if(!files.length)return;
    let next=[...state.files,...files].reduce((max,entry)=>Math.max(max,Number(/^item-(\d+)(?:_|\.)/i.exec(entry.name)?.[1])||0),0)+1;
    const named=files.map(file=>{
      if(file.name&&!/^(?:image|blob|clipboard|untitled)(?:\.[^.]+)?$/i.test(file.name))return file;
      const ext=({"image/png":"png","image/jpeg":"jpg","image/webp":"webp"})[file.type]||file.type.slice(6).replace(/[^a-z0-9]/gi,"")||"png";
      return new File([file],`item-${String(next++).padStart(2,"0")}_view-01.${ext}`,{type:file.type,lastModified:file.lastModified});
    });
    event.preventDefault();
    addFiles(named);
  }

  function renderThumbs() {
    const box=$("thumbs");box.replaceChildren();
    state.files.forEach((entry,index)=>{
      const wrap=document.createElement("div");wrap.className=`thumb${entry.loading?" uploading":""}`;
      wrap.innerHTML=`${entry.url?`<img src="${entry.url}" alt="素材 ${index+1}" decoding="async"/>`:""}<span class="idx">${index+1}</span><button type="button" aria-label="移除第 ${index+1} 张">×</button>`;
      wrap.querySelector("button").addEventListener("click",event=>{event.preventDefault();state.files.splice(index,1);if(entry.url)URL.revokeObjectURL(entry.url);state.draftId="";renderThumbs();});box.appendChild(wrap);
    });
    $("fileCount").textContent=state.files.length?`${state.files.length} 张`:"未选择";state.draftId="";updateSummary();
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
  async function ensureDraft(){
    if(state.draftId)return state.draftId;
    const files=state.files.map(entry=>entry.file),form=new FormData();
    files.forEach(file=>form.append("files",file,file.name));
    const data=await api("/api/drafts",{method:"POST",body:form});
    if(files.length!==state.files.length||files.some((file,index)=>file!==state.files[index]?.file))throw new Error("上传期间原图已变化，本次旧草稿已忽略，请重新点击生成");
    state.draftId=data.draft_id;return state.draftId;
  }

  /* ------------------------------------------------------- job creation --- */
  async function createJob(extraRequirements, {fromHarness=false}={}) {
    if (state.busy) return;
    const blocked=startBlockedReason();if(blocked){toast("bad",blocked);return;}
    saveSettings();
    state.busy = true;
    updateSummary();
    $("startBtn").classList.add("loading");
    $("startBtn").textContent = "提交中…";
    window.WorkbenchUI.beginSubmission(selectionsPayload());
    if(fromHarness)window.AIWorkflow?.phase('submitting');
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
      window.WorkbenchUI.endSubmission(true);
      if(fromHarness){state.harnessSubmittedJob=data.job.id;window.AIWorkflow?.submitted(data.job.id);}else closeHarness();
      watchJob(data.job.id, data.job);
      loadHistory();
      loadGate();
      setTimeout(loadPricing, 1500);   // 先让结果区渲染出来，用量芯片稍后再刷
      const est = data.job.params.estimate || {};
      toast("ok", est.priced
        ? `已开工：${data.job.stats.total} 张，预计 ${money(est.total, est.currency)}`
        : "已开工");
    } catch (err) {
      window.WorkbenchUI.endSubmission(false);
      toast("bad", `创建任务未确认：${err.message}。请先查看任务记录确认，避免重复提交。`);
      if(fromHarness)window.AIWorkflow?.error(`提交结果未确认：${err.message}。请先查看任务记录，勿重复提交。`);
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
  function setJobLoading(loading) {
    state.jobLoading=loading;
    if(loading)$("jobDuration").hidden=true;
    $("galleryPane").inert=loading;$("logPane").inert=loading;
    $("galleryPane").setAttribute("aria-busy",String(loading));
    document.body.classList.toggle("job-loading",loading);
    let surface=$("jobLoadingSurface");
    if(loading){
      if(!surface){surface=document.createElement("p");surface.id="jobLoadingSurface";surface.className="job-loading-surface";surface.setAttribute("role","status");$("galleryScroll").prepend(surface);}
      surface.textContent="正在切换任务，读取完成后即可操作";
    }else surface?.remove();
    updateSummary();
  }

  function watchJob(jobId, seed) {
    const epoch=++state.pollEpoch,changed=state.jobId!==jobId;
    state.pollController?.abort();
    const controller=new AbortController();state.pollController=controller;
    if(state.pollTimer)clearTimeout(state.pollTimer);state.pollTimer=null;
    state.jobId=jobId;
    if(changed){state.selected=new Set();$("lightbox").close();}
    state.pollStartedAt=Date.now();
    const current=()=>state.pollEpoch===epoch&&state.jobId===jobId;
    try{
      const url=new URL(location.href);url.searchParams.set("job",jobId);history.replaceState(null,"",url);
    }catch(_){}
    if(seed&&seed.id===jobId)renderJob(seed);
    else{
      setJobLoading(true);
      $("jobTitle").textContent="正在读取任务";$("jobStage").textContent="等待服务器返回任务详情";
    }
    const tick=async()=>{
      if(!current())return;
      state.pollTimer=null;
      if(document.hidden)return;
      let delay=1200;
      try{
        const data=await api(`/api/jobs/${jobId}`,{signal:controller.signal});
        if(!current())return;
        if(!data.job||data.job.id!==jobId)throw new Error("任务编号不匹配");
        renderJob(data.job);
        if(TERMINAL.includes(data.job.status)){
          loadHistory();loadGate();maybeLoadPricing();return;
        }
        const total=(data.job.stats||{}).total||0,elapsed=(Date.now()-state.pollStartedAt)/1000;
        if(total>12)delay=2400;else if(total>6)delay=1600;
        if(elapsed>60)delay=Math.max(delay,2000);
        if(elapsed>180)delay=Math.max(delay,3000);
      }catch(err){
        if(!current()||err.name==="AbortError")return;
        const phase=state.job?.id===jobId?jobPhase(state.job):null;
        if(phase?.terminal)return;
        $("jobStage").textContent=`${phase?.packaging?"生成完成，正在打包 · ":""}状态更新暂时失败，正在重试：${err.message}`;
        if(state.jobLoading&&$("jobLoadingSurface"))$("jobLoadingSurface").textContent=`读取任务失败，正在重试：${err.message}`;
        delay=2500;
      }
      if(current())state.pollTimer=setTimeout(tick,delay);
    };
    tick();
  }

  // 价格/用量不需要每次任务结束都刷（飞书那边有 60s 缓存，刷了也不省钱）
  function maybeLoadPricing(force = false) {
    const now = Date.now();
    if (!force && state.pricingAt && now - state.pricingAt < 45000) return;
    loadPricing();
  }

  function formatJobDuration(seconds){
    const value=Math.max(0,Number(seconds)||0),hours=Math.floor(value/3600),minutes=Math.floor(value%3600/60),remaining=(value%60).toFixed(1);
    return hours?`${hours}小时 ${minutes}分 ${remaining}秒`:minutes?`${minutes}分 ${remaining}秒`:`${remaining}秒`;
  }

  function jobPhase(job) {
    const stats=job.stats||{},terminal=TERMINAL.includes(job.status);
    const completed=Number(stats.done||0)+Number(stats.skipped||0),total=Number(stats.total||0);
    const settled=total>0&&completed+Number(stats.failed||0)+Number(stats.canceled||0)>=total&&!Number(stats.running||0)&&!Number(stats.pending||0)&&!Number(stats.queued||0)&&!(job.tasks||[]).some(task=>["pending","queued","running"].includes(task.status));
    const packaging=!terminal&&(settled||/packag|finaliz|打包|整理/.test(`${job.status} ${job.stage||""}`));
    return {terminal,packaging,successful:job.status==="done"&&!Number(stats.failed||0),progress:Math.max(0,Math.min(terminal?100:99,Number(job.progress)||0))};
  }

  /* ---------------------------------------------------------- 基准图审核 -- */
  // 先出基准 → 运营确认 → 再按基准改色。审核期由后端 job.status="review" + job.review 给出。
  function reviewInfo(job) {
    const info = (job && job.review) || null;
    return info && info.enabled ? info : null;
  }
  let reviewBound = false;
  function bindReviewActions() {
    if (reviewBound) return;
    reviewBound = true;
    $("approveMasterBtn")?.addEventListener("click", (event) => approveMaster(event.currentTarget));
    $("talkMasterBtn")?.addEventListener("click", () => talkMaster());
  }
  function renderReview(job) {
    bindReviewActions();
    const bar = $("reviewBar");
    if (!bar) return;
    const info = reviewInfo(job);
    const masters = (job?.tasks || []).filter((t) => t.master && t.thumb_small);
    const show = Boolean(info && (info.awaiting || info.pending_ids?.length));
    bar.hidden = !show;
    if (!show) return;
    const stage = $("reviewStage"), note = $("reviewNote");
    if (info.awaiting) {
      stage.textContent = `这张满意就点「确定」，之后才会按它给其它 ${info.derived_total || 0} 个颜色改色；不满意就在右边对着说。`;
      note.textContent = "确认前不会出其它颜色，也不花钱";
      note.dataset.warn = "0";
    } else if (info.phase === "derived") {
      stage.textContent = `基准已确认，正在按它改其它颜色（${info.derived_done || 0}/${info.derived_total || 0}）`;
      note.textContent = "改色中…";
      note.dataset.warn = "0";
    } else {
      stage.textContent = "基准图还没出来，稍等一下";
      note.textContent = "";
    }
    const box = $("reviewThumbs");
    if (box) {
      box.innerHTML = masters.map((task) => `
        <figure>
          <img src="${esc(task.thumb_small)}" alt="${esc(task.color_name || task.color)} 基准图"
               data-preview="${esc(task.preview || task.thumb || "")}" data-task="${esc(task.id)}" />
          <figcaption>${esc(task.color_name || task.color)}${task.approved ? " · 已确认" : " · 待确认"}</figcaption>
        </figure>`).join("");
      box.querySelectorAll("img[data-preview]").forEach((img) => {
        img.addEventListener("click", () => openLightbox?.(img.dataset.preview, img.dataset.task));
      });
    }
    const approve = $("approveMasterBtn");
    if (approve) {
      approve.disabled = !info.can_approve;
      approve.textContent = info.phase === "derived" ? "已确认" : "确定，按它改色";
    }
  }

  async function approveMaster(button) {
    const jobId = state.jobId;
    if (!jobId || state.job?.id !== jobId) return;
    const info = reviewInfo(state.job);
    if (!info || !info.can_approve) { toast("bad", "现在还没有可确认的基准图"); return; }
    const ids = [...(info.pending_ids || [])].length ? [...info.pending_ids] : [...(info.master_ids || [])];
    await withLoading(button, async () => {
      try {
        const res = await api(`/api/jobs/${jobId}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ task_ids: ids, note: state.reviewNote || "", learn: true }),
        });
        const learned = (res?.learned || []).length;
        toast("ok", `基准图已确认，开始按它改色${learned ? `（顺手记了 ${learned} 条款式规则）` : ""}`);
        if (res?.job) renderJob(res.job);
        watchJob(jobId);
      } catch (err) {
        toast("bad", err.message);
      }
    }, "确认中…");
  }

  function talkMaster() {
    if (!state.job?.id) { toast("bad", "还没有可讨论的任务"); return; }
    const info = reviewInfo(state.job);
    if (!info) { toast("bad", "这一批没有基准图审核"); return; }
    openHarness("master", info.pending_ids?.length ? info.pending_ids : info.master_ids, state.job.id);
  }

  function renderJob(job) {
    if(job.id!==state.jobId)return;
    state.job=job;
    renderReview(job);
    const phase=jobPhase(job);
    if(state.jobLoading)setJobLoading(false);
    const params = job.params || {};
    const isRework = params.kind === "rework";
    const styleCount = (params.selections || []).length;
    $("jobTitle").textContent = isRework
      ? `改图返工 · ${job.stats.total} 张`
      : styleCount > 1
        ? `${styleCount} 个款式 · ${job.stats.total} 张`
        : `${params.garment_name || "任务"} · ${job.stats.total} 张`;
    $("jobStage").textContent = phase.successful?"生成完成":phase.packaging?(params.packaging_progress?`生成完成，校验打包 ${params.packaging_progress}`:/打包|校验|packag/i.test(job.stage||"")?job.stage:"生成完成，正在打包"):job.stage||job.status;
    const duration=$("jobDuration"),end=Number(job.finished_at||job.updated_at||0);
    const seconds=typeof job.duration==="number"?job.duration:end-Number(job.created_at||end);
    duration.hidden=!TERMINAL.includes(job.status)||!Number.isFinite(seconds)||seconds<0;
    duration.textContent=duration.hidden?"":` · 总耗时 ${formatJobDuration(seconds)}`;

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
    bar.setAttribute("aria-valuenow", String(Math.round(phase.progress)));
    bar.setAttribute("aria-valuetext",phase.packaging?"图片已处理完成，正在整理打包":`${Math.round(phase.progress)}%`);
    bar.style.setProperty("--progress",String(phase.progress/100));
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
    if (job.error&&!phase.successful) queueParts.push(job.error);
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
        window.WorkbenchUI?.observeCard(entry.el,task);
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
    syncSelection(true);
    window.WorkbenchUI?.syncJob();
    updateSummary();
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
      <div class="shot flash-card"><div class="card-turner"><div class="card-back"><div class="back-top"><span>OBSIDIAN</span><span class="back-code">${esc(task.view||"")}</span></div><div class="back-emblem" aria-hidden="true">${icon("i-spark",34)}</div><b class="back-status">等待生成</b><span class="back-message">已为这张成品保留位置</span><span class="back-meter" aria-hidden="true"><i></i></span></div><div class="card-front"></div></div></div>
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
      front:el.querySelector(".card-front"),
      backText:el.querySelector(".back-status"),
      backMessage:el.querySelector(".back-message"),
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

  // 列表仅按视口加载服务端缩略图；灯箱预览和原图下载各用独立地址。
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
    }, {root:$("galleryScroll"),rootMargin:"180px 0px"});
    return imageIO;
  }

  function mountImage(entry,url,task) {
    const previous=entry.front.querySelector("img");if(previous)lazyObserver()?.unobserve(previous);
    const img=document.createElement("img");img.decoding="async";img.loading="eager";
    img.alt=`${task.garment_name||""} · ${task.color_name} · ${task.item}`;img.width=520;img.height=650;
    window.WorkbenchUI.resetCard(entry);entry.shot.setAttribute("aria-busy","true");
    img.onload=async()=>{try{await img.decode();}catch(_){}if(!img.isConnected)return;entry.shot.setAttribute("aria-busy","false");window.WorkbenchUI.revealCard(entry,task);};
    img.onerror=()=>{if(!img.isConnected)return;entry.shot.setAttribute("aria-busy","false");const retry=document.createElement("button");retry.type="button";retry.className="front-retry";retry.textContent="图片读取失败 · 点击重载";retry.onclick=event=>{event.stopPropagation();mountImage(entry,url,task);};entry.front.replaceChildren(retry);entry.el.classList.add("flash-revealed");};
    entry.front.replaceChildren(img);img.dataset.src=url;const io=lazyObserver();if(io)io.observe(img);else img.src=url;
  }

  function refreshCacheInfo() { /* 已取消 IndexedDB 图片缓存 */ }

  function updateCard(entry, task, job) {
    const signature=[task.status,task.queue_ahead,task.duration,task.thumb,task.thumb_small,task.version,task.error,task.out,task.master,task.master_task,task.person_alert,state.selected.has(task.id)].join("|");
    if(entry.renderSignature===signature)return;entry.renderSignature=signature;
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
      const ahead=task.queue_ahead==null?NaN:Number(task.queue_ahead);
      stateText=Number.isFinite(ahead)&&ahead>0?`排队中，还有 ${ahead} 张`:"排队中";
    } else if (task.duration) {
      stateText += ` ${task.duration}s`;
    }
    if (entry.state.textContent !== stateText) entry.state.textContent = stateText;

    // 只有真的换了图（版本变化）才动 <img>：否则进度轮询会不停重新解码
    const imageUrl=task.thumb_small||(task.thumb?`${task.thumb}${task.thumb.includes("?")?"&":"?"}v=${taskVersion(task,job)||0}`:"");
    if (imageUrl) {
      if (entry.thumb !== imageUrl) {
        entry.thumb = imageUrl;
        entry.el.classList.add("settled");
        entry.el.dataset.full=task.preview||`${task.thumb}?v=${taskVersion(task,job)||0}`;
        mountImage(entry, imageUrl, task);
      }
    } else if (entry.thumb) {
      entry.front.replaceChildren();window.WorkbenchUI.resetCard(entry);delete entry.el.dataset.full;
      entry.thumb = "";
      entry.el.classList.add("settled");
    } else {
      // 背面状态由业务展示层统一处理（基准依赖、排队、生成与异常）。
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

    if(entry.rework.disabled!==(status!=="done"))entry.rework.disabled=status!=="done";

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
    window.WorkbenchUI.updateCard(entry,task);
  }

  function syncSelection(onlyControls=false) {
    const ready=(state.job?.tasks||[]).filter(t=>t.out);
    if(!onlyControls)state.cards.forEach(entry=>{const on=state.selected.has(entry.el.dataset.task);if(entry.el.classList.contains("sel")!==on)entry.el.classList.toggle("sel",on);if(entry.input.checked!==on)entry.input.checked=on;});
    const count=state.selected.size,label=`已选 ${count} 张`;
    if($("selCount").textContent!==label)$("selCount").textContent=label;
    $("batchReworkBtn").disabled=count===0||Boolean(submissionBlockedReason());$("batchExportBtn").disabled=state.jobLoading||(ready.length===0&&count===0);
    $("selAll").checked=ready.length>0&&count===ready.length;
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
    if(state.jobLoading||!state.jobId||state.job?.id!==state.jobId)return;
    const jobId=state.jobId;
    await withLoading($("cancelBtn"), async () => {
      try { await api(`/api/jobs/${jobId}/cancel`, { method: "POST" }); }
      catch (err) { toast("bad", err.message); }
    }, "取消中…");
  }
  async function retryTasks(button, form) {
    const blocked=submissionBlockedReason();if(blocked){toast("bad",blocked);return;}
    if(!state.jobId||state.job?.id!==state.jobId)return;
    const jobId=state.jobId;state.busy=true;updateSummary();
    try{
      await withLoading(button,()=>api(`/api/jobs/${jobId}/retry`,{method:"POST",...(form?{body:form}:{})}),"重试中…");
      if(state.jobId===jobId)watchJob(jobId);
    }catch(err){toast("bad",`重试未确认：${err.message}。请先查看任务记录，避免重复提交。`);}
    finally{state.busy=false;updateSummary();}
  }
  async function retryJob(){await retryTasks($("retryBtn"));}
  async function retryLimited(){const form=new FormData();form.append("only","rate_limit");await retryTasks($("retryLimitedBtn"),form);}
  async function retrySingle(taskId){const form=new FormData();form.append("task_ids",taskId);await retryTasks(null,form);}
  function exportSelection() {
    if(state.jobLoading||!state.jobId||state.job?.id!==state.jobId)return;
    const ids = [...state.selected];
    const query = ids.length ? `?tasks=${encodeURIComponent(ids.join(","))}` : "";
    window.location.href = `/api/jobs/${state.jobId}/export${query}`;
    toast("ok", ids.length ? `正在导出 ${ids.length} 张原图` : "正在导出全部成品（原图）");
  }
  async function showPrompt() {
    openDrawer("prompt");$("promptBody").innerHTML=skeletonHTML(5,26);
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
          requirement: ($("briefInput").value||state.requirements||"").trim(),
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

  const panelCloseTimers=new Map();
  function openDrawer(which) {
    const isPrompt=which==="prompt",scrim=$(isPrompt?"promptScrim":"harnessScrim"),panel=$(isPrompt?"promptDrawer":"harnessDrawer");
    const other=$(isPrompt?"harnessDrawer":"promptDrawer"),otherScrim=$(isPrompt?"harnessScrim":"promptScrim");
    other.hidden=true;other.classList.remove("on");otherScrim.hidden=true;otherScrim.classList.remove("on");
    clearTimeout(panelCloseTimers.get(panel.id));
    const epoch=String(Number(panel.dataset.panelEpoch||0)+1);panel.dataset.panelEpoch=epoch;
    scrim.hidden=false;panel.hidden=false;
    requestAnimationFrame(()=>{if(panel.dataset.panelEpoch!==epoch)return;scrim.classList.add("on");panel.classList.add("on");window.AIWorkflow?.refresh();});
    if(!isPrompt)setTimeout(()=>{if(panel.dataset.panelEpoch===epoch&&!panel.hidden)$("harnessInput").focus({preventScroll:true});},240);
  }
  function closePanel(panelId,scrimId) {
    const panel=$(panelId),scrim=$(scrimId),epoch=String(Number(panel.dataset.panelEpoch||0)+1);panel.dataset.panelEpoch=epoch;
    panel.classList.remove("on");scrim.classList.remove("on");clearTimeout(panelCloseTimers.get(panelId));
    panelCloseTimers.set(panelId,setTimeout(()=>{if(panel.dataset.panelEpoch===epoch){panel.hidden=true;scrim.hidden=true;}},240));
  }
  function closeHarness(){closePanel("harnessDrawer","harnessScrim");window.AIWorkflow?.refresh();}
  function closePrompt() { closePanel("promptDrawer", "promptScrim"); }

  /* ----------------------------------------------------------- harness chat */
  function chatBubble(role, text) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${role}`;
    wrap.innerHTML = `<span class="who">${role === "user" ? "你" : "AI 工作室"}</span><div class="bubble"></div>`;
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
    wrap.innerHTML = `<span class="who">AI 工作室</span><div class="bubble"><i></i><i></i><i></i></div>`;
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
    const failed=/^(失败|错误|工具.+执行失败|Error:)/i.test(body);
    line.classList.add(failed?"failed":"done");
    line.querySelector(".spin").outerHTML=`<span class="ok">${icon(failed?"i-alert":"i-check",12)}</span>`;
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
    state.streamedText="";state.harnessSubmittedJob="";state.harnessHasResult=false;state.harnessStreamDone=false;
    $("harnessInput").value="";
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

  function harnessContextKey(){
    // 返工 / 基准图审核都是「对着已有成品说话」，不参与「素材已变化」的判定
    if(state.harnessMode==="rework"||state.harnessMode==="master")return "";
    return JSON.stringify({files:state.files.map(e=>[e.file.name,e.file.size,e.file.lastModified]),selections:selectionsPayload(),provider:$("provider").value,model:currentModel(),size:currentSize(),quality:$("quality").value,subject:$("subject").value,pattern:currentPatternMode(),format:currentOutputFormat(),brief:briefText()});
  }
  function harnessContextChanged(){return state.harnessMode==="intake"&&!!state.harnessContextKey&&state.harnessContextKey!==harnessContextKey();}
  async function openHarness(mode,taskIds,jobId){
    // master 模式：基准图审核期（对着基准图说改哪里 / 说确定），不要求重新准备素材
    if(mode==="master"){
      const target=(jobId||state.jobId);
      if(!target||!state.job||state.job.id!==target){toast("bad","请先选中这条任务");return false;}
      const info=reviewInfo(state.job);
      if(!info){toast("bad","这条任务没有基准图审核");return false;}
      state.harnessMode="master";state.reworkTargets=taskIds||info.master_ids||[];state.reworkSourceJob=target;
      const params=state.job?.params||{};
      state.reworkMeta={provider:String(params.provider||""),model:String(params.model||""),size:String(params.size||"auto")};
      resetChat();state.harnessContextKey=harnessContextKey();
      $("harnessTitle").textContent="AI 基准图确认";
      $("harnessSub").textContent="先看基准图：满意就确认改色，不满意直接说改哪里";
      $("harnessApply").textContent="按对话执行";
      $("harnessDefault").textContent="返回画廊";
      $("reworkModel").hidden=true;
      renderQuick(QUICK_MASTER);openDrawer("harness");updateSummary();
      chatBubble("assistant",`这是这批的基准图。满意就说「可以」或点「确定」，我再按它给其它 ${info.derived_total||0} 个颜色改色；不满意就直接说哪里要改，比如「领口再小一点」。`);
      window.AIWorkflow?.start("master");window.AIWorkflow?.phase('idle','对着基准图说明要改的地方，或直接确认。');
      return true;
    }
    const blocked=submissionBlockedReason();if(blocked){toast("bad",blocked);return false;}
    if(mode==="rework"&&(!state.job||state.job.id!==state.jobId))return false;
    if(mode!=="rework"){const reason=startBlockedReason();if(reason){toast("bad",reason);return false;}}
    const targets=mode==="rework"?(taskIds||[...state.selected]):[];
    if(mode==="rework"&&!targets.length){toast("bad","请先选择要改图的成品");return false;}
    state.harnessOpening=true;state.harnessMode=mode;state.reworkTargets=targets;state.reworkSourceJob=state.jobId;
    const params=state.job?.params||{};
    state.reworkMeta={provider:String(params.provider||""),model:String(params.model||""),size:String(params.size||"auto")};
    resetChat();state.harnessContextKey=harnessContextKey();
    if(mode!=="rework"&&state.pendingBrief){$("harnessInput").value=state.pendingBrief;state.pendingBrief="";}
    $("harnessTitle").textContent=mode==="rework"?"AI 改图工作室":"AI 创作工作室";
    $("harnessSub").textContent=mode==="rework"?`对照原始上传图与 ${targets.length} 张待改成品；保留完整正反面构图`:`${state.files.length} 张原图 · ${totalColors()} 个款色 · 先对齐需求，再确认生成`;
    $("harnessApply").textContent=mode==="rework"?"确认返工":"确认并生成";
    $("harnessDefault").textContent=mode==="rework"?"返回画廊":"按默认生成";
    $("reworkModel").hidden=mode!=="rework";
    renderQuick(mode==="rework"?QUICK_REWORK:QUICK_INTAKE);
    window.AIWorkflow?.start(mode);openDrawer("harness");updateSummary();
    try{
      if(mode==="rework"){
        await initReworkModels();
        chatBubble("assistant",`本轮将对照原始上传图与 ${targets.length} 张待改成品。请说明要改的地方；同张原图中的正反面将一起核对。`);
      }else{
        await ensureDraft();
        if(harnessContextChanged())throw new Error("素材或生成设定在准备期间发生变化，请重新点击生成");
        const model=currentModel(),price=priceOf(model,currentSize()),count=expectedCalls();
        const cost=price.unit!==null?`预计 ${money(price.unit*count,state.pricing.currency)}`:"该模型未定价";
        chatBubble("assistant",`${state.styles.length} 个款式 · ${count} 张预计成品 · ${model} · ${cost}。我会先对齐这次要求，明确需要改变和保留的部分。`);
      }
      window.AIWorkflow?.phase('idle',mode==="rework"?'说明需要修改的地方；发送后 AI 将对照原始上传图与当前成品。':'素材已准备，正在进入本轮需求分析。');
      return true;
    }catch(err){
      window.AIWorkflow?.error(`素材准备失败：${err.message}。本轮尚未提交生图，可重新点击生成。`);
      chatBubble("assistant",`素材准备未完成：${err.message}。没有提交生图任务。`);return false;
    }finally{state.harnessOpening=false;updateSummary();}
  }

  /* ------------------------------------------------- 改图：换模型 ---------- */
  // 成品当参考图交给别的模型重画：默认跟原任务同接口同模型，选了别人才换
  function sourceJobMeta() {
    return state.reworkMeta||{provider:"",model:"",size:"auto"};
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

  let reworkModelRequest=0;
  async function loadReworkModels(providerName, preferred) {
    const request=++reworkModelRequest,select=$("reworkModelSel");
    select.disabled=true;select.innerHTML='<option value="">正在读取模型…</option>';syncActionControls();
    let rows=[];
    try{
      const data=await api(`/api/models?provider=${encodeURIComponent(providerName)}&refresh=false`);
      if(request!==reworkModelRequest||$("reworkProvider").value!==providerName)return;
      rows=(data.models||[]).filter(m=>m.image);
    }catch(err){
      if(request!==reworkModelRequest||$("reworkProvider").value!==providerName)return;
      toast("bad",`改图模型读取失败：${err.message}`);
    }
    if(request!==reworkModelRequest||$("reworkProvider").value!==providerName)return;
    select.replaceChildren();
    if(!rows.length){const option=document.createElement("option");option.value="";option.textContent="（这个接口没有可用的图像模型）";select.appendChild(option);}
    for(const model of rows){const option=document.createElement("option");option.value=model.id;option.textContent=model.id;select.appendChild(option);}
    select.value=rows.some(m=>m.id===preferred)?preferred:(rows[0]?.id||"");
    select.disabled=false;
    select.onchange=()=>{renderReworkHint();syncActionControls();};
    renderReworkHint();syncActionControls();
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
    const info = model ? priceOf(model, size, provider) : { unit: null };
    const priceText = info.unit !== null && info.unit !== undefined
      ? `预计 ${money(info.unit * Math.max(1, count), state.pricing.currency)}`
        + `（${money(info.unit, state.pricing.currency)}/张）`
      : "该模型未定价";
    let head;
    if (!switched) head = `沿用 ${model || "原模型"}`;
    else if (meta.model) head = `换模型重画：${meta.model} → ${model || "未选"}`;
    else head = `换成 ${model || "未选"} 重画`;
    hint.textContent = `${head} · ${count} 张 · 回溯原图与成品对照 · ${priceText}`;
    hint.dataset.state = switched ? "switch" : "same";
  }

  async function streamHarness(payload){
    const res=await fetch("/api/harness/stream",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    if(!res.ok||!res.body)throw new Error(`AI 连接失败 HTTP ${res.status}`);
    window.AIWorkflow?.phase('processing','对话已连接，等待 AI 返回本轮分析。');
    const reader=res.body.getReader(),decoder=new TextDecoder();let buffer="";
    function drain(){buffer=buffer.replace(/\r\n/g,"\n");let index;while((index=buffer.indexOf("\n\n"))>=0){const frame=buffer.slice(0,index);buffer=buffer.slice(index+2);handleFrame(frame);}}
    try{
      while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});drain();}
      buffer+=decoder.decode();drain();if(buffer.trim())handleFrame(buffer);
      if(!state.harnessStreamDone&&!state.harnessSubmittedJob)throw new Error("AI 流式连接提前结束，尚未收到本轮完成确认");
    }finally{reader.releaseLock();}
  }

  function acceptHarnessJob(jobId){
    const id=String(jobId||"");if(!id||state.harnessSubmittedJob===id)return;
    state.harnessSubmittedJob=id;window.AIWorkflow?.submitted(id);
    watchJob(id);loadHistory();loadGate();toast("ok","AI 已创建任务，可查看生成进度");
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
    if(event==="error")throw new Error(String(payload.error||payload.text||"AI 返回了错误"));
    if(event==="meta")window.AIWorkflow?.phase('processing','服务端已开始处理本轮需求，工具将按需执行。');

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
      window.AIWorkflow?.stream(payload.text||"");
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
        window.AIWorkflow?.stream(text);
      }
      state.streamedText = "";
    } else if (event === "tool") {
      hideTyping();
      flushType(true);
      if (state.streamBubble) state.streamBubble.classList.remove("streaming");
      state.streamBubble = null;
      toolStart(payload.name);
      window.AIWorkflow?.tool(payload.name,'running');
      showTyping();           // 工具跑完还要再想一轮 → 继续转圈
    } else if (event === "result") {
      toolEnd(payload.name, String(payload.text || ""));
      window.AIWorkflow?.tool(payload.name,'result',String(payload.text||""));
      showTyping();
    } else if (event === "prompt") {
      hideTyping();
      flushType(true);
      if (state.streamBubble) state.streamBubble.classList.remove("streaming");
      state.streamBubble = null;
      state.composedPrompt = payload.prompt || "";
      state.requirements = payload.requirements || "";
      state.harnessHasResult=true;syncActionControls();
      if(payload.job_id)acceptHarnessJob(payload.job_id);
    } else if (event === "done") {
      state.harnessStreamDone=true;
      hideTyping();
      flushType(true);
      if (state.streamBubble) state.streamBubble.classList.remove("streaming");
      state.streamBubble = null;
    } else if (event === "job") {
      if(payload.job_id)acceptHarnessJob(payload.job_id);
    }
  }

  function harnessPayload(text) {
    const payload = {
      mode: state.harnessMode === "master"
        ? "master"
        : (state.harnessMode === "rework" ? "rework" : "intake"),
      instruction: text,
    };
    if (state.harnessMode === "master") {
      payload.job_id = state.reworkSourceJob || state.jobId;
      payload.task_ids = state.reworkTargets || [];
      const params = state.job?.params || {};
      payload.provider = String(params.provider || "");
      payload.model = String(params.model || "");
    } else if (state.harnessMode === "rework") {
      // 改图必须带上对话框里选的那个接口/模型，否则会退回左侧主面板（= 原接口）
      const choice = reworkChoice();
      payload.job_id = state.reworkSourceJob;
      payload.task_ids = state.reworkTargets;
      payload.provider=choice.provider;
      payload.model=choice.model;
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
    payload.subject=$("subject").value;
    payload.size=currentSize();payload.quality=$("quality").value;payload.output_format=currentOutputFormat();
    payload.send_reference=true;payload.shape_lock="master";
    payload.pattern_mode = currentPatternMode();
    payload.history = state.harnessHistory.slice(-10);
    return payload;
  }

  async function sendHarnessMessage(){
    // 基准图审核期：任务状态是 review（不算运行中），这里只确认有任务在审；
    // 否则对话框会被「本轮任务仍在运行」挡死，运营没法确认或提修改。
    const blocked=state.harnessMode==="master"
      ? (state.job&&state.job.id?"":"请先选中基准图待确认的任务")
      : (submissionBlockedReason()||harnessModelBlockedReason());
    if(blocked){toast("bad",blocked);return;}
    if(state.harnessSubmittedJob){closeHarness();return;}
    if(harnessContextChanged()){window.AIWorkflow?.phase('stale');toast("bad","本轮素材或设定已变化，请关闭后重新点击生成");return;}
    if(state.harnessMode!=="rework"&&state.harnessMode!=="master"){
      const reason=startBlockedReason();if(reason){toast("bad",reason);return;}
      if(!state.draftId){window.AIWorkflow?.error("素材尚未准备完成，请重新点击生成。");return;}
    }
    const text=($("harnessInput").value||"").trim();if(!text){toast("bad","请先补充或确认本轮要求");return;}
    chatBubble("user",text);state.harnessHistory.push({role:"user",content:text});state.assistantText="";$("harnessInput").value="";
    state.harnessBusy=true;state.harnessStreamDone=false;state.harnessHasResult=false;
    state.composedPrompt="";state.requirements="";
    window.AIWorkflow?.phase('connecting');updateSummary();$("harnessSend").classList.add("loading");showTyping();
    try{
      await streamHarness(harnessPayload(text));
      if(!state.harnessSubmittedJob){
        const prepared=state.harnessHasResult&&!!(state.harnessMode==="rework"?state.composedPrompt:state.requirements);
        window.AIWorkflow?.phase(prepared?'ready':'waiting');
      }
    }catch(err){
      flushType(true);
      if(!state.harnessSubmittedJob){state.composedPrompt="";state.requirements="";state.harnessHasResult=false;window.AIWorkflow?.error(`AI 连接未完成：${err.message}。请保留本轮对话；若可能已开工，先查看任务记录，勿重复提交。`);toast("bad",`AI 连接提示：${err.message}`);}
    }finally{
      hideTyping();state.harnessBusy=false;$("harnessSend").classList.remove("loading");
      const answer=state.assistantText.trim();if(answer)state.harnessHistory.push({role:"assistant",content:answer});
      state.harnessHistory=state.harnessHistory.slice(-12);state.assistantText="";updateSummary();
    }
  }

  async function harnessDefaultAction() {
    if(state.harnessBusy||state.busy)return;
    if(state.harnessSubmittedJob){closeHarness();return;}
    if (state.harnessMode === "rework") { closeHarness(); return; }
    $("briefInput").value="";state.pendingBrief="";
    await createJob("",{fromHarness:true});
  }

  function briefText() {
    return ($("briefInput") ? $("briefInput").value : "").trim();
  }

  async function beginGeneration(){
    const blocked=startBlockedReason();if(blocked){toast("bad",blocked);return;}
    const brief=briefText();
    if(!brief){state.pendingBrief="";await createJob("");return;}
    const phase=window.AIWorkflow?.snapshot()?.phase;
    if(state.harnessMode==="intake"&&!state.harnessSubmittedJob&&!harnessContextChanged()&&['ready','waiting'].includes(phase)){openDrawer("harness");return;}
    state.pendingBrief=brief;
    if(!await openHarness("intake",[]))return;
    await sendHarnessMessage();
  }

  async function harnessApplyAction() {
    if(state.harnessBusy||state.busy)return;
    if(state.harnessSubmittedJob){closeHarness();return;}
    if(harnessContextChanged()){window.AIWorkflow?.phase('stale');toast("bad","请按当前素材和设定重新分析");return;}
    if(state.harnessMode!=="rework"&&!state.requirements){toast("bad","请先完成 AI 需求对齐");return;}
    if (state.harnessMode === "rework") { await submitRework(); return; }
    await createJob(state.requirements,{fromHarness:true});
  }

  async function submitRework() {
    const blocked=submissionBlockedReason()||harnessModelBlockedReason();if(blocked){toast("bad",blocked);return;}
    if(state.harnessSubmittedJob){closeHarness();return;}
    if (!state.reworkTargets.length) { toast("bad", "没有选中成品"); return; }
    const instruction = ($("harnessInput").value || "").trim() || state.requirements ||
      "按 harness 分析结果调整";
    const choice = reworkChoice();
    const provider=choice.provider,model=choice.model;
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
    form.append("subject",$("subject").value);
    if (state.composedPrompt) form.append("prompt_override", state.composedPrompt);
    form.append("start", "true");
    const sourceJob=state.reworkSourceJob,count=state.reworkTargets.length;
    state.busy=true;updateSummary();
    window.AIWorkflow?.phase('submitting');
    $("harnessApply").disabled = true;
    $("harnessApply").classList.add("loading");
    $("harnessApply").textContent = "提交中…";
    try {
      const data = await api(`/api/jobs/${sourceJob}/rework`, { method: "POST", body: form });
      state.harnessSubmittedJob=data.job.id;window.AIWorkflow?.submitted(data.job.id);
      watchJob(data.job.id, data.job);
      loadHistory();
      loadGate();
      setTimeout(loadPricing, 1500);
      toast("ok", `已提交 ${count} 张返工`);
    } catch (err) {
      toast("bad", `提交返工未确认：${err.message}。请先查看任务记录，避免重复提交。`);
      window.AIWorkflow?.error(`提交返工未确认：${err.message}。请先查看任务记录，勿重复提交。`);
    } finally {
      $("harnessApply").classList.remove("loading");
      state.busy=false;
      $("harnessApply").textContent = "确认返工";
      updateSummary();
    }
  }

  /* ------------------------------------------------------------------ bind */
  function submissionBlockedReason(){
    if(state.busy)return "正在提交，请等待服务器确认";
    if(state.harnessBusy)return "需求分析仍在进行，请等待本轮分析结束";
    if(state.harnessOpening)return "正在准备 AI 创作素材，请稍候";
    if(state.jobLoading)return "正在切换任务，请等待读取完成";
    return "";
  }
  function harnessModelBlockedReason(){
    if(state.harnessMode==="rework"){
      if($("reworkModelSel").disabled)return "改图模型正在读取，请稍候";
      if(!$("reworkProvider").value||!$("reworkModelSel").value)return "请选择改图用的接口和模型";
    }else if($("model").disabled)return "模型列表正在读取，请稍候";
    return "";
  }
  function syncActionControls(){
    const locked=Boolean(submissionBlockedReason()),modelBlocked=Boolean(harnessModelBlockedReason()),submitted=!!state.harnessSubmittedJob,stale=harnessContextChanged();
    if(stale&&!submitted)window.AIWorkflow?.phase('stale');
    $("harnessSend").disabled=locked||modelBlocked||submitted||stale;
    $("harnessDefault").disabled=locked||submitted;
    $("harnessApply").disabled=locked||modelBlocked||submitted||stale||!(state.harnessMode==="rework"?state.composedPrompt:state.requirements);
    ["retryBtn","retryLimitedBtn"].forEach(id=>$(id).disabled=locked);
    state.cards.forEach(entry=>{
      entry.rework.disabled=locked||entry.status!=="done";
      if(entry.retry)entry.retry.disabled=locked;
    });
    $("cancelBtn").disabled=state.jobLoading;
    $("feishuSyncBtn").disabled=state.jobLoading||Boolean(state.syncingJob);
    $("downloadBtn").setAttribute("aria-disabled",String(state.jobLoading));
    syncSelection(true);
  }

  function startBlockedReason() {
    const locked=submissionBlockedReason();if(locked)return locked;
    if(state.job&&!TERMINAL.includes(state.job.status))return "本轮任务仍在运行，请等待完成或取消";
    if(state.files.some(entry=>entry.loading))return "原图正在读取，请稍候";
    if($("model").disabled)return "模型列表正在读取，请稍候";
    if (!state.files.length) return "请先上传至少一张原图";
    if (!totalColors()) return "请至少选择一个颜色";
    if (!currentModel()) return "请选择模型";
    if (!$("provider").value) return "请选择生图接口";
    return "";
  }

  function bind() {
    const drop = $("drop");
    const input = $("fileInput");
    document.addEventListener("paste",pasteImages);
    const pasteHint=document.createElement("p");pasteHint.id="clipboardHint";pasteHint.className="hint";pasteHint.textContent="支持 Ctrl+V / ⌘V 粘贴图片；同件多角度请保留 item / view 文件名。";
    drop.after(pasteHint);drop.setAttribute("aria-describedby",[drop.getAttribute("aria-describedby"),pasteHint.id].filter(Boolean).join(" "));
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
      store(`modelManual.${$("provider").value}`,$("modelManual").value);
      renderModelLabels();
      updateSummary();
    });

    $("startBtn").addEventListener("click",beginGeneration);
    $("previewBtn").addEventListener("click", showPrompt);
    $("cancelBtn").addEventListener("click", cancelJob);
    $("retryBtn").addEventListener("click", retryJob);
    if ($("retryLimitedBtn")) $("retryLimitedBtn").addEventListener("click", retryLimited);
    $("feishuSyncBtn").addEventListener("click", async () => {
      const jobId=state.jobId;
      if(state.jobLoading||!jobId||state.job?.id!==jobId)return;
      state.syncingJob = jobId;
      if (state.job) renderJob(state.job);
      $("feishuSyncBtn").classList.add("loading");
      try {
        const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/feishu`, { method: "POST" });
        toast(
          data.rows ? "ok" : "bad",
          data.rows ? `已补写 ${data.rows} 行到飞书` : "写入 0 行（检查飞书配置或权限）"
        );
        if(state.job?.id===jobId&&!state.jobLoading){
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
      if(state.jobLoading)return;
      const ready = (state.job?.tasks || []).filter((t) => t.out);
      state.selected = event.target.checked ? new Set(ready.map((t) => t.id)) : new Set();
      syncSelection();
    });
    $("batchReworkBtn").addEventListener("click", () => openHarness("rework", [...state.selected]));
    $("batchExportBtn").addEventListener("click", exportSelection);
    $("usageChip").addEventListener("click",()=>window.WorkbenchUI.showUsage(()=>api("/api/usage")));


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

    // 成品卡用事件委托：卡片是增量更新的，不再每次轮询重复绑定监听
    const grid = $("gridImgs");
    grid.addEventListener("change", (event) => {
      if(state.jobLoading)return;
      const input = event.target.closest("input[data-task]");
      if (!input) return;
      if (input.checked) state.selected.add(input.dataset.task);
      else state.selected.delete(input.dataset.task);
      syncSelection();
    });
    grid.addEventListener("click", (event) => {
      if(state.jobLoading){event.preventDefault();return;}
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
        ++state.pollEpoch;state.pollController?.abort();
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
    window.WorkbenchUI.openLightbox(url,title);
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
    const usage = $("railUsage");
    if (usage) usage.addEventListener("click", () => showAccounts());
    const avatarBtn = $("railAvatar");
    if (avatarBtn) {
      avatarBtn.addEventListener("click",()=>window.WorkbenchUI.toggleProfile(avatarBtn));
    }
    // 顶栏的「账号用量 / 管理后台」按权限显示，图标栏跟着同步
    const syncRail = () => {
      const adminHidden = $("adminLink") ? $("adminLink").hidden : true;
      const usageHidden = $("accountsBtn") ? $("accountsBtn").hidden : true;
      if ($("railAdmin")) $("railAdmin").hidden = adminHidden;
      if($("railUsage"))$("railUsage").hidden=true;
      const name=$("whoName")?$("whoName").textContent.trim():"";
      if(avatarBtn&&name)avatarBtn.title=`${name} · 个人菜单`;
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
        $("briefInput").value="";state.pendingBrief="";
        await withLoading(direct,()=>createJob(""),"提交中…");
        updateSummary();
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
    if (false && window.matchMedia("(pointer: fine)").matches
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

  boot().catch(err=>{document.body.classList.remove("booting");toast("bad",`初始化失败：${err.message}`);});
})();
