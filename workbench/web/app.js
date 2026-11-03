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
    pending: "排队", running: "生成中", done: "完成",
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
    garment: null,
    colors: new Set(),
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
    pendingText: "",
    typerTimer: null,
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
    const exists = Array.from(el.options).some((option) => option.value === value);
    el.value = exists ? value : (el.options[0] ? el.options[0].value : "");
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
    return `${symbol}${Number(value).toFixed(value < 0.1 ? 3 : 2)}`;
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

  function priceOf(model, size) {
    const entry = (state.pricing.raw || {})[model];
    if (entry === null || entry === undefined) return { unit: null, tier: "", tiered: false };
    if (typeof entry === "number") return { unit: entry, tier: "", tiered: false };
    const tier = tierFor(size);
    const tiers = entry.tiers || {};
    const unit = tiers[tier] !== undefined && tiers[tier] !== null ? tiers[tier] : entry.other;
    return { unit: unit === undefined ? null : unit, tier, tiered: true };
  }

  function priceText(model, size) {
    const info = priceOf(model, size);
    if (info.unit === null) return "未定价";
    return `${money(info.unit, state.pricing.currency)}${info.tiered ? "/" + info.tier : "/张"}`;
  }

  function icon(id, size = 15) {
    return `<svg width="${size}" height="${size}" aria-hidden="true"><use href="#${id}"/></svg>`;
  }
  async function api(path, options) {
    const res = await fetch(path, options);
    let payload = null;
    try { payload = await res.json(); } catch (_) { /* ignore */ }
    if (!res.ok || (payload && payload.ok === false)) {
      throw new Error((payload && payload.error) || `请求失败 HTTP ${res.status}`);
    }
    return payload;
  }
  function toast(kind, message) {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.innerHTML = `${icon(kind === "bad" ? "i-alert" : "i-check")}<span>${esc(message)}</span>`;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), kind === "bad" ? 6000 : 3200);
  }

  /* ------------------------------------------------------------- bootstrap */
  async function boot() {
    try {
      const health = await api("/api/health");
      $("healthLine").textContent = `${health.types} 款 · ${health.providers_ui || health.providers} 接口`;
    } catch (_) { /* ignore */ }

    try {
      const skill = await api("/api/skill");
      state.skill = skill;
      $("skillLine").textContent = `skill · ${skill.root}`;
      if (skill.warnings && skill.warnings.length) {
        $("skillWarn").hidden = false;
        $("skillWarn").textContent = `解析提示 ${skill.warnings.length} 条`;
        $("skillWarn").title = skill.warnings.join("\n");
      }
      const slugs = Object.keys(skill.types);
      const saved = restore("garment", null);
      state.garment = slugs.includes(saved) ? saved : slugs[0];
      state.colors = new Set(skill.types[state.garment].colors.map((c) => c.label));
      renderTypes();
      renderColors();
    } catch (err) {
      $("skillLine").textContent = `skill 读取失败：${err.message}`;
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
    } catch (_) { /* ignore */ }
    bind();
    loadHistory();
    const params = new URLSearchParams(location.search);
    const deep = params.get("job");
    if (deep) watchJob(deep);
    updateSummary();
    if (params.get("panel") === "prompt") showPrompt();
  }

  /* ----------------------------------------------------------------- types */
  function renderTypes() {
    const box = $("types");
    box.innerHTML = "";
    for (const [slug, type] of Object.entries(state.skill.types)) {
      const label = document.createElement("label");
      label.className = "type" + (slug === state.garment ? " sel" : "");
      label.innerHTML = `
        <input type="radio" name="garment" value="${slug}" ${slug === state.garment ? "checked" : ""}/>
        <div>
          <strong>${esc(type.display_name)}</strong>
          <span class="sub">${esc(type.aliases.filter((a) => a !== type.display_name).join(" / "))}</span>
        </div>
        <span class="count">${type.colors.length} 色</span>`;
      label.addEventListener("click", () => {
        state.garment = slug;
        store("garment", slug);
        state.colors = new Set(state.skill.types[slug].colors.map((c) => c.label));
        renderTypes();
        renderColors();
      });
      box.appendChild(label);
    }
    $("typeHint").textContent = state.skill.types[state.garment].batch_name;
  }

  /* ---------------------------------------------------------------- colors */
  function renderColors() {
    const type = state.skill.types[state.garment];
    const box = $("colors");
    box.innerHTML = "";
    type.colors.forEach((color) => {
      const on = state.colors.has(color.label);
      const chip = document.createElement("label");
      chip.className = "chip" + (on ? " sel" : "");
      chip.title = `${color.name_zh} ${color.hex}`;
      chip.innerHTML = `<span class="dot" style="background:${color.hex}"></span>${esc(color.name_zh)}`;
      chip.addEventListener("click", () => {
        if (state.colors.has(color.label)) state.colors.delete(color.label);
        else state.colors.add(color.label);
        renderColors();
      });
      box.appendChild(chip);
    });
    updateSummary();
  }

  function expectedCalls() {
    return state.files.length * state.colors.size;
  }

  function currentModel() {
    const value = $("model").value;
    if (value === "__manual__") return ($("modelManual").value || "").trim();
    return value;
  }

  function renderModelLabels() {
    const size = $("size").value;
    document.querySelectorAll("#model option[data-model]").forEach((option) => {
      option.textContent = `${option.dataset.model} · ${priceText(option.dataset.model, size)}`;
    });
    if (state.modelRows.length) {
      const priced = state.modelRows.filter((m) => priceOf(m.id, size).unit !== null).length;
      $("modelTip").textContent =
        `共 ${state.modelRows.length} 个图片模型 · 已定价 ${priced} 个（按次计费，档位跟随尺寸 ${size === "auto" ? "auto→1K" : size}）`;
    }
  }

  function updateSummary() {
    const items = state.files.length;
    const colors = state.colors.size;
    const calls = expectedCalls();
    $("outputCount").textContent = state.skill
      ? `将产出 ${items * colors} 张成品（${items} 图 × ${colors} 色）· 预计调用 ${calls} 次`
      : "";
    $("concurrency").value = calls || "";

    const model = currentModel();
    const info = priceOf(model, $("size").value);
    const line = $("estimateLine");
    if (!calls) {
      line.textContent = "预计花费 —";
      line.classList.remove("warn");
    } else if (info.unit === null) {
      line.textContent = `预计花费 — · ${model || "未选模型"} 未定价（在 pricing.json 里填该模型单价）`;
      line.classList.add("warn");
    } else {
      const tier = info.tiered ? ` · ${info.tier} 档` : "";
      line.textContent =
        `预计花费 ${money(info.unit * calls, state.pricing.currency)} · ` +
        `${calls} 张 × ${money(info.unit, state.pricing.currency)}${tier}（${model}）`;
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
    });
    applyProvider();
  }

  function applyProvider() {
    const provider = state.providers.find((p) => p.name === $("provider").value);
    if (!provider) return;
    $("providerState").textContent = provider.ready ? "就绪" : provider.reason;
    $("providerState").style.color = provider.ready ? "var(--primary)" : "var(--danger)";
    $("providerTip").textContent =
      `单次最多 ${provider.max_images} 张输入图 · ` +
      (provider.supports_negative ? "支持 negative" : "无 negative 字段，负面词合并进提示词");
    loadModels(provider.name);
  }

  async function loadModels(providerName, refresh) {
    const tip = $("modelTip");
    tip.textContent = "正在读取模型…";
    tip.style.color = "var(--fg-subtle)";
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
      renderModelLabels();

      const wanted = rows.find((m) => m.id === previous) ? previous : (rows[0] ? rows[0].id : "__manual__");
      select.value = wanted;
      $("modelManual").hidden = wanted !== "__manual__";
      tip.textContent = `共 ${data.count} 个模型 · 可选图片模型 ${rows.length} 个（单价来自 pricing.json，可自行修改）`;
      store(`model.${providerName}`, wanted);
    } catch (err) {
      tip.style.color = "var(--danger)";
      tip.textContent = `模型拉取失败：${err.message}（已回退到接口默认模型，可手填）`;
      const select = $("model");
      if (!select.options.length) {
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
      (state.usage.unpriced_images ? ` · ${state.usage.unpriced_images} 张未定价` : "");
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
    setSelect("size", restore("size", state.defaults.size || "auto"));
    setSelect("quality", state.defaults.quality || "high");
    setSelect("outputFormat", restore("outputFormat", state.defaults.output_format || "png"));
    $("sendReference").checked = restore("sendReference", true);
    updateSummary();
  }
  function saveSettings() {
    store("size", $("size").value);
    store("outputFormat", $("outputFormat").value);
    store("sendReference", $("sendReference").checked);
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
    $("startBtn").textContent = "提交中…";
    try {
      const form = new FormData();
      state.files.forEach((entry) => form.append("files", entry.file, entry.file.name));
      form.append("garment_type", state.garment);
      for (const label of state.colors) form.append("colors", label);
      form.append("mode", "single_pass");
      form.append("provider", $("provider").value);
      form.append("model", currentModel());
      form.append("concurrency", "auto");
      form.append("size", $("size").value);
      form.append("quality", $("quality").value);
      form.append("output_format", $("outputFormat").value);
      form.append("send_reference", $("sendReference").checked ? "true" : "false");
      if (extraRequirements) form.append("extra_requirements", extraRequirements);
      form.append("start", "true");
      const data = await api("/api/jobs", { method: "POST", body: form });
      closeHarness();
      watchJob(data.job.id);
      loadHistory();
      loadPricing();
      const est = data.job.params.estimate || {};
      toast("ok", est.priced
        ? `已开工：${data.job.stats.total} 张，预计 ${money(est.total, est.currency)}`
        : "已开工");
    } catch (err) {
      toast("bad", `创建任务失败：${err.message}`);
    } finally {
      state.busy = false;
      $("startBtn").innerHTML = `${icon("i-spark")}开始生成`;
      updateSummary();
    }
  }

  /* ----------------------------------------------------------------- watch */
  function watchJob(jobId) {
    state.jobId = jobId;
    state.selected = new Set();
    try {
      const url = new URL(location.href);
      url.searchParams.set("job", jobId);
      history.replaceState(null, "", url);
    } catch (_) { /* ignore */ }
    if (state.pollTimer) clearTimeout(state.pollTimer);
    const tick = async () => {
      if (!state.jobId) return;
      try {
        const data = await api(`/api/jobs/${state.jobId}`);
        renderJob(data.job);
        if (TERMINAL.includes(data.job.status)) {
          loadHistory();
          loadPricing();
          state.pollTimer = null;
          return;
        }
      } catch (err) {
        $("jobStage").textContent = `读取任务失败：${err.message}`;
      }
      state.pollTimer = setTimeout(tick, 1200);
    };
    tick();
  }

  function renderJob(job) {
    state.job = job;
    const params = job.params || {};
    const isRework = params.kind === "rework";
    $("jobTitle").textContent = isRework
      ? `改图返工 · ${job.stats.total} 张`
      : `${params.garment_name || "任务"} · ${job.stats.total} 张`;
    $("jobStage").textContent = job.stage || job.status;

    const stats = job.stats;
    $("kpiTotal").textContent = stats.total;
    $("kpiDone").textContent = stats.done + stats.skipped;
    $("kpiFailed").textContent = stats.failed;
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
    $("progressLabel").textContent = `${done} / ${stats.total}`;
    $("progressStats").textContent =
      `运行 ${stats.running} · 失败 ${stats.failed}${job.error ? ` · ${job.error}` : ""}`;

    $("cancelBtn").hidden = TERMINAL.includes(job.status);
    $("retryBtn").hidden = !(stats.failed > 0);
    $("downloadBtn").hidden = !job.has_zip;
    if (job.has_zip) $("downloadBtn").href = job.zip;

    const grid = $("gridImgs");
    grid.innerHTML = "";
    $("emptyState").hidden = job.tasks.length > 0;
    for (const task of job.tasks) {
      const card = document.createElement("div");
      card.className = `cardimg st-${task.status}` + (state.selected.has(task.id) ? " sel" : "");
      card.dataset.task = task.id;
      const thumb = task.thumb
        ? `<img src="${task.thumb}?t=${Math.round(job.updated_at)}" loading="lazy" alt="${esc(task.color_name)}成品"/>`
        : `<div class="ph">${STATUS[task.status] || task.status}</div>`;
      card.innerHTML = `
        <label class="pick" title="选择"><input type="checkbox" data-task="${esc(task.id)}" ${state.selected.has(task.id) ? "checked" : ""} aria-label="选择 ${esc(task.item)} ${esc(task.color_name)}"/></label>
        <span class="state">${STATUS[task.status] || task.status}${task.duration ? ` ${task.duration}s` : ""}</span>
        <div class="shot">${thumb}</div>
        <div class="meta">
          <span class="dot" style="background:${task.color_hex}"></span>
          <span class="name">${esc(task.color_name)}</span>
          <span class="time">${esc(task.item)}</span>
        </div>
        ${task.error ? `<div class="err" title="${esc(task.error)}">${esc(task.error)}</div>` : ""}
        <div class="acts">
          <a class="btn btn-ghost btn-sm" ${task.thumb ? `href="/api/jobs/${job.id}/image/${encodeURIComponent(task.id)}?download=true" download` : "aria-disabled=\"true\" style=\"pointer-events:none;opacity:.45\""}>${icon("i-download", 13)}下载</a>
          <button class="btn btn-ghost btn-sm" data-rework="${esc(task.id)}" ${task.status === "done" ? "" : "disabled"}>${icon("i-chat", 13)}改图</button>
          ${task.status === "failed" ? `<button class="btn btn-danger btn-sm" data-retry="${esc(task.id)}">重试</button>` : ""}
        </div>`;
      grid.appendChild(card);
    }

    grid.querySelectorAll("input[data-task]").forEach((input) => {
      input.addEventListener("change", () => {
        if (input.checked) state.selected.add(input.dataset.task);
        else state.selected.delete(input.dataset.task);
        syncSelection();
      });
    });
    grid.querySelectorAll("[data-rework]").forEach((button) => {
      button.addEventListener("click", () => openHarness("rework", [button.dataset.rework]));
    });
    grid.querySelectorAll("[data-retry]").forEach((button) => {
      button.addEventListener("click", () => retrySingle(button.dataset.retry));
    });

    const log = $("log");
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
    log.innerHTML = (job.log || [])
      .map((entry) => `<div class="${entry.level}">${fmtTime(entry.t)} ${esc(entry.msg)}</div>`)
      .join("");
    if (stick) log.scrollTop = log.scrollHeight;

    $("resultToolbar").hidden = !job.tasks.length;
    syncSelection();
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
        const name = job.params.kind === "rework" ? "改图返工" : (job.params.garment_name || job.id);
        item.innerHTML = `<span class="grow">${esc(name)} · ${job.stats.total} 张</span>
          <span class="badge ${job.status}">${job.stats.done}/${job.stats.total}</span>`;
        item.title = `${job.id} · ${job.stage || ""}`;
        item.addEventListener("click", () => watchJob(job.id));
        box.appendChild(item);
      }
    } catch (_) { /* ignore */ }
  }

  /* --------------------------------------------------------------- actions */
  async function cancelJob() {
    if (!state.jobId) return;
    try { await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" }); }
    catch (err) { toast("bad", err.message); }
  }
  async function retryJob() {
    if (!state.jobId) return;
    try {
      await api(`/api/jobs/${state.jobId}/retry`, { method: "POST" });
      watchJob(state.jobId);
    } catch (err) { toast("bad", err.message); }
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
    toast("ok", ids.length ? `正在导出 ${ids.length} 张` : "正在导出全部成品");
  }
  async function showPrompt() {
    try {
      const color = state.colors.values().next().value || null;
      const data = await api("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ garment_type: state.garment, color }),
      });
      $("promptBody").textContent = `【一次到位】\n\n${data.single_pass}\n\n【负面提示词】\n\n${data.negative}`;
      openDrawer("prompt");
    } catch (err) { toast("bad", err.message); }
  }

  /* ------------------------------------------------------- drawer plumbing */
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
    $("chatLog").scrollTop = $("chatLog").scrollHeight;
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
    $("harnessPrompt").hidden = true;
    $("harnessPrompt").textContent = "";
    state.composedPrompt = "";
    state.requirements = "";
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
      chatBubble("assistant", `要返工 ${state.reworkTargets.length} 张。说说要改什么（哪一处、往哪个方向），我先看清楚再动手。`);
    } else {
      try { await ensureDraft(); }
      catch (err) { toast("bad", `上传草稿失败：${err.message}`); return; }
      const items = state.files.length;
      const colors = state.colors.size;
      const name = state.skill ? state.skill.types[state.garment].display_name : "";
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
        `这批：${name} × ${colors} 色 × ${items} 图，${model}，${cost}。\n有特殊要求可以直接说，也可以让我先看图判断风险。`
      );
    }
    $("harnessApply").disabled = true;
    openDrawer("harness");
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
      if (!state.streamBubble) {
        state.streamBubble = chatBubble("assistant", "");
        state.streamBubble.classList.add("streaming");
        state.streamedText = "";
      }
      state.streamedText += payload.text || "";
      pushTypeText(payload.text || "");
    } else if (event === "message") {
      flushType(true);
      state.streamBubble = null;
      const text = String(payload.text || "");
      // 已经流式输出过同一段内容 → 不再重复渲染气泡
      if (text && text.trim() !== state.streamedText.trim()) chatBubble("assistant", text);
      state.streamedText = "";
    } else if (event === "tool") {
      flushType(true);
      state.streamBubble = null;
      chatNote(TOOL_LABEL[payload.name] || payload.name, "");
    } else if (event === "result") {
      const text = String(payload.text || "");
      chatNote(`${TOOL_LABEL[payload.name] || payload.name} 结果`, text, "tool");
    } else if (event === "prompt") {
      flushType(true);
      if (state.streamBubble) state.streamBubble.classList.remove("streaming");
      state.streamBubble = null;
      state.composedPrompt = payload.prompt || "";
      state.requirements = payload.requirements || "";
      if (state.composedPrompt) {
        $("harnessPrompt").hidden = false;
        $("harnessPrompt").textContent = `【将提交的提示词 · ${state.composedPrompt.length} 字，可滚动】\n${state.composedPrompt}`;
      }
      $("harnessApply").disabled = state.harnessMode === "rework"
        ? !state.composedPrompt
        : !state.requirements;
      if (payload.job_id) {
        watchJob(payload.job_id);
        loadHistory();
        loadPricing();
        closeHarness();
        toast("ok", "harness 已提交任务");
      }
    } else if (event === "job") {
      if (payload.job_id) {
        watchJob(payload.job_id);
        loadHistory();
        loadPricing();
        closeHarness();
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
      payload.job_id = state.reworkSourceJob;
      payload.task_ids = state.reworkTargets;
      payload.model = currentModel();
    } else {
      payload.draft_id = state.draftId;
      payload.garment_type = state.garment;
      payload.colors = [...state.colors];
      payload.model = currentModel();
      payload.count = expectedCalls();
      payload.size = $("size").value;
      payload.quality = $("quality").value;
      payload.output_format = $("outputFormat").value;
    }
    return payload;
  }

  async function sendHarnessMessage() {
    if (state.harnessBusy) return;
    const text = ($("harnessInput").value || "").trim();
    if (!text) { toast("bad", "先写一句需求，或直接点「按默认生成」"); return; }
    chatBubble("user", text);
    $("harnessInput").value = "";
    state.harnessBusy = true;
    $("harnessSend").disabled = true;
    $("harnessApply").disabled = true;
    try {
      await streamHarness(harnessPayload(text));
    } catch (err) {
      toast("bad", `harness 出错：${err.message}`);
    } finally {
      state.harnessBusy = false;
      $("harnessSend").disabled = false;
    }
  }

  async function harnessDefaultAction() {
    if (state.harnessMode === "rework") { closeHarness(); return; }
    await createJob("");
  }

  async function harnessApplyAction() {
    if (state.harnessMode === "rework") { await submitRework(); return; }
    await createJob(state.requirements);
  }

  async function submitRework() {
    if (!state.reworkTargets.length) { toast("bad", "没有选中成品"); return; }
    const instruction = ($("harnessInput").value || "").trim() || state.requirements ||
      "按 harness 分析结果调整";
    const form = new FormData();
    for (const id of state.reworkTargets) form.append("task_ids", id);
    form.append("instruction", instruction);
    form.append("provider", $("provider").value);
    form.append("model", currentModel());
    form.append("size", $("size").value);
    form.append("quality", $("quality").value);
    form.append("output_format", $("outputFormat").value);
    if (state.composedPrompt) form.append("prompt_override", state.composedPrompt);
    form.append("start", "true");
    $("harnessApply").disabled = true;
    $("harnessApply").textContent = "提交中…";
    try {
      const data = await api(`/api/jobs/${state.reworkSourceJob}/rework`, { method: "POST", body: form });
      closeHarness();
      watchJob(data.job.id);
      loadHistory();
      loadPricing();
      toast("ok", `已提交 ${state.reworkTargets.length} 张返工`);
    } catch (err) {
      toast("bad", `提交返工失败：${err.message}`);
    } finally {
      $("harnessApply").disabled = false;
      $("harnessApply").textContent = "确认返工";
    }
  }

  /* ------------------------------------------------------------------ bind */
  function startBlockedReason() {
    if (!state.files.length) return "请先上传至少一张原图";
    if (!state.colors.size) return "请至少选择一个颜色";
    if (!currentModel()) return "请选择模型";
    if (!$("provider").value) return "请选择生图接口";
    return "";
  }

  function bind() {
    const drop = $("drop");
    const input = $("fileInput");
    drop.addEventListener("click", () => input.click());
    input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });
    ["dragenter", "dragover"].forEach((type) =>
      drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((type) =>
      drop.addEventListener(type, (event) => { event.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (event) => {
      if (event.dataTransfer && event.dataTransfer.files) addFiles(event.dataTransfer.files);
    });

    $("pickAll").addEventListener("click", () => {
      state.colors = new Set(state.skill.types[state.garment].colors.map((c) => c.label));
      renderColors();
    });
    $("pickNone").addEventListener("click", () => { state.colors.clear(); renderColors(); });

    ["size", "outputFormat", "sendReference"].forEach((id) =>
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
      updateSummary();
    });
    $("modelManual").addEventListener("input", updateSummary);

    $("startBtn").addEventListener("click", () => {
      const reason = startBlockedReason();
      if (reason) { toast("bad", reason); return; }
      openHarness("intake", []);
    });
    $("previewBtn").addEventListener("click", showPrompt);
    $("cancelBtn").addEventListener("click", cancelJob);
    $("retryBtn").addEventListener("click", retryJob);
    $("selAll").addEventListener("change", (event) => {
      const ready = (state.job?.tasks || []).filter((t) => t.out);
      state.selected = event.target.checked ? new Set(ready.map((t) => t.id)) : new Set();
      syncSelection();
    });
    $("batchReworkBtn").addEventListener("click", () => openHarness("rework", [...state.selected]));
    $("batchExportBtn").addEventListener("click", exportSelection);
    $("usageChip").addEventListener("click", async () => {
      await loadPricing();
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
    $("promptClose").addEventListener("click", closePrompt);
    $("promptScrim").addEventListener("click", closePrompt);

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("harnessDrawer").hidden) closeHarness();
      if (!$("promptDrawer").hidden) closePrompt();
    });
  }

  boot();
})();
