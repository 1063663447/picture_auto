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
  // 基准图改图时的常用话术（确认在画布上点，这里只管「改哪里」）
  const QUICK_MASTER = [
    "领口再小一点，别太大",
    "颜色偏亮了，往效果图的颜色靠",
    "印花和文字必须原样，不要重画",
    "这张的裁切再往下一点",
    "以后这个款都按这个版型来",
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
    // 9.6.6 改图任务只返一次图：同一个改图任务被重复上报时不再往对话里塞第二张卡片
    reworkJobId: "",
    harnessReworkSubmitted: false,
    // 9.6.7 每张基准图各自的改图进度：taskId -> {stage,jobId,shots,progress,note,error}
    // 多张可以同时处于「改图中」，互不排队（生图接口支持并发）
    reworks: {},
    // 9.6.2 对话优先：正在盯的基准图任务（空=没在盯）
    masterJobId: "",
    annotationId: "",
    annotationBoxes: [],
    masterShown: false,
    masterDoneShown: false,
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
      // 9.6.5：基准图的确认回到画布上的审核条（可单张确认）；
      // 只有需要改图时才打开对话框（在审核条上点「改这张」）。
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
    $("reviewCancelBtn")?.addEventListener("click", (event) => cancelReviewBatch(event.currentTarget));
    // 9.6.7 卡片用事件委托：轮询重建 DOM 时不会把点击监听一起丢掉
    //（以前那一版的按钮在「重建瞬间」点下去是没反应的）
    $("reviewThumbs")?.addEventListener("click", (event) => {
      const hit = event.target.closest(
        "[data-approve],[data-adopt],[data-revert],[data-annotate],[data-annotate-round],[data-rework],[data-pause-review],[data-cancel-style],img[data-preview]"
      );
      if (!hit) return;
      const revert = hit.closest("[data-revert]");
      if (revert) { revertRound(revert); return; }
      const approve = hit.closest("[data-approve]");
      if (approve) { approveMaster(approve, null, [approve.dataset.approve]); return; }
      const adopt = hit.closest("[data-adopt]");
      if (adopt) { adoptRound(adopt); return; }
      const annotateRound = hit.closest("[data-annotate-round]");
      if (annotateRound) {
        const button = annotateRound;
        openAnnotator({
          jobId: button.dataset.roundJob || state.jobId,
          taskId: button.dataset.roundTask || button.dataset.annotateRound,
          url: button.dataset.roundUrl || "",
          masterTaskId: button.dataset.annotateRound,
          round: Number(button.dataset.roundNo) || 1,
          label: button.dataset.roundLabel || "",
        });
        return;
      }
      const annotate = hit.closest("[data-annotate]");
      if (annotate) { openAnnotator(annotate.dataset.annotate); return; }
      const rework = hit.closest("[data-rework]");
      if (rework) { talkMaster(rework.dataset.rework); return; }
      // 9.6.31 基准图也能暂停/继续
      // 9.6.33 基准图的出口：取消这款（本款不再生成 + 卡片撤下）
      const cancelStyleHit = hit.closest("[data-cancel-style]");
      if (cancelStyleHit) {
        cancelStyleTasks([cancelStyleHit.dataset.cancelStyle], cancelStyleHit);
        return;
      }
      const pauseHit = hit.closest("[data-pause-review]");
      if (pauseHit) {
        pauseTasks([pauseHit.dataset.pauseReview], pauseHit.dataset.pauseMode === "resume", pauseHit);
        return;
      }
      const img = hit.closest("img[data-preview]");
      if (img) {
        const card = img.closest(".review-card");
        const state0 = card?.dataset.state || "";
        if (state0 === "working") { toast("ok", "这张正在按框改图，等新图出来再动手"); return; }
        const taskId = card?.dataset.task || img.dataset.task;
        const shot = (reworkOf(taskId)?.shots || [])[0];
        if (state0 === "revised" && shot) {
          openAnnotator({
            jobId: shot.jobId || reworkOf(taskId)?.jobId || state.jobId,
            taskId: shot.id, url: shot.preview || shot.thumb || shot.thumb_small || "",
            masterTaskId: taskId, round: Number(reworkOf(taskId)?.round) || 2,
          });
          return;
        }
        openAnnotator(img.dataset.task);
      }
    });
  }
  function styleLabel(task) {
    const name = task.garment_name || (state.skill?.types?.[task.garment]?.display_name) || "";
    const colour = task.color_name || task.color || "";
    return [name, colour].filter(Boolean).join(" · ");
  }

  /* --------------------------------------- 9.6.7 卡片状态：待确认/改图中/第 2 轮 -- */
  // 运营在一张卡片上要能直接看到：这张确认了没有、AI 是不是正在按框改它、
  // 第 2 轮出来了没有。多张基准图可以**同时**改（每张一个任务，互不排队）。
  function reworkOf(taskId) {
    const entry = taskId ? state.reworks[taskId] : null;
    if (!entry) return null;
    if (entry.masterJob && state.job?.id && entry.masterJob !== state.job.id) return null;
    return entry;
  }

  function setRework(taskId, patch, opts) {
    if (!taskId) return;
    const prev = state.reworks[taskId] || {
      masterJob: state.job?.id || "", stage: "", jobId: "", shots: [], progress: "",
      note: "", error: "", at: 0,
    };
    state.reworks[taskId] = { ...prev, ...patch, at: Date.now() };
    if (!(opts && opts.quiet) && state.job) renderReview(state.job);
  }

  function reviewCardState(task) {
    const rw = reworkOf(task.id);
    const stage = rw?.stage || "";
    return {
      rw,
      stage,
      working: stage === "thinking" || stage === "running",
      ready: stage === "ready" && (rw?.shots || []).length > 0,
      failed: stage === "failed",
      approved: Boolean(task.approved),
    };
  }

  function reviewCardHtml(task) {
    const st = reviewCardState(task);
    const shot = (st.rw?.shots || [])[0] || null;
    const round = Number(st.rw?.round) || 1;
    const shownRound = round;
    const nextRound = round + 1;
    const thumb = shot ? (shot.thumb_small || shot.preview || "") : (task.thumb_small || "");
    const full = shot ? (shot.preview || shot.thumb || "") : (task.preview || task.thumb || "");
    const kind = st.approved ? "approved" : st.working ? "working" : st.ready ? "revised"
      : st.failed ? "failed" : "pending";
    // 「AI 分析中」= 正在读框选/写提示词；「图片生成中」= 任务已提交，在等生图
    const analysing = st.stage === "thinking";
    const phase = analysing ? "AI 分析中" : st.stage === "running" ? "图片生成中" : "";
    const badge = task.paused && !st.approved
      ? "已暂停"
      : st.approved
        ? (task.adopted_from ? `已确认 · 第 ${shownRound} 轮` : "已确认")
        : st.working ? `第 ${nextRound} 轮 · ${phase}`
          : st.ready ? `第 ${shownRound} 轮 · 待确认`
            : st.failed ? "改图失败" : `第 ${shownRound} 轮 · 待确认`;
    const tone = st.approved ? "ok" : (task.paused || st.working || st.failed || st.ready) ? "warn" : "";
    const hasRound = round > 1 || (st.rw?.shots || []).length > 0 || st.stage === "running" || analysing;
    const veil = st.working
      ? `<div class="working-veil"><span class="spin"></span>
           <b>${analysing ? "AI 分析中…" : "图片生成中…"}</b>
           <small>${esc(analysing ? "正在读框选、改提示词" : (st.rw.progress || "按框位生成中"))}</small>
         </div>`
      : "";
    const revertBtn = hasRound && !st.approved
      ? `<button type="button" class="warn" data-revert="${esc(task.id)}">复原</button>`
      : "";
    // 9.6.31 基准图的出口是「取消这款」（9.6.33 由暂停改过来）：本款不再生成，卡片也不显示
    const paused = Boolean(task.paused);
    const cancelStyleBtn = st.approved ? ""
      : `<button type="button" data-cancel-style="${esc(task.id)}" `
        + `title="本款不再生成：这张基准图和这款没开始的颜色都会取消并从列表里撤下">取消这款</button>`;
    const pauseBtn = st.approved ? ""
      : `<button type="button" data-pause-review="${esc(task.id)}" `
        + `data-pause-mode="${paused ? "resume" : "pause"}" `
        + `title="${paused ? "放回审核条继续确认" : "暂停这张基准图：不再占着审核条，成品保留"}">`
        + `${paused ? "继续" : "暂停"}</button>`;
    let acts;
    if (st.approved) {
      acts = "";
    } else if (st.working) {
      acts = `<div class="review-card-acts">${revertBtn}</div>`;
    } else if (st.ready) {
      // 直接在被改出来的这张图上接着框 → 第 3 轮、第 4 轮…（不用先采纳入库）
      const roundRef = `data-round-job="${esc(shot?.jobId || "")}" data-round-task="${esc(shot?.id || "")}" `
        + `data-round-url="${esc(shot?.preview || shot?.thumb || shot?.thumb_small || "")}" `
        + `data-round-no="${esc(String(shownRound))}"`;
      acts = `<div class="review-card-acts">
        <button type="button" data-adopt="${esc(task.id)}">确定</button>
        <button type="button" data-annotate-round="${esc(task.id)}" ${roundRef}>接着改（第 ${nextRound} 轮）</button>
        ${cancelStyleBtn}
        ${revertBtn}
      </div>`;
    } else if (st.failed) {
      const backRef = st.rw?.shots?.[0]?.jobId
        ? `data-round-job="${esc(st.rw.shots[0].jobId)}" data-round-task="${esc(st.rw.shots[0].id)}" `
          + `data-round-url="${esc(st.rw.shots[0].preview || st.rw.shots[0].thumb || "")}" `
          + `data-round-no="${esc(String(round))}"`
        : "";
      acts = `<div class="review-card-acts">
        <button type="button" data-annotate-round="${esc(task.id)}" ${backRef}>再框一次</button>
        ${cancelStyleBtn}
        ${revertBtn}
      </div>`;
    } else {
      acts = `<div class="review-card-acts">
        <button type="button" data-approve="${esc(task.id)}">确定</button>
        <button type="button" data-annotate="${esc(task.id)}">框选改</button>
        <button type="button" data-rework="${esc(task.id)}">直接说</button>
        ${cancelStyleBtn}
      </div>`;
    }
    const why = st.failed && st.rw.error
      ? `<span class="rw-error" title="${esc(st.rw.error)}">${esc(st.rw.error)}</span>`
      : "";
    return `<figure class="review-card ${kind}" data-state="${kind}" data-task="${esc(task.id)}">
      <div class="thumb-wrap${st.working ? " is-working" : ""}">
        <img src="${esc(thumb)}" alt="${esc(styleLabel(task))} 基准图"
             data-preview="${esc(full)}" data-task="${esc(task.id)}" />
        <span class="badge" data-tone="${esc(tone)}">${esc(badge)}</span>
        ${veil}
      </div>
      <div class="card-body">
        <figcaption>
          <b>${esc(task.garment_name || task.garment || "款式")} · ${esc(task.color_name || task.color)}</b>
          ${why}
        </figcaption>
        ${acts}
      </div>
    </figure>`;
  }

  // 复原到第 1 轮：丢掉这张基准图的所有改图轮次（连同一轮的审核记忆一起丢），
  // 免得「越改越糟还回不去」，也免得把糟的那一轮写进款式规则。
  const revertArmed = new Map();
  async function revertRound(button) {
    const card = button.closest(".review-card");
    const taskId = card?.dataset.task || button.dataset.revert;
    const rw = reworkOf(taskId);
    const label = (card?.querySelector("figcaption b")?.textContent || "这张").trim();
    if (!revertArmed.has(taskId)) {
      revertArmed.set(taskId, setTimeout(() => {
        revertArmed.delete(taskId);
        if (state.job) renderReview(state.job);
      }, 5000));
      button.textContent = "确认复原？";
      return;
    }
    clearTimeout(revertArmed.get(taskId));
    revertArmed.delete(taskId);
    await withLoading(button, async () => {
      try {
        const res = await api(`/api/jobs/${state.jobId}/revert`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ task_id: taskId }),
        });
        // 本地也清：卡片回到第 1 轮 + 这轮对话历史不再带给 AI
        delete state.reworks[taskId];
        if (rw?.jobId) stopWatch(rw.jobId);
        state.harnessHistory = (state.harnessHistory || []).filter(
          (item) => item.roundTag !== taskId
        );
        if (res?.job) renderJob(res.job);
        toast("ok", `「${label}」已复原到第 1 轮，这轮的审核记忆也一起丢掉了`);
      } catch (err) {
        toast("bad", err.message);
      } finally {
        if (state.job) renderReview(state.job);
      }
    }, "复原中…");
  }

  async function adoptRound(button) {
    const taskId = button.dataset.adopt;
    const rw = reworkOf(taskId);
    if (!rw || !rw.jobId || !rw.shots?.length) { toast("bad", "还没拿到改好的图"); return; }
    const shot = rw.shots[0];
    const adopt = { [taskId]: `${shot.jobId || rw.jobId}::${shot.id}` };
    await approveMaster(button, adopt, [taskId]);
    setRework(taskId, { stage: "adopted" });
  }

  function renderReview(job) {
    bindReviewActions();
    const bar = $("reviewBar");
    if (!bar) return;
    // 刷新/重进任务时，用服务端记下的改图轮次把卡片状态补回来（9.6.24）
    hydrateReworks(job);
    const info = reviewInfo(job);
    const masters = (job?.tasks || []).filter((t) => t.master && t.thumb_small && !t.hidden);
    // 取消/失败/完成的单子不该再顶着审核条（否则会有人去点「确定」）
    const show = Boolean(
      info && !TERMINAL.includes(job.status)
      && (info.awaiting || info.pending_ids?.length || masters.length)
    );
    bar.hidden = !show;
    if (!show) return;
    const pending = masters.filter((t) => !t.approved);
    const working = pending.filter((t) => reviewCardState(t).working).length;
    // 客户要求：审核条上不要再堆说明文字，只留一个极简计数（含「改图中」提醒）
    const head = $("reviewHead");
    if (head) {
      head.textContent = pending.length
        ? `${pending.length} 张待确认${working ? ` · ${working} 张改图中` : ""}`
        : "已全部确认";
    }
    const box = $("reviewThumbs");
    // 轮询每 1.2s 会走一遍 renderReview：卡片没变化就不重建 DOM，
    // 否则用户正要点「框选改」时节点被换掉（点了没反应 / 框选对话被打断）。
    const sig = masters.map((task) => {
      const st = reviewCardState(task);
      const shot = st.rw?.shots?.[0] || null;
      return `${task.id}:${task.approved ? 1 : 0}:${task.thumb_small || ""}:${st.stage}:${shot?.id || ""}`;
    }).join("|");
    if (box && box.dataset.sig !== sig) {
      box.dataset.sig = sig;
      box.innerHTML = masters.map((task) => reviewCardHtml(task)).join("");
    }
    const approve = $("approveMasterBtn");
    if (approve) {
      const canAll = pending.length > 0;
      approve.hidden = !canAll;
      approve.disabled = !canAll;
      approve.textContent = pending.length === masters.length ? "全部确定" : `确定剩下 ${pending.length} 张`;
    }
  }

  async function approveMaster(button, adopt, onlyTaskIds) {
    const jobId = state.jobId;
    if (!jobId || state.job?.id !== jobId) return;
    const info = reviewInfo(state.job);
    if (!info || !info.can_approve) { toast("bad", "现在还没有可确认的基准图"); return; }
    const ids = (onlyTaskIds && onlyTaskIds.length)
      ? [...onlyTaskIds]
      : ([...(info.pending_ids || [])].length ? [...info.pending_ids] : [...(info.master_ids || [])]);
    await withLoading(button, async () => {
      try {
        const res = await api(`/api/jobs/${jobId}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            task_ids: ids,
            note: state.reviewNote || "",
            learn: true,
            adopt: adopt || {},
          }),
        });
        toast("ok", "基准图已确认，开始按它改色（后台在评估这次的要求要不要沉淀）");
        chatBubble("assistant", "收到，就按这张来。其余颜色开始改色，我在后台看一下这次的要求值不值得记进款式规则。");
        watchDistill(jobId, res?.job?.id || jobId);
        if (res?.job) renderJob(res.job);
        watchJob(jobId);
        state.masterShown = true;
        state.masterDoneShown = false;
      } catch (err) {
        toast("bad", err.message);
      }
    }, "确认中…");
  }

  /* ------------------------------------------------ 9.6.2 对话优先的确认流程 -- */
  // 客户要求：放原图 + 选款式 → 点生成 → 直接拉出对话框；基准图在对话里返给用户看，
  // 有歧义就在对话里说哪张要改，改完再返图，直到确认才开始改色。

  function masterStatusLine(text) {
    // 同一条状态只更新文字，不刷屏
    let node = $("masterStatus");
    if (!node) {
      node = document.createElement("div");
      node.id = "masterStatus";
      node.className = "msg assistant";
      node.innerHTML = `<span class="who">AI 助手</span><div class="bubble"></div>`;
      $("chatLog").appendChild(node);
    }
    node.querySelector(".bubble").textContent = text;
    scrollChat();
    return node;
  }
  function masterStatusDone() {
    const node = $("masterStatus");
    if (node) node.remove();
  }

  let masterTimer = 0;
  function stopMasterWatch() {
    if (masterTimer) { clearTimeout(masterTimer); masterTimer = 0; }
    masterStatusDone();
    state.masterJobId = "";
    state.masterShown = false;
  }



  /* ------------------------------------------- 9.6.2 改图返图 + 采纳当基准 --- */
  // 用户在对话里说「领口小一点」→ AI 调 reject_master 生成改图任务 →
  // 这里盯着那条改图任务，出图后在对话里返图，并给「用这张当基准」按钮（= 采纳 + 开始改色）。
  let reworkTimer = 0;
  // 9.6.7 每张基准图一条改图任务，可以同时跑好几条：定时器按 jobId 分开存
  const reworkTimers = new Map();
  function stopWatch(jobId) {
    const timer = reworkTimers.get(String(jobId || ""));
    if (timer) clearTimeout(timer);
    reworkTimers.delete(String(jobId || ""));
  }

  function stopReworkWatch() {
    if (reworkTimer) { clearTimeout(reworkTimer); reworkTimer = 0; }
    for (const timer of reworkTimers.values()) clearTimeout(timer);
    reworkTimers.clear();
  }

  // 9.6.24 服务端记着每张基准图改到第几轮（job.review.rounds）。刷新页面后内存里的
  // state.reworks 是空的，卡片就退回「第 1 轮 · 待确认」——改图明明成了，用户看不到
  // 也没法点确定，整批就一直挂着。这里按服务端记录把卡片状态补回来。
  let hydrateAt = 0;
  function hydrateReworks(job) {
    const rounds = (job && job.review && job.review.rounds) || [];
    if (!rounds.length) return;
    const now = Date.now();
    if (now - hydrateAt < 1200) return;   // 主任务轮询 1.2s 一轮，别每帧都算
    hydrateAt = now;
    for (const round of rounds) {
      const taskId = round.task_id;
      if (!taskId || !round.job_id) continue;
      const cur = reworkOf(taskId);
      // 本地已经在跟同一条改图任务（提交中/生成中/已出图/已采纳）：以本地为准
      if (cur && (cur.stage === "thinking" || cur.jobId === round.job_id)) continue;
      // 本地盯的是另一条任务且还没跑完：别拿旧轮次盖掉它
      if (cur && cur.stage === "running") continue;
      // 这张已经确认过了：卡片自己会显示「已确认」，不用再补轮次
      const masterTask = (job.tasks || []).find((t) => t.id === taskId);
      if (masterTask && masterTask.approved) continue;
      if (round.status === "done" && round.task) {
        setRework(taskId, {
          stage: "ready", jobId: round.job_id, progress: "", note: "", error: "",
          round: Number(round.round) || 2,
          shots: [{
            id: round.task, jobId: round.job_id,
            thumb: round.preview || round.thumb || "",
            preview: round.preview || round.thumb || "",
            thumb_small: round.thumb || "",
          }],
        }, { quiet: true });
      } else if (round.status === "failed") {
        setRework(taskId, {
          stage: "failed", jobId: round.job_id, error: round.error || "改图没出图",
        }, { quiet: true });
      } else if (round.status === "running") {
        setRework(taskId, { stage: "running", jobId: round.job_id, progress: "生成中" }, { quiet: true });
        if (!reworkTimers.has(String(round.job_id))) watchReworkFor(round.job_id, [taskId]);
      }
    }
  }

  // 重新拉一次主任务（改图任务查不到时，用它把服务端记下的真实轮次捞回来）
  async function refreshParentJob() {
    const jobId = state.jobId;
    if (!jobId) return;
    try {
      const data = await api(`/api/jobs/${jobId}`, { cache: "no-store" });
      if (data && data.job && data.job.id === jobId) {
        hydrateAt = 0;            // 强制让 hydrateReworks 这次一定跑
        renderJob(data.job);
      }
    } catch (_) { /* 拉不到就交给调用方的失败分支 */ }
  }

  // 盯一条改图任务：出图就分发给对应的基准图卡片，失败/中断就标出来
  // 9.6.24 加了放弃条件：查不到任务（服务重启/被清）或太久不出图时，先回主任务捞
  // 服务端记下的轮次，捞不到才标失败。以前这里 catch 完就无限重试，界面永远「生成中」。
  function watchReworkFor(jobId, taskIds, onSettled) {
    const ids = (taskIds || []).filter(Boolean);
    if (!jobId || !ids.length) return;
    let startedAt = Date.now();
    let misses = 0;
    const settle = (ok) => { if (typeof onSettled === "function") onSettled(ok); };
    // 放弃之前先回主任务捞一次服务端记下的轮次；真捞不到才标失败（要让用户看见）
    const giveUp = async (why) => {
      stopWatch(jobId);
      await refreshParentJob();
      const recovered = ids.some((taskId) => {
        const entry = reworkOf(taskId);
        return entry && (entry.stage === "ready" || entry.stage === "failed");
      });
      if (!recovered) {
        ids.forEach((taskId) => setRework(taskId, { stage: "failed", jobId, error: why }));
        toast("bad", why);
      }
      settle(recovered);
    };
    const tick = async () => {
      reworkTimers.delete(jobId);
      try {
        const data = await api(`/api/jobs/${jobId}`, { cache: "no-store" });
        misses = 0;
        const job = data.job || {};
        const shots = (job.tasks || []).filter((t) => t.out && (t.thumb_small || t.preview));
        if (shots.length) {
          ids.forEach((taskId, index) => {
            const shot = shots[Math.min(index, shots.length - 1)];
            const prevRound = Number(reworkOf(taskId)?.round) || 1;
            setRework(taskId, {
              stage: "ready", jobId, progress: "",
              // 这一轮出的图 = 第 prevRound+1 轮；再框就基于它继续改
              round: prevRound + 1,
              shots: [{ ...shot, jobId }],
            });
          });
          if ($("harnessDrawer") && !$("harnessDrawer").hidden) chatReworkCard(shots, jobId, ids);
          settle(true);
          toast("ok", "第 2 轮基准图出来了，看一眼这份改得对不对");
          return;
        }
        if (TERMINAL.includes(job.status)) {
          const why = job.error || job.stage || "改图没出图";
          ids.forEach((taskId) => setRework(taskId, { stage: "failed", jobId, error: why }));
          settle(false);
          return;
        }
        ids.forEach((taskId) => {
          if (reworkOf(taskId)?.stage === "running") {
            setRework(taskId, { progress: job.stage || "" }, { quiet: true });
          }
        });
      } catch (_) {
        // 网络抖一下可以忍；一直查不到（服务重启/任务被清）就不能无限转圈了
        misses += 1;
        if (misses >= 4) {
          await giveUp("改图任务查不到了（服务可能重启过），已回主任务重新读取进度");
          return;
        }
      }
      if (Date.now() - startedAt > 12 * 60 * 1000) {
        await refreshParentJob();
        const still = ids.map((taskId) => reworkOf(taskId)).find((entry) => entry && entry.stage === "running");
        if (still) {
          startedAt = Date.now();   // 服务端还在跑，再给 12 分钟
          misses = 0;
        } else {
          await giveUp("改图超过 12 分钟还没出图，可以再框一次或换个接口试试");
          return;
        }
      }
      reworkTimers.set(jobId, setTimeout(tick, 2200));
    };
    tick();
  }

  function startReworkWatch(jobId, masterIds) {
    // 「直接说」这条路：任务和卡片共用一套进度（watchReworkFor），
    // 对话开着的时候额外把图返到对话里，并保留一句状态行。
    const ids = (masterIds || []).filter(Boolean);
    ids.forEach((taskId) => setRework(taskId, { stage: "running", jobId, progress: "生成中" }));
    const alive = () => state.harnessMode === "master" && $("harnessDrawer") && !$("harnessDrawer").hidden;
    if (alive()) masterStatusLine("正在按你说的改…（生成中）");
    watchReworkFor(jobId, ids, () => { masterStatusDone(); });
  }

  function chatReworkCard(shots, reworkJobId, masterIds) {
    const wrap = document.createElement("div");
    wrap.className = "msg assistant";
    const figures = shots.map((task) => `
      <figure><img src="${esc(task.thumb_small)}" alt="${esc(task.color_name || task.color)} 改后"
        data-preview="${esc(task.preview || task.thumb || task.thumb_small)}" data-task="${esc(task.id)}" />
        <figcaption>${esc(task.color_name || task.color)} · 改后</figcaption></figure>`).join("");
    wrap.innerHTML = `<span class="who">AI 助手</span>
      <div class="bubble master-card">
        <p style="margin:0">按你说的改好了。可以就换成这张，我再拿它给其余颜色改色；还要动就继续说。</p>
        <div class="master-shots">${figures}</div>
        <div class="chat-actions">
          <button type="button" class="primary" data-master-act="adopt">用这张当基准，开始改色</button>
          <button type="button" data-master-act="edit">还要改…</button>
        </div>
      </div>`;
    $("chatLog").appendChild(wrap);
    wrap.querySelectorAll("img[data-preview]").forEach((img) => {
      img.addEventListener("click", () => openLightbox?.(img.dataset.preview, img.dataset.task));
    });
    wrap.querySelector("[data-master-act='adopt']")?.addEventListener("click", (event) => {
      const button = event.currentTarget;
      const adopt = {};
      const ids = (masterIds && masterIds.length) ? masterIds : [null];
      shots.forEach((task, index) => {
        const masterId = ids[index] || ids[0];
        if (masterId) adopt[masterId] = `${reworkJobId}::${task.id}`;
      });
      button.disabled = true;
      // 只确认对话卡片对应的那几张（以前不传 ids，会把其它待确认的基准图一起确认掉）
      const onlyIds = (masterIds || []).filter(Boolean);
      approveMaster(button, adopt, onlyIds).finally(() => { button.disabled = false; });
    });
    wrap.querySelector("[data-master-act='edit']")?.addEventListener("click", () => {
      const input = $("harnessInput");
      if (!input) return;
      if (!input.value) input.value = "这张还要改：";
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
    scrollChat();
  }

  /* --------------------------------------------- 9.6.3 静默评估结果回对话 --- */
  // 拍板后 harness 在后台评判「这条要求是款式通病还是一次性微调」，
  // 评完在对话里补一行说明（不追问、不打断），硬规则升级会单独说一句。
  let distillTimer = 0;
  function stopDistillWatch() {
    if (distillTimer) { clearTimeout(distillTimer); distillTimer = 0; }
  }
  function watchDistill(jobId, targetJobId) {
    stopDistillWatch();
    const alive = () => state.masterJobId === targetJobId || state.job?.id === targetJobId;
    let tries = 0;
    const tick = async () => {
      tries += 1;
      if (!alive() || tries > 40) { stopDistillWatch(); return; }
      try {
        const data = await api(`/api/jobs/${targetJobId}/distill`, { cache: "no-store" });
        const info = data.distill || {};
        if (info.state === "done") {
          stopDistillWatch();
          const promoted = Number(info.promoted || 0);
          const remembered = Number(info.remembered || 0);
          const dropped = Number(info.dropped || 0);
          if (!info.total) return;
          const parts = [];
          if (promoted) parts.push(`${promoted} 条升级成这个款式的硬规则（下次生图自动带上）`);
          if (remembered - promoted > 0) parts.push(`${remembered - promoted} 条记进款式经验`);
          if (dropped) parts.push(`${dropped} 条判为这一次的微调，没写进规则`);
          chatBubble("assistant", `这次的要求我看过了：${parts.join("，")}。`);
          return;
        }
        if (info.state === "off" || info.state === "skipped") { stopDistillWatch(); return; }
      } catch (_) { /* 评估状态读不到就算了，别打扰用户 */ }
      if (alive()) distillTimer = setTimeout(tick, 2500);
    };
    distillTimer = setTimeout(tick, 4000);
  }

  /* ------------------------------------------------ 9.6.6 框选标注（改图对齐） -- */
  // 客户要求：点要改的基准图 → 大图 → 画框标出有歧义的部分、每个框写要改的效果/问题
  // → 交给 harness 分析并改提示词（标注图也随改图发给生图接口，提高命中率）。
  const anno = { taskId: "", jobId: "", url: "", boxes: [], image: null, dragging: null, mode: "master" };
  let annoReady = false;

  function annoCount() { return anno.boxes.length; }

  function renderAnnoLayer() {
    const layer = $("annotateLayer");
    if (!layer) return;
    layer.innerHTML = anno.boxes.map((box) => {
      const x = box.x * 100, y = box.y * 100, w = box.w * 100, h = box.h * 100;
      const nx = Math.min(97, x + Math.max(2.4, Math.min(4, w / 4)));
      const ny = Math.min(97, y + Math.max(2.4, Math.min(4, h / 4)));
      return `<rect class="box" x="${x}" y="${y}" width="${w}" height="${h}" rx="0.6" />`
        + `<text class="box-num" x="${nx}" y="${ny}">${box.n}</text>`;
    }).join("");
    const list = $("annotateList");
    if (!list) return;
    if (!anno.boxes.length) {
      list.innerHTML = `<p class="tip">还没有框：在图上拖动画框，然后在下面写「这里要改成什么」。</p>`;
      return;
    }
    list.innerHTML = anno.boxes.map((box, index) => `
      <div class="row">
        <span class="n">${box.n}</span>
        <input type="text" data-note="${index}" value="${esc(box.note || "")}"
               placeholder="框 ${box.n}：这里要改成什么 / 有什么问题" />
        <button type="button" class="del" data-del="${index}" aria-label="删除框 ${box.n}">删除</button>
      </div>`).join("");
    list.querySelectorAll("[data-note]").forEach((input) => {
      input.addEventListener("input", () => {
        const box = anno.boxes[Number(input.dataset.note)];
        if (box) box.note = input.value;
      });
    });
    list.querySelectorAll("[data-del]").forEach((button) => {
      button.addEventListener("click", () => {
        anno.boxes.splice(Number(button.dataset.del), 1);
        anno.boxes.forEach((box, i) => { box.n = i + 1; });
        renderAnnoLayer();
      });
    });
  }

  // ref 可以是主任务的 taskId（第 1 轮），也可以是 {jobId, taskId, url, label, round, masterTaskId}
  // —— 第 2 轮之后被框的那张图在「上一轮的改图任务」里，不在主任务里。
  function openAnnotator(ref) {
    let task = null, jobId = state.jobId, url = "", labelText = "";
    let round = 1, masterTaskId = "";
    if (ref && typeof ref === "object") {
      jobId = ref.jobId || state.jobId;
      task = ref.task || { id: ref.taskId, garment_name: ref.garmentName, color_name: ref.colorName };
      url = ref.url || "";
      labelText = ref.label || "";
      round = Number(ref.round) || 1;
      masterTaskId = ref.masterTaskId || "";
      anno.mode = ref.mode === "rework" ? "rework" : "master";
    } else {
      task = (state.job?.tasks || []).find((t) => t.id === ref);
      if (!task) { toast("bad", "找不到这张图"); return; }
      url = task.preview || task.thumb || task.thumb_small || "";
      masterTaskId = task.id;
      anno.mode = "master";
    }
    if (!url) { toast("bad", "这张图还没有可框选的大图"); return; }
    anno.taskId = task?.id || "";
    anno.jobId = jobId;
    anno.masterTaskId = masterTaskId || anno.taskId;
    anno.round = round;
    anno.url = url;
    anno.boxes = [];
    anno.image = null;
    const label = $("annotateLabel");
    if (label) {
      const which = labelText
        || `${task?.garment_name || task?.garment || "款式"} · ${task?.color_name || task?.color || ""}`;
      label.textContent = `${which}${round > 1 ? `（第 ${round} 轮）` : ""}　框外保持不动`;
    }
    const img = $("annotateImage");
    if (img) {
      img.onload = () => { anno.image = img; renderAnnoLayer(); };
      img.src = anno.url;
    }
    // 成品卡那条路不经过对话：按钮别再说「交给 AI」，免得以为还要再说一句
    const submit = $("annotateSubmit");
    if (submit) submit.textContent = anno.mode === "rework" ? "开始改图" : "交给 AI 改";
    const hint = $("annotateHint");
    if (hint && anno.mode === "rework") {
      hint.textContent = "按住鼠标在图上拖，框出要改的地方（可画多个）；框外保持不动";
    }
    renderAnnoLayer();
    const dialog = $("annotateDialog");
    if (dialog && typeof dialog.showModal === "function") dialog.showModal();
    else dialog?.setAttribute("open", "");
  }

  function closeAnnotator() {
    const dialog = $("annotateDialog");
    if (dialog && typeof dialog.close === "function") dialog.close();
    else dialog?.removeAttribute("open");
  }

  function bindAnnotator() {
    if (annoReady) return;
    annoReady = true;
    const stage = $("annotateStage");
    if (!stage) return;
    const point = (event) => {
      const rect = stage.getBoundingClientRect();
      const image = $("annotateImage");
      const box = image ? image.getBoundingClientRect() : rect;
      const x = (event.clientX - box.left) / Math.max(1, box.width);
      const y = (event.clientY - box.top) / Math.max(1, box.height);
      return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) };
    };
    stage.addEventListener("pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      // 部分浏览器/笔式指针会在 capture 时抛 NotFoundError，抛出去整段拖拽就废了
      try { stage.setPointerCapture?.(event.pointerId); } catch (_) { /* 抓不住也照样画 */ }
      const start = point(event);
      anno.dragging = { startX: start.x, startY: start.y, box: null };
      stage.style.cursor = "crosshair";
    });
    stage.addEventListener("pointermove", (event) => {
      if (!anno.dragging) return;
      const now = point(event);
      const { startX, startY } = anno.dragging;
      const x = Math.min(startX, now.x), y = Math.min(startY, now.y);
      const w = Math.abs(now.x - startX), h = Math.abs(now.y - startY);
      anno.dragging.box = { x, y, w, h };
      renderAnnoLayer();
      const layer = $("annotateLayer");
      if (layer && (w > 0.01 || h > 0.01)) {
        layer.innerHTML += `<rect class="box" x="${x * 100}" y="${y * 100}" `
          + `width="${w * 100}" height="${h * 100}" rx="0.6" />`;
      }
    });
    stage.addEventListener("pointerup", (event) => {
      if (!anno.dragging) return;
      const dragging = anno.dragging;
      anno.dragging = null;
      const box = dragging.box;
      if (!box || box.w < 0.02 || box.h < 0.02) { renderAnnoLayer(); return; }
      anno.boxes.push({ n: anno.boxes.length + 1, x: box.x, y: box.y, w: box.w, h: box.h, note: "" });
      renderAnnoLayer();
      const inputs = $("annotateList")?.querySelectorAll("[data-note]");
      inputs?.[inputs.length - 1]?.focus();
    });
    $("annotateUndo")?.addEventListener("click", () => {
      anno.boxes.pop();
      anno.boxes.forEach((box, i) => { box.n = i + 1; });
      renderAnnoLayer();
    });
    $("annotateClear")?.addEventListener("click", () => { anno.boxes = []; renderAnnoLayer(); });
    $("annotateClose")?.addEventListener("click", closeAnnotator);
    $("annotateCancel")?.addEventListener("click", closeAnnotator);
    $("annotateSubmit")?.addEventListener("click", (event) => submitAnnotation(event.currentTarget));
  }

  // 把框画进图里，得到一张「标注图」（给生图接口和小模型看的位置地图）
  function composeAnnotatedImage() {
    const image = anno.image;
    if (!image) return Promise.resolve(null);
    const width = image.naturalWidth || image.width;
    const height = image.naturalHeight || image.height;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, 0, 0, width, height);
    const line = Math.max(3, Math.round(Math.min(width, height) / 160));
    ctx.lineWidth = line;
    ctx.font = `bold ${Math.max(14, Math.round(line * 5))}px sans-serif`;
    anno.boxes.forEach((box) => {
      const x = box.x * width, y = box.y * height;
      const w = box.w * width, h = box.h * height;
      ctx.strokeStyle = "#ff3b3b";
      ctx.fillStyle = "rgba(255,59,59,0.12)";
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
      const label = String(box.n);
      const size = Math.max(18, Math.round(line * 6));
      ctx.fillStyle = "#ff3b3b";
      ctx.fillRect(x, Math.max(0, y - size), size + Math.round(size * 0.25), size);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, x + Math.round(size * 0.25), Math.max(size * 0.9, y - size * 0.25));
    });
    return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), "image/png"));
  }

  function annotationText() {
    return anno.boxes.map((box) => {
      const note = (box.note || "").trim();
      return note ? `框${box.n}：${note}` : `框${box.n}：（还没写要求）`;
    }).join("；");
  }

  // 9.6.7 框选改图：点「交给 AI 改」就把标注图 + 框位 + 要求**直接发给 AI**，
  // 不再让用户再点一次发送。发完立刻回到审核条，这张卡片转圈等第 2 轮；
  // 用户可以在等待期间去框选、去改别的基准图（每张一条任务，互不排队）。
  function annotationInstruction(task, summary) {
    const which = task
      ? `${task.garment_name || task.garment || "这个款式"} · ${task.color_name || task.color || ""}`
      : "这张基准图";
    return `按我框选的位置改「${which}」：${summary}。框外不要动，标注框线不要画进成品。`;
  }

  /* ------------------------------------------- 9.6.25 成品卡的「改图」三项 -- */
  // 出好的图要改，三条路各有用处：
  //   直接重新生成 = 同样的提示词再跑一次（不满意但懒得说改哪儿）
  //   对话改图     = 和 AI 说一句（它会整理成提示词，走一条新的改图任务）
  //   框选改图     = 在图上框出要改的地方，框外不动（带标注图，最省话）
  // 菜单用 fixed 定位挂在 body 上：卡片本身 overflow:hidden，直接放在卡片里会被裁掉。
  function closeReworkMenu() {
    document.getElementById("reworkMenu")?.remove();
  }

  function openReworkMenu(anchor, taskId) {
    closeReworkMenu();
    const task = (state.job?.tasks || []).find((t) => t.id === taskId);
    if (!task) return;
    if (task.status !== "done") { toast("bad", "这张还没出图，等成品出来再改"); return; }
    const items = [
      ["regen", "直接重新生成", "同样的提示词再跑一次"],
      ["chat", "对话改图", "跟 AI 说改哪儿，它整理成提示词"],
      ["annotate", "框选改图", "在图上框出来，框外保持不动"],
    ];
    const menu = document.createElement("div");
    menu.id = "reworkMenu";
    menu.className = "rework-pop";
    menu.setAttribute("role", "menu");
    menu.dataset.task = taskId;
    menu.innerHTML = items
      .map(([key, label, hint]) => `<button type="button" role="menuitem" data-rework-act="${key}">
          <b>${label}</b><small>${hint}</small></button>`)
      .join("");
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    const width = menu.offsetWidth || 210;
    const left = Math.max(10, Math.min(rect.right - width, window.innerWidth - width - 10));
    let top = rect.top - menu.offsetHeight - 8;
    if (top < 10) top = rect.bottom + 8;      // 上面放不下就往下弹
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  // 框选改图（成品卡这条路）：不经过对话，直接把标注图 + 框位要求提交成一条改图任务
  async function submitCardRework({ taskId, jobId, annotationId, summary, task }) {
    const form = new FormData();
    form.append("task_ids", taskId);
    form.append("instruction", annotationInstruction(task, summary));
    if (annotationId) form.append("annotation_id", annotationId);
    form.append("start", "true");
    try {
      const res = await api(`/api/jobs/${jobId}/rework`, { method: "POST", body: form });
      if (!res?.ok || !res?.job?.id) throw new Error(res?.error || "服务端没有返回改图任务");
      toast("ok", "已按框改这张（新任务），出图后单独下载");
      watchJob(res.job.id);
      loadHistory();
    } catch (err) {
      toast("bad", `框选改图没提交成功：${err.message}`);
    }
  }

  async function submitAnnotation(button) {    if (!anno.boxes.length) { toast("bad", "先画一个框再提交"); return; }
    const empty = anno.boxes.filter((box) => !(box.note || "").trim());
    if (empty.length) {
      toast("bad", `框${empty.map((b) => b.n).join("、")} 还没写要改什么`);
      return;
    }
    const taskId = anno.taskId, jobId = anno.jobId;
    const masterTaskId = anno.masterTaskId || taskId;
    const round = Number(anno.round) || 1;
    await withLoading(button, async () => {
      let annotationId = "";
      try {
        const blob = await composeAnnotatedImage();
        if (blob) {
          const form = new FormData();
          form.append("task_id", taskId);
          form.append("boxes", JSON.stringify(anno.boxes));
          form.append("file", blob, `annotation-${taskId}.png`);
          const res = await api(`/api/jobs/${jobId}/annotations`, { method: "POST", body: form });
          annotationId = res?.annotation?.id || "";
        }
      } catch (err) {
        toast("bad", `标注图保存失败：${err.message}`);
        return;
      }
      const task = (state.job?.tasks || []).find((t) => t.id === masterTaskId)
        || { id: taskId, garment_name: $("annotateLabel")?.textContent || "" };
      const summary = annotationText();
      const boxes = anno.boxes.map((box) => ({ ...box }));
      closeAnnotator();
      // 成品卡那条路：不走对话、不进审核条，直接建改图任务并跳过去看进度
      if (anno.mode === "rework") {
        await submitCardRework({ taskId, jobId, annotationId, summary, task });
        return;
      }
      // 立刻回到审核条，并把这张标成「AI 正在读框选…」（保留当前显示的是第几轮）
      setRework(masterTaskId, {
        stage: "thinking", jobId: "", error: "", progress: "",
        note: "", round,
        // 保留正在被改的那张图（第 N 轮），圈要盖在它上面
        shots: reworkOf(masterTaskId)?.shots || [],
        instruction: annotationInstruction(task, summary),
      });
      $("reviewBar")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      toast("ok", `已交给 AI：正在按框改这张（第 ${round + 1} 轮），你可以先去框选别的基准图`);
      runAnnotationRound({
        taskId, jobId, annotationId, boxes, summary, task,
        masterTaskId, round, sourceJobId: jobId,
      });
    }, "提交中…");
  }

  // 安静的流式请求：不抢对话面板，只等服务端把任务建出来（一次请求 = 一轮改图）
  async function streamQuiet(payload, onEvent) {
    const res = await fetch("/api/harness/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok || !res.body) throw new Error(`AI 连接失败 HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let reply = "";
    let jobId = "";
    const take = (frame) => {
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) return;
      let body;
      try { body = JSON.parse(data); } catch (_) { return; }
      if (event === "error") throw new Error(String(body.error || body.text || "AI 返回了错误"));
      if (event === "delta" || event === "message") reply += String(body.text || "");
      if (event === "job" && body.job_id) jobId = String(body.job_id);
      if (event === "prompt" && body.job_id) jobId = String(body.job_id);
      if (typeof onEvent === "function") onEvent(event, body);
    };
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        let index;
        while ((index = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
          take(frame);
        }
      }
      buffer += decoder.decode();
      if (buffer.trim()) take(buffer);
    } finally {
      reader.releaseLock();
    }
    return { reply: reply.trim(), jobId };
  }

  async function runAnnotationRound(roundInfo) {
    const taskId = roundInfo.taskId;
    const masterTaskId = roundInfo.masterTaskId || taskId;
    const payload = {
      mode: "master",
      instruction: roundInfo.instruction || annotationInstruction(roundInfo.task, roundInfo.summary),
      // job_id 是「主任务」（审核/采纳的落点）；要改的那张图可能是上一轮的改图任务
      job_id: state.jobId,
      task_ids: [masterTaskId],
      source_job_id: roundInfo.sourceJobId || roundInfo.jobId,
      source_task_id: taskId,
      subject: $("subject")?.value || "auto",
      size: currentSize(),
      quality: $("quality")?.value || "",
      output_format: currentOutputFormat(),
      send_reference: true,
      shape_lock: "master",
      pattern_mode: currentPatternMode(),
      history: [],
    };
    if (roundInfo.annotationId) payload.annotation_id = roundInfo.annotationId;
    if (roundInfo.boxes?.length) payload.annotations = roundInfo.boxes;
    try {
      const { reply, jobId } = await streamQuiet(payload, (event, body) => {
        if (event === "tool" || event === "result") {
          const label = String(body.name || "");
          if (/reject_master|submit_rework|submit_generation/.test(label)) {
            setRework(masterTaskId, { progress: "正在提交改图任务…" }, { quiet: true });
          }
        }
      });
      const note = (reply || "").replace(/\s+/g, " ").slice(0, 220);
      if (!jobId) {
        setRework(masterTaskId, { stage: "failed", error: note || "AI 没有建改图任务" });
        toast("bad", "AI 没有提交改图任务，可以再框一次或直接说");
        return;
      }
      setRework(masterTaskId, { stage: "running", jobId, note, progress: "生成中" });
      watchReworkFor(jobId, [masterTaskId]);
      state.reworkJobId = jobId;
      if (note) {
        // 记进对话历史：之后在对话框里接着说，AI 知道上一轮改了哪儿
        state.harnessHistory.push({ role: "user", content: payload.instruction, roundTag: masterTaskId });
        state.harnessHistory.push({ role: "assistant", content: note, roundTag: masterTaskId });
        state.harnessHistory = state.harnessHistory.slice(-12);
      }
    } catch (err) {
      setRework(masterTaskId, { stage: "failed", error: err.message });
      toast("bad", `框选改图没成功：${err.message}`);
    }
  }

  function talkMaster(taskId) {
    if (!state.job?.id) { toast("bad", "还没有可讨论的任务"); return; }
    const info = reviewInfo(state.job);
    if (!info) { toast("bad", "这一批没有基准图审核"); return; }
    const pending = (state.job.tasks || []).filter(
      (t) => t.master && !t.approved && (t.thumb_small || t.thumb)
    );
    // 只针对一张：没指定就用第一张待确认的
    const target = taskId
      ? (state.job.tasks || []).find((t) => t.id === taskId)
      : pending[0];
    if (!target) { toast("bad", "没有可改的基准图"); return; }
    // 这张已经出过第 N 轮 → 对话继续改的就是那一轮的新图（不用先采纳）
    const shot = (reworkOf(target.id)?.shots || [])[0];
    state.annotationSource = shot && shot.jobId ? { jobId: shot.jobId, taskId: shot.id } : null;
    openHarness("master", [target.id], state.job.id);
  }


  function renderJob(job) {
    if(job.id!==state.jobId)return;
    const kpiBox = document.querySelector('.kpis-top');
    if (kpiBox) kpiBox.title = "总任务 / 已完成 / 失败 / 本次花费";
    state.job=job;
    renderReview(job);
    const phase=jobPhase(job);
    if(state.jobLoading)setJobLoading(false);
    const params = job.params || {};
    const isRework = params.kind === "rework";
    const styleCount = (params.selections || []).length;
    // 客户要求：审核期间不要右上角那两行（款式张数 / 基准图修改中）——审核条自己说清楚了
    const strip = document.querySelector(".job-strip");
    if (strip) strip.hidden = String(job.status || "") === "review";
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
    // 9.6.12 总任务 / 已完成 / 失败 由顶栏筛选行的「全部 / 已完成 / 异常」承担（去重），
    // 这里只更新「花费」——不再重复渲染一套数字。
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
    // 9.6.33 被「取消这款」掉的任务（hidden）不再占位置：卡片直接不渲染
    const visible = job.tasks.filter((task) => !task.hidden);
    $("emptyState").hidden = job.tasks.length > 0;
    // 换了任务才重建；同一任务的轮询只增量更新，避免"出一张图就整片重载"
    if (state.cardsJob !== job.id) {
      grid.innerHTML = "";
      state.cards = new Map();
      state.cardsJob = job.id;
      state.logSig = "";
    }
    const seen = new Set();
    visible.forEach((task, index) => {
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
        <button class="btn btn-ghost btn-sm" type="button" data-pause="${esc(task.id)}" hidden>暂停</button>
        <button class="btn btn-ghost btn-sm" type="button" data-rework-menu="${esc(task.id)}"
                title="改图：直接重新生成 / 对话改图 / 框选改图">${icon("i-chat", 13)}改图 ⌄</button>
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
      rework: el.querySelector("[data-rework-menu]"),
      pause: el.querySelector("[data-pause]"),
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

    // 9.6.31 单张暂停/继续：暂停中的显示「继续」，还没出图的显示「暂停」
    if (entry.pause) {
      const paused = Boolean(task.paused) || status === "paused";
      const canPause = !paused && (status === "pending" || status === "queued" || status === "running");
      const show = paused || canPause;
      if (entry.pause.hidden !== !show) entry.pause.hidden = !show;
      const label = paused ? "继续" : "暂停";
      if (entry.pause.textContent !== label) entry.pause.textContent = label;
      entry.pause.dataset.pauseMode = paused ? "resume" : "pause";
      const disabled = !paused && status === "running";
      if (entry.pause.disabled !== disabled) entry.pause.disabled = disabled;
      entry.pause.title = paused
        ? "放回队列接着跑"
        : status === "running"
          ? "这张正在生成，接口调用停不下来：跑完这张就停，不再重试"
          : "暂停这张：不会开跑，之后可以继续";
    }

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
    // 暂停/继续按选中的状态各自可用：选中里有能暂停的就亮「暂停选中」，有暂停中的就亮「继续选中」
    const picked=(state.job?.tasks||[]).filter(t=>state.selected.has(t.id));
    const canPause=picked.some(t=>!t.paused&&["pending","queued","running"].includes(t.status));
    const canResume=picked.some(t=>Boolean(t.paused)||t.status==="paused");
    if($("batchPauseBtn"))$("batchPauseBtn").disabled=state.jobLoading||!canPause;
    if($("batchResumeBtn"))$("batchResumeBtn").disabled=state.jobLoading||!canResume;
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
  /* 9.6.31 单张/多张暂停：某一两张不想跑（或基准图占着审核条）就停掉它，不用整批取消。
     暂停的不会开跑；继续会把它们放回队列（整批已经收尾的话会重新开跑）。 */
  async function pauseTasks(taskIds, resume, button) {
    const ids = (taskIds || []).filter(Boolean);
    const jobId = state.jobId;
    if (!jobId || state.job?.id !== jobId) return;
    if (!ids.length) { toast("bad", "先勾选要处理的那几张"); return; }
    const form = new FormData();
    ids.forEach((id) => form.append("task_ids", id));
    const url = `/api/jobs/${jobId}/${resume ? "resume" : "pause"}`;
    try {
      const res = await api(url, { method: "POST", body: form });
      if (res?.job && state.jobId === jobId) renderJob(res.job);
      toast("ok", resume
        ? `已继续 ${ids.length} 张`
        : `已暂停 ${ids.length} 张（选中后点「继续选中」可以接着跑）`);
      // 暂停会把这批收尾（轮询本来就会停）；继续之后要重新盯上
      if (resume) watchJob(jobId);
    } catch (err) {
      toast("bad", `${resume ? "继续" : "暂停"}失败：${err.message}`);
    }
  }

  /* 9.6.33 「取消这款」：基准图的出口（替代原来的暂停）。取消后本款不再生成，
     这张基准图和该款没开始的颜色都会从列表里撤下（成品文件不删，历史还在）。
     同样两步确认，点别处自动复原。 */
  let cancelStyleArmed = false;
  function disarmCancelStyle(button) {
    cancelStyleArmed = false;
    if (button) {
      button.textContent = "取消这款";
      delete button.dataset.armed;
    }
  }
  async function cancelStyleTasks(taskIds, button) {
    const ids = (taskIds || []).filter(Boolean);
    const jobId = state.jobId;
    if (!jobId || state.job?.id !== jobId || !ids.length) return;
    if (!cancelStyleArmed) {
      cancelStyleArmed = true;
      if (button) {
        button.textContent = "确认取消这款";
        button.dataset.armed = "1";
      }
      setTimeout(() => {
        document.addEventListener("click", function once(event) {
          document.removeEventListener("click", once);
          if (!event.target.closest("[data-cancel-style]")) disarmCancelStyle(button);
        });
      }, 0);
      return;
    }
    cancelStyleArmed = false;
    const form = new FormData();
    ids.forEach((id) => form.append("task_ids", id));
    await withLoading(button, async () => {
      try {
        const res = await api(`/api/jobs/${jobId}/cancel-style`, { method: "POST", body: form });
        if (res?.job && state.jobId === jobId) renderJob(res.job);
        toast("ok", "已取消这个款式：这张基准图和它没开始的颜色都不再显示");
        loadHistory();
      } catch (err) {
        toast("bad", `取消失败：${err.message}`);
      }
    }, "取消中…");
    disarmCancelStyle(button);
  }

  async function cancelJob() {
    if(state.jobLoading||!state.jobId||state.job?.id!==state.jobId)return;
    const jobId=state.jobId;
    await withLoading($("cancelBtn"), async () => {
      try {
        const res = await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
        if (res?.job && state.jobId === jobId) renderJob(res.job);
        toast("ok", "已取消：已经出的成品还在");
        watchJob(jobId);
      }
      catch (err) { toast("bad", err.message); }
    }, "取消中…");
  }

  /* 9.6.28 审核期卡住时的出路：两段式任务停在「等你点确定」不会自己往下走，
     以前只能去工具栏点取消，而且那会儿后端根本没在跑、点了也不收尾。
     两步确认：第一下按钮变成「确认取消这批」，再点才真取消；点别处自动复原（不设超时，
     免得手慢一点就白点）。 */
  let cancelArmed = false;
  function disarmCancel(button) {
    cancelArmed = false;
    if (button) {
      button.textContent = "取消这批";
      delete button.dataset.armed;
    }
  }
  async function cancelReviewBatch(button) {
    const jobId = state.jobId;
    if (!jobId || state.job?.id !== jobId) return;
    if (!cancelArmed) {
      cancelArmed = true;
      if (button) {
        button.textContent = "确认取消这批";
        button.dataset.armed = "1";
      }
      // 下一次点到别处就复原（setTimeout 是为了跳过当前这一下）
      setTimeout(() => {
        document.addEventListener("click", function once(event) {
          document.removeEventListener("click", once);
          if (!event.target.closest("#reviewCancelBtn")) disarmCancel(button);
        });
      }, 0);
      return;
    }
    cancelArmed = false;
    await withLoading(button, async () => {
      try {
        const res = await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
        if (res?.job && state.jobId === jobId) renderJob(res.job);
        toast("ok", "这批已取消：已出的成品还在，没开始的颜色不再生成");
        watchJob(jobId);
        loadHistory();
      } catch (err) {
        toast("bad", `取消失败：${err.message}`);
      }
    }, "取消中…");
    if (button) {
      button.textContent = "取消这批";
      delete button.dataset.armed;
    }
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
  // 9.6.7 关对话框**不**停改图轮询：卡片上的「改图中」要在关掉对话后继续跑完
  function closeHarness(){stopMasterWatch();stopDistillWatch();state.annotationId="";state.annotationBoxes=[];closePanel("harnessDrawer","harnessScrim");window.AIWorkflow?.refresh();}
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
      state.harnessMode="master";state.reworkTargets=taskIds||info.master_ids||[];
      state.reworkSourceJob=target;
      if(!state.reworkTargets||!state.reworkTargets.length)state.annotationSource=null;
      const params=state.job?.params||{};
      state.reworkMeta={provider:String(params.provider||""),model:String(params.model||""),size:String(params.size||"auto")};
      resetChat();state.masterDoneShown=false;state.harnessContextKey=harnessContextKey();
      $("harnessTitle").textContent="AI 基准图确认";
      $("harnessSub").textContent="先看基准图：满意就确认改色，不满意直接说改哪里";
      $("harnessApply").textContent="按对话执行";
      $("harnessDefault").textContent="返回画廊";
      $("reworkModel").hidden=true;
      renderQuick(QUICK_MASTER);openDrawer("harness");updateSummary();
      const pickedTasks = (state.job?.tasks || []).filter((t) => (taskIds || []).includes(t.id));
      const pickedTask = pickedTasks[0] || null;
      const which = pickedTask
        ? `${pickedTask.garment_name || pickedTask.garment || "这个款式"} · `
          + `${pickedTask.color_name || pickedTask.color || ""}`
        : "这张基准图";
      chatBubble("assistant",
        `现在只改「${which}」这一张基准图，其它颜色先不动。`
        + "直接说要改哪里就行（比如「领口再小一点」「印花别动」），我改完把新图发给你看；"
        + "觉得可以了就回工作台点这张的「这张可以」，那时才会开始按它改色。");
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
    const id=String(jobId||"");
    if(state.harnessMode==="master"){
      // 基准图审核期：AI 建的是「改图任务」，出图后要返给用户看，不能算「已提交结束」
      // 同一个改图任务可能被服务端上报两次（工具结果 + 运行事件），这里按 id 去重，
      // 否则对话里会出现两张一模一样的「改后」卡片。
      if(!id||state.reworkJobId===id)return;
      const review=reviewInfo(state.job)||{};
      state.reworkJobId=id;state.harnessReworkSubmitted=true;
      // 只推进「本轮点名的那些基准图」，不能把整批都刷成改图中
      const targets=(state.reworkTargets&&state.reworkTargets.length)
        ? state.reworkTargets.slice()
        : (review.pending_ids||review.master_ids||[]);
      startReworkWatch(id, targets);
      window.AIWorkflow?.phase('reworking');
      loadHistory();toast("ok","已按你说的开始改图，出图后我会返给你看");
      return;
    }
    if(!id||state.harnessSubmittedJob===id)return;
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
      if (state.annotationId) payload.annotation_id = state.annotationId;
      if (state.annotationBoxes?.length) payload.annotations = state.annotationBoxes;
      if (state.annotationSource?.jobId && state.annotationSource?.taskId) {
        payload.source_job_id = state.annotationSource.jobId;
        payload.source_task_id = state.annotationSource.taskId;
      }
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
    state.harnessReworkSubmitted=false;
    state.composedPrompt="";state.requirements="";
    window.AIWorkflow?.phase('connecting');updateSummary();$("harnessSend").classList.add("loading");showTyping();
    try{
      await streamHarness(harnessPayload(text));
      if(!state.harnessSubmittedJob){
        const prepared=state.harnessHasResult&&!!(state.harnessMode==="rework"?state.composedPrompt:state.requirements);
        // 基准审核期本轮已经按框选提交了改图任务：状态说「改图任务已提交」，
        // 不能再用「本轮尚未提交生图」把人劝回去重发。
        window.AIWorkflow?.phase(state.harnessReworkSubmitted?'reworking':(prepared?'ready':'waiting'));
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
    $("batchPauseBtn")?.addEventListener("click", (event) => pauseTasks([...state.selected], false, event.currentTarget));
    $("batchResumeBtn")?.addEventListener("click", (event) => pauseTasks([...state.selected], true, event.currentTarget));
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
      const menuBtn = event.target.closest("[data-rework-menu]");
      if (menuBtn) {
        event.preventDefault();
        if (!menuBtn.disabled) openReworkMenu(menuBtn, menuBtn.dataset.reworkMenu);
        return;
      }
      const pauseBtn = event.target.closest("[data-pause]");
      if (pauseBtn) {
        event.preventDefault();
        if (!pauseBtn.disabled) {
          pauseTasks([pauseBtn.dataset.pause], pauseBtn.dataset.pauseMode === "resume", pauseBtn);
        }
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

    // 「改图」三项菜单（9.6.25）：菜单挂在 body 上，所以在这里统一收口
    document.addEventListener("click", (event) => {
      const menu = document.getElementById("reworkMenu");
      if (!menu) return;
      const act = event.target.closest("[data-rework-act]");
      if (act) {
        event.preventDefault();
        const taskId = menu.dataset.task;
        const kind = act.dataset.reworkAct;
        const task = (state.job?.tasks || []).find((t) => t.id === taskId);
        closeReworkMenu();
        if (!task || task.status !== "done") { toast("bad", "这张还没出图，等成品出来再改"); return; }
        if (kind === "regen") retrySingle(taskId);
        else if (kind === "chat") openHarness("rework", [taskId]);
        else if (kind === "annotate") {
          openAnnotator({
            taskId, jobId: state.jobId, task, mode: "rework",
            url: task.preview || task.thumb || task.thumb_small || "",
          });
        }
        return;
      }
      if (!event.target.closest("#reworkMenu") && !event.target.closest("[data-rework-menu]")) {
        closeReworkMenu();
      }
    });
    window.addEventListener("resize", closeReworkMenu);
    $("galleryScroll")?.addEventListener("scroll", closeReworkMenu, { passive: true });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("harnessDrawer").hidden) closeHarness();
      if (!$("promptDrawer").hidden) closePrompt();
      if (!$("feishuDrawer").hidden) closeFeishu();
      if (!$("accountsDrawer").hidden) hideAccounts();
      closeReworkMenu();
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
    bindAnnotator();
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
