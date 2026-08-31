import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

if (!document.querySelector("link[data-h3lv-style]")) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = new URL("./h3lv.css", import.meta.url).href;
  link.dataset.h3lvStyle = "1";
  document.head.append(link);
}

async function request(path, body) {
  const response = await api.fetchApi(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || JSON.stringify(result));
  return result;
}

function element(tag, text, parent, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  if (parent) parent.append(node);
  return node;
}

function actionButton(parent, label, action, className = "") {
  const node = element("button", label, parent, `h3lv-button ${className}`.trim());
  node.type = "button";
  node.onclick = async () => {
    node.disabled = true;
    try { await action(); } catch (error) {
      await messageDialog({title: "操作失败", message: error.message || String(error), tone: "error"});
    }
    finally { node.disabled = false; }
  };
  return node;
}

function confirmDialog({title, message, confirmText = "确认", cancelText = "取消",
                        confirmClass = "primary", tone = ""}) {
  return new Promise(resolve => {
    const shade = element("div", undefined, document.body,
      "h3lv-shade h3lv-settings-shade h3lv-confirm-shade");
    const panel = element("div", undefined, shade,
      `h3lv-settings-panel h3lv-confirm-panel ${tone ? `h3lv-dialog-${tone}` : ""}`.trim());
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", title);
    element("h2", title, panel);
    element("p", message, panel, "h3lv-confirm-copy");
    const buttons = element("div", undefined, panel, "h3lv-actions h3lv-confirm-actions");
    let finished = false;
    const finish = value => {
      if (finished) return;
      finished = true;
      window.removeEventListener("keydown", onKeyDown);
      shade.remove();
      resolve(value);
    };
    if (cancelText) actionButton(buttons, cancelText, () => finish(false));
    const confirm = actionButton(buttons, confirmText, () => finish(true), confirmClass);
    const onKeyDown = event => {
      if (event.key === "Escape") finish(false);
      if (event.key === "Enter") finish(true);
    };
    window.addEventListener("keydown", onKeyDown);
    shade.onclick = event => { if (event.target === shade) finish(false); };
    queueMicrotask(() => confirm.focus());
  });
}

function messageDialog({title, message, buttonText = "知道了", tone = ""}) {
  return confirmDialog({title, message, confirmText: buttonText, cancelText: null, tone});
}

function editPromptDialog(index, value) {
  return new Promise(resolve => {
    const shade = element("div", undefined, document.body,
      "h3lv-shade h3lv-settings-shade h3lv-prompt-editor-shade");
    const panel = element("div", undefined, shade, "h3lv-settings-panel h3lv-prompt-editor-panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    const title = element("div", undefined, panel, "h3lv-title-row");
    element("h2", `编辑第 ${index + 1} 段镜头简报`, title);
    element("p", "这是交给 PromptExpand 的结构化输入，不是最终 H3 提示词。保留参考图映射，主要调整构图、运镜、衔接和表演。", panel, "h3lv-help");
    const editor = element("textarea", undefined, panel, "h3lv-prompt-editor");
    editor.value = value;
    editor.spellcheck = false;
    const buttons = element("div", undefined, panel, "h3lv-actions h3lv-confirm-actions");
    let finished = false;
    const finish = result => {
      if (finished) return;
      finished = true;
      window.removeEventListener("keydown", onKeyDown);
      shade.remove();
      resolve(result);
    };
    actionButton(buttons, "取消", () => finish(null));
    actionButton(buttons, "应用到当前草稿", () => finish(editor.value), "primary");
    const onKeyDown = event => { if (event.key === "Escape") finish(null); };
    window.addEventListener("keydown", onKeyDown);
    shade.onclick = event => { if (event.target === shade) finish(null); };
    queueMicrotask(() => editor.focus());
  });
}

function confirmReanalysis() {
  return confirmDialog({
    title: "重新分析当前音频？",
    message: "这个工作流已经分析过音频。重新分析会按当前节点设置创建新的分段项目。要生成完整视频，请使用节点内绿色的“开始顺序生成”按钮。",
    confirmText: "重新分析",
  });
}

async function openDirectorSettings(onSaved) {
  document.getElementById("h3lv-settings")?.remove();
  const shade = element("div", undefined, document.body, "h3lv-shade h3lv-settings-shade");
  shade.id = "h3lv-settings";
  const panel = element("div", undefined, shade, "h3lv-settings-panel");
  const title = element("div", undefined, panel, "h3lv-title-row");
  element("h2", "导演模型连接设置", title);
  actionButton(title, "关闭", () => shade.remove(), "h3lv-close");
  element("p", "仅在节点选择“AI导演”重新分析时调用。插件上传当前参考图和结构化分段信息，不上传音频文件。API Key 只保存在当前 ComfyUI 用户目录，不写入工作流、项目文件或日志。", panel, "h3lv-help");
  const settings = await request("/h3lv/settings");
  const form = element("div", undefined, panel, "h3lv-settings-form");
  const field = (label, value, type = "text") => {
    const row = element("label", label, form);
    const input = element("input", undefined, row);
    input.type = type; input.value = value || "";
    return input;
  };
  const baseUrl = field("OpenAI兼容服务地址", settings.base_url);
  const model = field("模型名称", settings.model);
  const key = field("API Key", "", "password");
  key.placeholder = settings.api_key_configured ? "已配置；留空则保持原值" : "尚未配置";
  const buttons = element("div", undefined, panel, "h3lv-actions");
  actionButton(buttons, "保存导演模型设置", async () => {
    await request("/h3lv/settings", {base_url: baseUrl.value, model: model.value, api_key: key.value});
    await onSaved?.();
    shade.remove();
  }, "primary");
  shade.onclick = event => { if (event.target === shade) shade.remove(); };
}

async function openDirectorRules() {
  document.getElementById("h3lv-rules")?.remove();
  const shade = element("div", undefined, document.body, "h3lv-shade h3lv-settings-shade");
  shade.id = "h3lv-rules";
  const panel = element("div", undefined, shade, "h3lv-settings-panel h3lv-rules-panel");
  const title = element("div", undefined, panel, "h3lv-title-row");
  element("h2", "导演规则", title);
  actionButton(title, "关闭", () => shade.remove(), "h3lv-close");
  element("p", "规则保存在当前 ComfyUI 用户目录，不写入节点，也不会被插件更新覆盖。AI 规则决定大模型如何规划镜头；JSON 配置同时约束规则导演和 AI 导演。新规则只用于重新分析的新项目。", panel, "h3lv-help");
  const rules = await request("/h3lv/rules");
  const location = element("p", `保存位置：${rules.directory}`, panel, "h3lv-notice");
  const form = element("div", undefined, panel, "h3lv-settings-form");
  const aiLabel = element("label", "AI 导演创作规则", form);
  const aiRule = element("textarea", undefined, aiLabel, "h3lv-rule-editor");
  aiRule.value = rules.ai_rule;
  aiRule.rows = 12;
  const configLabel = element("label", "规则导演与本地校验配置（JSON）", form);
  const config = element("textarea", undefined, configLabel, "h3lv-rule-editor h3lv-rule-config");
  config.value = rules.config_text;
  config.rows = 20;
  const buttons = element("div", undefined, panel, "h3lv-actions");
  actionButton(buttons, "保存并校验", async () => {
    const saved = await request("/h3lv/rules", {
      ai_rule: aiRule.value, config_text: config.value
    });
    aiRule.value = saved.ai_rule;
    config.value = saved.config_text;
    location.textContent = `保存位置：${saved.directory} · 版本 ${saved.revision}`;
    await messageDialog({title: "导演规则已保存", message: "重新分析音频后，新项目会使用这套规则。"});
  }, "primary");
  actionButton(buttons, "恢复默认规则", async () => {
    if (!await confirmDialog({
      title: "恢复默认导演规则？",
      message: "当前用户规则会被默认规则覆盖，已经生成的项目不会改变。",
      confirmText: "恢复默认",
      tone: "warning",
    })) return;
    const reset = await request("/h3lv/rules/reset", {});
    aiRule.value = reset.ai_rule;
    config.value = reset.config_text;
    location.textContent = `保存位置：${reset.directory} · 版本 ${reset.revision}`;
  });
  shade.onclick = event => { if (event.target === shade) shade.remove(); };
}

async function refreshAiSettingsButton(node, widget) {
  try {
    const settings = await request("/h3lv/settings");
    widget.name = settings.api_key_configured ?
      "导演模型 API Key：已配置（点击修改）" : "导演模型 API Key：未配置（点击设置）";
  } catch (_) {
    widget.name = "导演模型 API Key：设置入口不可用";
  }
  widget.label = widget.name;
  node.setDirtyCanvas?.(true, true);
}

const originalWidgetComputeSize = new WeakMap();

function setWidgetHidden(widget, hidden) {
  if (!widget) return;
  if (!originalWidgetComputeSize.has(widget)) originalWidgetComputeSize.set(widget, widget.computeSize);
  widget.hidden = hidden;
  widget.computeSize = hidden ? (() => [0, -4]) : originalWidgetComputeSize.get(widget);
}

function resizeNodeToVisibleWidgets(node) {
  const computed = node.computeSize?.();
  if (computed) node.setSize?.([Math.max(node.size?.[0] || 0, computed[0]), computed[1]]);
  node.setDirtyCanvas?.(true, true);
}

function confidenceLabel(value, kind) {
  if (kind === "endpoint") return "端点";
  if (value === null || value === undefined) return "待复核";
  if (value >= .8) return "高置信";
  if (value >= .5) return "中置信";
  return "低置信";
}

function confidenceClass(value, kind) {
  if (kind === "endpoint") return "neutral";
  if (value === null || value === undefined || value < .5) return "risk";
  return value >= .8 ? "safe" : "review";
}

function cutLabel(value) {
  return ({
    "shot-size cut": "景别切",
    "30-degree angle cut": "角度切",
    "shot-size plus angle cut": "景别+角度切",
    "matched-action cut": "动作匹配切",
    "locked-camera continuity cut": "固定机位切",
    "opening": "开场",
  })[value] || value;
}

function needsReplacement(row) {
  return Boolean(row.needs_regeneration && row.job?.status === "completed" && row.job?.video);
}

function outputPreviewUrl(preview) {
  const query = new URLSearchParams({
    filename: preview.filename,
    subfolder: preview.subfolder || "",
    type: preview.type || "output",
  });
  return api.apiURL(`/view?${query.toString()}`);
}

function showFinalOnVideoNode(preview, projectId) {
  if (!preview?.filename) return false;
  const nodes = app.graph?._nodes || [];
  const owners = nodes.filter(node => node.comfyClass === "H3LVUnified");
  const ownerProjects = owners.map(node => String(
    node.properties?.h3lv_project || node.widgets?.find(item => item.name === "project_id")?.value || ""
  ).trim()).filter(Boolean);
  if (projectId && ownerProjects.length && !ownerProjects.includes(String(projectId))) return false;
  const videos = nodes.filter(node => node.comfyClass === "VHS_VideoCombine");
  if (videos.length !== 1 || typeof videos[0].updateParameters !== "function") return false;
  const key = `${preview.subfolder || ""}/${preview.filename}`;
  if (videos[0].__h3lvFinalPreview === key) return true;
  videos[0].updateParameters(preview, true);
  videos[0].__h3lvFinalPreview = key;
  return true;
}

function clearVideoNodePreview() {
  const videos = (app.graph?._nodes || []).filter(node => node.comfyClass === "VHS_VideoCombine");
  if (videos.length !== 1) return false;
  const node = videos[0];
  const preview = node.widgets?.find(widget => widget.name === "videopreview");
  if (!preview) return false;
  preview.videoEl?.pause();
  preview.videoEl?.removeAttribute("src");
  preview.videoEl?.load();
  if (preview.videoEl) preview.videoEl.hidden = true;
  preview.imgEl?.removeAttribute("src");
  if (preview.imgEl) preview.imgEl.hidden = true;
  if (preview.parentEl) preview.parentEl.hidden = true;
  if (preview.value && typeof preview.value === "object") preview.value.params = {};
  preview.aspectRatio = null;
  delete node.__h3lvFinalPreview;
  node.setDirtyCanvas?.(true, true);
  return true;
}

async function restoreFinalVideoPreview() {
  const owners = (app.graph?._nodes || []).filter(node => node.comfyClass === "H3LVUnified");
  const projectIds = [...new Set(owners.map(node => String(
    node.properties?.h3lv_project || node.widgets?.find(item => item.name === "project_id")?.value || ""
  ).trim()).filter(Boolean))];
  for (const projectId of projectIds) {
    try {
      const plan = await request(`/h3lv/project/${encodeURIComponent(projectId)}`);
      if (plan.final_preview) showFinalOnVideoNode(plan.final_preview, projectId);
    } catch {}
  }
}

function statusText(plan) {
  const completed = plan.segments.filter(row => row.job?.status === "completed" && !needsReplacement(row)).length;
  const stale = plan.segments.filter(needsReplacement).length;
  return `状态：${plan.run_status} · ${plan.approved ? "已确认可生成" : "待确认"} · ${completed}/${plan.segments.length} 段可用`+
    `${stale ? ` · ${stale}段待重生成` : ""}${plan.final_stale ? " · 当前成片为旧版" : ""}${plan.error ? ` · ${plan.error}` : ""}`;
}

function createTimeline(canvas, getState, onSelect, onMove, onCommit) {
  let dragIndex = -1;
  const geometry = () => ({left: 50, right: 18, top: 25, height: 178});
  const xFor = (time, width, duration) => {
    const g = geometry();
    return g.left + (Math.max(0, Math.min(duration, time))/duration) * (width-g.left-g.right);
  };
  const timeFor = (clientX) => {
    const {plan} = getState();
    const rect = canvas.getBoundingClientRect();
    const g = geometry();
    const x = Math.max(g.left, Math.min(rect.width-g.right, clientX-rect.left));
    return (x-g.left)/(rect.width-g.left-g.right)*plan.duration;
  };
  function drawWave(ctx, peaks, center, amplitude, width, duration, color) {
    if (!peaks?.length) return;
    ctx.beginPath();
    peaks.forEach((peak, index) => {
      const time = index/Math.max(1, peaks.length-1)*duration;
      const x = xFor(time, width, duration);
      ctx.moveTo(x, center-peak*amplitude);
      ctx.lineTo(x, center+peak*amplitude);
    });
    ctx.strokeStyle = color;
    ctx.globalAlpha = .82;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  function draw() {
    const {plan, analysis, rows, selected} = getState();
    if (!plan || !rows.length) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(640, Math.round(rect.width));
    const height = 220;
    canvas.width = Math.round(width*ratio);
    canvas.height = Math.round(height*ratio);
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    const g = geometry();
    const plotWidth = width-g.left-g.right;
    ctx.fillStyle = "#11151c";
    ctx.fillRect(g.left, g.top, plotWidth, g.height);

    const ends = rows.map(row => Number(row.end.value));
    const starts = [0, ...ends.slice(0, -1)];
    starts.forEach((start, index) => {
      const x1 = xFor(start, width, plan.duration);
      const x2 = xFor(ends[index], width, plan.duration);
      ctx.fillStyle = index === selected ? "rgba(77, 163, 255, .23)" :
        (index%2 ? "rgba(255,255,255,.025)" : "rgba(255,255,255,.055)");
      ctx.fillRect(x1, g.top, Math.max(1, x2-x1), g.height);
    });
    for (const section of analysis?.sections || []) {
      const x1 = xFor(section.start, width, plan.duration);
      const x2 = xFor(section.end, width, plan.duration);
      ctx.fillStyle = "rgba(178, 125, 255, .13)";
      ctx.fillRect(x1, g.top, x2-x1, g.height);
      ctx.fillStyle = "#c6a5ff";
      ctx.font = "11px sans-serif";
      ctx.fillText(section.kind, x1+4, g.top+14);
    }
    ctx.strokeStyle = "rgba(255,255,255,.09)";
    ctx.beginPath();
    [70, 145].forEach(y => { ctx.moveTo(g.left, y); ctx.lineTo(width-g.right, y); });
    ctx.stroke();
    ctx.fillStyle = "#9ea8b7";
    ctx.font = "12px sans-serif";
    ctx.fillText("原曲", 12, 74);
    ctx.fillText("人声", 12, 149);
    drawWave(ctx, analysis?.waveform?.original, 70, 34, width, plan.duration, "#6fb8ff");
    drawWave(ctx, analysis?.waveform?.vocals, 145, 34, width, plan.duration, "#7ee0b1");
    starts.forEach((start, index) => {
      const middle = (start+ends[index])/2;
      const x = xFor(middle, width, plan.duration);
      const label = `第${index+1}段`;
      ctx.font = "bold 12px sans-serif";
      const labelWidth = ctx.measureText(label).width+12;
      ctx.fillStyle = "rgba(12, 16, 22, .72)";
      ctx.fillRect(x-labelWidth/2, g.top+5, labelWidth, 20);
      ctx.fillStyle = "#edf2f8";
      ctx.fillText(label, x-labelWidth/2+6, g.top+19);
    });

    ctx.strokeStyle = "rgba(255, 208, 111, .16)";
    ctx.lineWidth = 1;
    for (const beat of analysis?.rhythm?.bars || []) {
      const x = xFor(beat, width, plan.duration);
      ctx.beginPath(); ctx.moveTo(x, g.top); ctx.lineTo(x, g.top+g.height); ctx.stroke();
    }
    ends.slice(0, -1).forEach((end, index) => {
      const row = plan.segments[index];
      const x = xFor(end, width, plan.duration);
      const cls = confidenceClass(row.boundary_confidence, row.boundary_kind);
      ctx.strokeStyle = cls === "safe" ? "#50d890" : cls === "review" ? "#f1c45c" : "#ff7d7d";
      ctx.lineWidth = dragIndex === index ? 4 : 2;
      ctx.beginPath(); ctx.moveTo(x, g.top); ctx.lineTo(x, g.top+g.height); ctx.stroke();
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath(); ctx.arc(x, g.top, 6, 0, Math.PI*2); ctx.fill();
    });
    ctx.fillStyle = "#9ea8b7";
    ctx.font = "11px sans-serif";
    const ticks = Math.max(2, Math.ceil(plan.duration/15));
    for (let i=0; i<=ticks; i++) {
      const time = plan.duration*i/ticks;
      const x = xFor(time, width, plan.duration);
      ctx.fillText(`${time.toFixed(time < 10 ? 1 : 0)}s`, x-8, 218);
    }
  }
  function nearestBoundary(clientX) {
    const {plan, rows} = getState();
    const rect = canvas.getBoundingClientRect();
    let result = -1, distance = 13;
    rows.slice(0, -1).forEach((row, index) => {
      const x = xFor(Number(row.end.value), rect.width, plan.duration)+rect.left;
      const d = Math.abs(clientX-x);
      if (d < distance) { result = index; distance = d; }
    });
    return result;
  }
  canvas.onpointerdown = event => {
    dragIndex = nearestBoundary(event.clientX);
    if (dragIndex >= 0) {
      canvas.setPointerCapture(event.pointerId);
      onSelect(dragIndex);
    } else {
      const {rows} = getState();
      const time = timeFor(event.clientX);
      const index = rows.findIndex(row => time <= Number(row.end.value));
      onSelect(index < 0 ? rows.length-1 : index);
    }
    draw();
  };
  canvas.onpointermove = event => {
    if (dragIndex < 0) return;
    onMove(dragIndex, timeFor(event.clientX));
    draw();
  };
  const stop = event => {
    const committed = dragIndex;
    if (dragIndex >= 0 && canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    dragIndex = -1;
    if (committed >= 0) onCommit(committed);
    draw();
  };
  canvas.onpointerup = stop;
  canvas.onpointercancel = stop;
  canvas.drawTimeline = draw;
  return canvas;
}

async function openReview(owner) {
  document.getElementById("h3lv-panel")?.remove();
  const shade = element("div", undefined, document.body, "h3lv-shade");
  shade.id = "h3lv-panel";
  shade.setAttribute("role", "dialog");
  shade.setAttribute("aria-modal", "true");
  shade.setAttribute("aria-label", "H3 长视频分段审核");
  const panel = element("div", undefined, shade, "h3lv-panel");
  const header = element("header", undefined, panel, "h3lv-header");
  const titleRow = element("div", undefined, header, "h3lv-title-row");
  element("h2", "H3 长视频 · 音频切分审核", titleRow);
  actionButton(titleRow, "关闭", () => shade.remove(), "h3lv-close");
  const projectRow = element("div", undefined, header, "h3lv-project-row");
  const selectLabel = element("label", "分析项目", projectRow);
  const select = element("select", undefined, selectLabel);
  const projects = await request("/h3lv/projects");
  for (const project of projects) {
    const mode = project.mode === "speaking" ? "口播" : "唱歌";
    const option = element("option", `${new Date(project.created*1000).toLocaleString()} · ${mode} · ${project.duration.toFixed(2)}s · ${project.count}段 · ${project.id.slice(0,8)}`, select);
    option.value = project.id;
  }
  const widget = owner.widgets?.find(item => item.name === "project_id");
  const preferred = owner.properties?.h3lv_project || widget?.value;
  if (projects.some(project => project.id === preferred)) select.value = preferred;
  const status = element("div", "", header, "h3lv-status");
  const controls = element("div", undefined, header, "h3lv-actions");
  const content = element("main", undefined, panel, "h3lv-content");
  const overview = element("section", undefined, content, "h3lv-overview");
  element("p", "先看整曲切点和风险，再逐段试听。调整完成后保存并确认；在生成工作流中点击绿色按钮启动完整循环。", overview, "h3lv-help");
  const notice = element("div", "", overview, "h3lv-notice");
  const canvas = element("canvas", undefined, overview, "h3lv-timeline");
  canvas.tabIndex = 0;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "整首歌原曲和人声波形；内部切点可用鼠标或触控拖动，键盘用户可使用下方结束时间输入框");
  const selectedBar = element("div", undefined, overview, "h3lv-selected-bar");
  const selectedText = element("strong", "", selectedBar);
  const selectedAudio = element("audio", undefined, selectedBar);
  selectedAudio.controls = true;
  selectedAudio.preload = "metadata";
  const segmentsBody = element("section", undefined, content, "h3lv-segments");
  let plan = null;
  let analysis = null;
  let rows = [];
  let details = [];
  let dirty = false;
  let selected = 0;
  const endpoint = (suffix = "") => `/h3lv/project/${encodeURIComponent(select.value)}${suffix}`;
  const state = () => ({plan, analysis, rows, selected});

  function markDirty() {
    dirty = true;
    const completed = plan.segments.filter(row => row.job?.status === "completed" && !needsReplacement(row)).length;
    status.textContent = `状态：draft · 修改未保存，尚不可生成 · ${completed}/${plan.segments.length} 段完成`;
    status.classList.add("is-dirty");
  }
  function previewUrl(index, boundary = false) {
    const start = index === 0 ? 0 : Number(rows[index-1].end.value);
    const end = Number(rows[index].end.value);
    return api.apiURL(endpoint(`/audio?index=${index}&start=${start.toFixed(6)}&end=${end.toFixed(6)}`+
      `${boundary ? "&boundary=1&vocals=1" : ""}&revision=${plan.revision}`));
  }
  function refreshPreviewAudio(index) {
    if (!rows[index]) return;
    if (selected === index || selected === index+1) selectedAudio.src = previewUrl(selected);
    if (rows[index].cut) rows[index].cut.src = previewUrl(index, true);
  }
  function updateSelected(index, openCard = false, refreshAudio = true) {
    if (!plan?.segments.length) return;
    selected = Math.max(0, Math.min(plan.segments.length-1, index));
    const start = selected === 0 ? 0 : Number(rows[selected-1].end.value);
    const end = Number(rows[selected].end.value);
    selectedText.textContent = `当前试听：第 ${selected+1} 段 · ${start.toFixed(3)}—${end.toFixed(3)}s`;
    if (refreshAudio) selectedAudio.src = previewUrl(selected);
    if (openCard && details[selected]) {
      details.forEach((item, i) => { item.open = i === selected; });
      details[selected].scrollIntoView({behavior: "smooth", block: "nearest"});
    }
    canvas.drawTimeline?.();
  }
  function refreshDraftDisplays() {
    rows.forEach((item, index) => {
      const start = index === 0 ? 0 : Number(rows[index-1].end.value);
      const end = Number(item.end.value);
      const duration = end-start;
      const editFrames = Math.round(end*24)-Math.round(start*24);
      const generationFrames = 5+17*Math.ceil((Math.max(124, Math.ceil(duration*24), editFrames)-5)/17);
      item.time.textContent = `${start.toFixed(3)}—${end.toFixed(3)}s`;
      item.duration.textContent = `${duration.toFixed(3)}s`;
      item.editFrames.textContent = `${editFrames} 剪辑帧`;
      item.generationFrames.textContent = `${generationFrames} 生成帧`;
    });
  }
  function moveBoundary(index, proposed) {
    const start = index === 0 ? 0 : Number(rows[index-1].end.value);
    const nextEnd = Number(rows[index+1].end.value);
    const lower = Math.max(start+3, nextEnd-plan.max_seconds);
    const upper = Math.min(start+plan.max_seconds, nextEnd-3);
    rows[index].end.value = Math.max(lower, Math.min(upper, proposed)).toFixed(3);
    refreshDraftDisplays();
    markDirty();
    updateSelected(index, false, false);
  }
  createTimeline(canvas, state, index => updateSelected(index, true), moveBoundary,
    index => { refreshPreviewAudio(index); updateSelected(selected); });

  function renderCards() {
    segmentsBody.replaceChildren(); rows = []; details = [];
    plan.segments.forEach(row => {
      const card = element("details", undefined, segmentsBody, "h3lv-card");
      const summary = element("summary", undefined, card);
      const main = element("span", undefined, summary, "h3lv-summary-main");
      element("strong", `第 ${row.index+1} 段`, main);
      const time = element("span", `${row.start.toFixed(3)}—${row.end.toFixed(3)}s`, main, "h3lv-time");
      const duration = element("span", `${row.duration.toFixed(3)}s`, main, "h3lv-duration");
      element("span", row.reason || "未标注切点原因", summary, "h3lv-reason");
      element("span", confidenceLabel(row.boundary_confidence, row.boundary_kind), summary,
        `h3lv-chip ${confidenceClass(row.boundary_confidence, row.boundary_kind)}`);
      if (row.entry_cut_strategy) element("span", `剪辑：${cutLabel(row.entry_cut_strategy)}`,
        summary, `h3lv-chip ${row.entry_cut_risk === "low" ? "safe" : "review"}`);
      if (needsReplacement(row)) element("span", "待重生成", summary, "h3lv-chip risk");
      else if (row.job?.status) element("span", row.job.status, summary, "h3lv-chip neutral");
      card.ontoggle = () => { if (card.open) updateSelected(row.index); };
      const inner = element("div", undefined, card, "h3lv-card-body");
      if (row.video_preview?.filename) {
        const preview = element("figure", undefined, inner, "h3lv-segment-preview");
        element("figcaption", "当前分段结果", preview);
        const video = element("video", undefined, preview);
        video.controls = true;
        video.preload = "metadata";
        video.playsInline = true;
        video.src = outputPreviewUrl(row.video_preview);
      }
      element("p", row.text || "未识别出文字，人声状态仍需试听确认。", inner, "h3lv-lyrics");
      if (row.warnings?.length) {
        const warningList = element("ul", undefined, inner, "h3lv-warnings");
        row.warnings.forEach(warning => element("li", warning, warningList));
      }
      const metrics = element("div", undefined, inner, "h3lv-metrics");
      const generationFrames = element("span", `${row.generation_frames} 生成帧`, metrics);
      const editFrames = element("span", `${row.edit_frames} 剪辑帧`, metrics);
      const line = element("label", "结束时间（秒）", metrics);
      const end = element("input", undefined, line);
      end.type = "number"; end.step = ".01"; end.value = row.end;
      end.disabled = row.index === plan.segments.length-1;
      end.min = row.start+3; end.max = row.start+plan.max_seconds;
      end.oninput = () => {
        if (!Number.isFinite(Number(end.value))) return;
        refreshDraftDisplays(); markDirty(); updateSelected(selected, false, false); canvas.drawTimeline?.();
      };
      end.onchange = () => { refreshPreviewAudio(row.index); updateSelected(selected); };
      let cut = null;
      if (row.index < plan.segments.length-1) {
        element("p", "切点前后分离人声试听（播放器约第 2 秒是切点）", inner, "h3lv-audio-label");
        cut = element("audio", undefined, inner, "h3lv-cut-audio");
        cut.controls = true; cut.preload = "metadata";
        cut.src = api.apiURL(endpoint(`/audio?index=${row.index}&boundary=1&vocals=1&revision=${plan.revision}`));
      }
      const advanced = element("details", undefined, inner, "h3lv-advanced");
      element("summary", "镜头简报 / PromptExpand 输入", advanced);
      element("p", "这不是最终 H3 提示词。", advanced, "h3lv-notice");
      const promptActions = element("div", undefined, advanced, "h3lv-actions h3lv-prompt-actions");
      const promptPreview = element("pre", row.prompt, advanced, "h3lv-prompt-preview");
      const prompt = element("textarea", undefined, advanced, "h3lv-prompt-source");
      prompt.value = row.prompt;
      prompt.oninput = markDirty;
      actionButton(promptActions, "编辑本段镜头简报", async () => {
        const updated = await editPromptDialog(row.index, prompt.value);
        if (updated === null || updated === prompt.value) return;
        prompt.value = updated;
        promptPreview.textContent = updated;
        prompt.dispatchEvent(new Event("input"));
      }, "prompt-edit");
      rows.push({end, prompt, duration, time, generationFrames, editFrames, cut});
      details.push(card);
      if (row.job || needsReplacement(row)) {
        element("p", `生成状态：${row.job?.status || "待生成"}${needsReplacement(row) ? ` · ${row.regeneration_reason || "需要重新生成"}` : ""}`+
          `${row.job?.error ? ` · ${row.job.error}` : ""}`, inner, "h3lv-job");
        actionButton(inner, "重新生成本段", async () => {
          if (dirty) throw new Error("请先保存并确认当前修改。");
          if (!plan.approved) throw new Error("请先保存并确认分段方案。");
          if (!await confirmDialog({
            title: `重新生成第 ${row.index+1} 段？`,
            message: "只重新生成当前段，其他完成段不会重跑；当前成功版本会保留，生成失败时可以恢复。",
            confirmText: "重新生成",
          })) return;
          const payload = await generationPayload();
          await request(endpoint("/regenerate"), {...payload, index: row.index});
          await load();
        }, "segment-run");
      }
      if (row.takes?.length) {
        actionButton(inner, `恢复上一版（${row.takes.length}）`, async () => {
          if (!await confirmDialog({
            title: `恢复第 ${row.index+1} 段上一版？`,
            message: "恢复后不会重跑其他片段，但需要重新合成最终视频。",
            confirmText: "恢复上一版",
          })) return;
          await request(endpoint("/restore"), {index: row.index}); await load();
        });
      }
      if (row.failed_attempts?.length) {
        element("p", `保留了 ${row.failed_attempts.length} 次失败的新版本记录；当前仍采用上一个成功版本。`, inner, "h3lv-job");
      }
    });
  }

  async function load() {
    if (!select.value) {
      status.textContent = "还没有分析项目，请先运行 H3 音频分析节点。";
      notice.textContent = "没有可审核的项目。";
      return;
    }
    plan = await request(endpoint());
    analysis = await request(endpoint("/analysis"));
    if (widget) widget.value = plan.id;
    owner.properties = {...owner.properties, h3lv_project: plan.id};
    if (plan.final_preview) showFinalOnVideoNode(plan.final_preview, plan.id);
    dirty = false; selected = Math.min(selected, plan.segments.length-1);
    status.textContent = statusText(plan);
    status.classList.remove("is-dirty");
    const failedIndex = plan.segments.findIndex(row => row.job?.status === "failed");
    runButton.textContent = failedIndex >= 0 ? `▶ 重试第 ${failedIndex+1} 段并继续` :
      (["paused", "stopped"].includes(plan.run_status) ? "▶ 继续顺序生成" : "▶ 开始顺序生成");
    const referenceLabel = ({single_composite: "单图：人物+场景",
      solo_scene: "双图：图1人物，图2场景"})[plan.references?.layout] || "旧版双图单人+场景";
    notice.textContent = analysis.available === false ? analysis.reason :
      `诊断：${analysis.phrases?.length || 0} 个识别句段 · ${analysis.sections?.length || 0} 个疑似无人声区 · `+
      `${analysis.rhythm?.tempo_bpm ? `约 ${analysis.rhythm.tempo_bpm} BPM（仅次级参考）` : "未取得稳定节拍参考"}`+
      ` · 图片组合：${referenceLabel} · 导演：${plan.director?.mode === "ai" ? "AI整曲规划" : "本地规则"}`+
      `${analysis.legacy_notice ? ` · ${analysis.legacy_notice}` : ""}`;
    renderCards();
    updateSelected(selected);
    if (details[selected]) details[selected].open = true;
    if (plan.final_video) {
      const result = element("section", undefined, segmentsBody, "h3lv-result");
      element("h3", plan.final_stale ? "当前旧版成片（有片段待更新）" : "合并结果", result);
      const video = element("video", undefined, result);
      video.controls = true;
      video.src = api.apiURL(endpoint("/final"));
    }
  }

  actionButton(controls, "保存并确认", async () => {
    if (!await confirmDialog({
      title: "保存并确认分段？",
      message: "请确认已经试听并检查所有切点。保存后，这个项目将允许开始顺序生成。",
      confirmText: "保存并确认",
    })) return;
    let revision = plan.revision;
    if (dirty) {
      const saved = await request(endpoint("/edit"), {revision,
        segments: rows.map(row => ({end: Number(row.end.value), prompt: row.prompt.value}))});
      revision = saved.revision;
    }
    await request(endpoint("/approve"), {revision});
    await load();
  }, "primary");
  async function generationPayload() {
    const snapshot = await app.graphToPrompt();
    const loaders = Object.entries(snapshot.output).filter(([, node]) =>
      node.class_type === "H3LVUnified");
    const videos = Object.entries(snapshot.output).filter(([, node]) => node.class_type === "VHS_VideoCombine");
    if (loaders.length !== 1 || videos.length !== 1) throw new Error("当前工作流需要且只能有一个 H3 长视频一体化节点和一个 VHS 输出节点。");
    return {prompt: snapshot.output, workflow: snapshot.workflow,
      loader_id: loaders[0][0], video_id: videos[0][0], client_id: api.clientId || ""};
  }
  const runButton = actionButton(controls, "▶ 开始顺序生成", async () => {
    if (dirty) throw new Error("请先保存修改并重新确认。");
    if (!plan?.approved) throw new Error("请先确认分段。");
    const payload = await generationPayload();
    const failedIndex = plan.segments.findIndex(row => row.job?.status === "failed");
    if (failedIndex >= 0) {
      if (!await confirmDialog({
        title: `第 ${failedIndex+1} 段未完成`,
        message: "将按当前工作流设置重新生成这一段，成功后继续生成后续分段。",
        confirmText: "重新生成并继续",
      })) return;
      await request(endpoint("/retry"), {index: failedIndex});
      payload.replace_snapshot = true;
    }
    await request(endpoint("/run"), payload);
    clearVideoNodePreview();
    await load();
  }, "run");
  actionButton(controls, "当前段完成后暂停", async () => {
    await request(endpoint("/pause"), {}); await load();
  }, "pause");
  actionButton(controls, "■ 停止后续生成", async () => {
    if (!await confirmDialog({
      title: "停止提交后续片段？",
      message: "当前正在生成的片段会正常完成，完成后不再提交新的片段。",
      confirmText: "停止后续生成",
      confirmClass: "stop",
      tone: "warning",
    })) return;
    await request(endpoint("/stop"), {}); await load();
  }, "stop");
  actionButton(controls, "仅重新合成", async () => {
    await request(endpoint("/assemble"), {}); await load();
  });
  actionButton(controls, "复制成片目录", async () => {
    const result = await request("/h3lv/final-folder");
    try {
      await navigator.clipboard.writeText(result.path);
      await messageDialog({title: "成片目录已复制", message: result.path});
    } catch {
      await messageDialog({title: "成片目录", message: result.path});
    }
  });
  actionButton(controls, "刷新状态", load);
  select.onchange = () => {
    selected = 0;
    load().catch(error => messageDialog({title: "项目加载失败", message: error.message, tone: "error"}));
  };
  window.addEventListener("resize", () => canvas.drawTimeline?.(), {passive: true});
  await load();
  const timer = setInterval(async () => {
    if (!shade.isConnected) { clearInterval(timer); return; }
    if (dirty || !plan || !["running", "pausing", "stopping", "merging"].includes(plan.run_status)) return;
    try {
      const latest = await request(endpoint());
      status.textContent = statusText(latest);
      const oldDone = plan.segments.filter(row => row.job?.status === "completed").length;
      const newDone = latest.segments.filter(row => row.job?.status === "completed").length;
      if (latest.run_status !== plan.run_status || oldDone !== newDone) await load();
    } catch (error) { status.textContent = error.message; }
  }, 2000);
}

app.registerExtension({
  name: "PixelFantasy.H3LongVideo",
  afterConfigureGraph() {
    setTimeout(() => restoreFinalVideoPreview(), 0);
  },
  async setup() {
    if (!app.__h3lvFinalListenerInstalled) {
      app.__h3lvFinalListenerInstalled = true;
      api.addEventListener("h3lv-final", event => {
        const data = event.detail || {};
        showFinalOnVideoNode(data.preview, data.project_id);
      });
      api.addEventListener("h3lv-segment", event => {
        const data = event.detail || {};
        showFinalOnVideoNode(data.preview, data.project_id);
      });
    }
    if (app.__h3lvQueueGuardInstalled) return;
    app.__h3lvQueueGuardInstalled = true;
    const originalQueuePrompt = app.queuePrompt.bind(app);
    app.queuePrompt = async function () {
      const nodes = app.graph?._nodes || [];
      const unified = nodes.filter(node => node.comfyClass === "H3LVUnified");
      if (unified.length === 1) {
        const node = unified[0];
        const previousProject = String(node.properties?.h3lv_project || "").trim();
        if (previousProject && !await confirmReanalysis()) return;
        const directorMode = node.widgets?.find(item => item.name === "director_mode")?.value;
        const contentMode = node.widgets?.find(item => item.name === "mode")?.value;
        if (["AI导演", "ai"].includes(directorMode) && contentMode !== "speaking") {
          const settings = await request("/h3lv/settings");
          if (!settings.api_key_configured) {
            await messageDialog({
              title: "AI导演尚未配置 API",
              message: "请先填写兼容服务地址、模型名称和 API Key。关闭提示后将打开设置窗口。",
              buttonText: "去配置",
            });
            await openDirectorSettings();
            return;
          }
        }
        const snapshot = await app.graphToPrompt();
        const promptNode = snapshot.output?.[String(node.id)];
        if (!promptNode) throw new Error("没有在执行图中找到 H3 一体化节点。");
        promptNode.inputs.project_id = "";
        promptNode.inputs.segment_index = 0;
        await api.queuePrompt(0, snapshot, {partialExecutionTargets: [String(node.id)]});
        return;
      }
      return originalQueuePrompt.apply(this, arguments);
    };
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "H3LVUnified") return;
    const oldCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = oldCreated?.apply(this, arguments);
      const directorLabels = {
        camera_activity: "镜头活跃度",
        widest_framing: "最远允许景别",
        director_mode: "导演方式",
        reference_layout: "图片模式",
      };
      for (const item of this.widgets || []) {
        if (directorLabels[item.name]) item.label = directorLabels[item.name];
      }
      const legacyBrief = this.widgets?.find(item => item.name === "visual_brief");
      setWidgetHidden(legacyBrief, true);
      const legacyVocal = this.widgets?.find(item => item.name === "vocal_assignment");
      setWidgetHidden(legacyVocal, true);
      setWidgetHidden(this.widgets?.find(item => item.name === "performance_intensity"), true);
      setWidgetHidden(this.widgets?.find(item => item.name === "director_note"), true);
      for (const name of ["asr_python", "asr_model", "asr_device"]) {
        const technical = this.widgets?.find(item => item.name === name);
        if (!technical) continue;
        if (name === "asr_device") technical.value = "auto";
        else technical.value = "";
        setWidgetHidden(technical, true);
      }
      for (const name of ["project_id", "segment_index"]) {
        const internal = this.widgets?.find(item => item.name === name);
        setWidgetHidden(internal, true);
      }
      const inputLabels = {
        vocals: "分离人声（可选）",
        reference_image_1: "人物图 / 单图",
        reference_image_2: "独立场景图（双图模式）",
      };
      for (const input of this.inputs || []) {
        if (inputLabels[input.name]) input.label = inputLabels[input.name];
      }
      let settingsWidget;
      settingsWidget = this.addWidget("button", "导演模型 API Key：检查配置中", null,
        () => openDirectorSettings(() => refreshAiSettingsButton(this, settingsWidget)));
      settingsWidget.serialize = false;
      refreshAiSettingsButton(this, settingsWidget);
      const rulesWidget = this.addWidget("button", "导演规则：查看与修改", null,
        () => openDirectorRules());
      rulesWidget.serialize = false;
      const widget = this.addWidget("button", "打开分段与生成控制", null,
        () => openReview(this).catch(error => messageDialog({
          title: "无法打开分段审核", message: error.message, tone: "error"})));
      widget.serialize = false;
      setTimeout(() => resizeNodeToVisibleWidgets(this), 0);
      return result;
    };
    const oldExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      oldExecuted?.apply(this, arguments);
      if (message?.h3lv_project?.[0]) {
        const projectId = message.h3lv_project[0];
        this.properties = {...this.properties, h3lv_project: projectId};
        const ownProject = this.widgets?.find(item => item.name === "project_id");
        if (ownProject) ownProject.value = projectId;
        setTimeout(() => openReview(this).catch(error => messageDialog({
          title: "无法打开分段审核", message: error.message, tone: "error"})), 0);
      }
    };
  }
});
