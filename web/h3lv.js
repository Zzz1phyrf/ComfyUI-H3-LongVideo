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
                        confirmClass = "primary", secondaryText = "", secondaryClass = "", tone = ""}) {
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
    if (secondaryText) actionButton(buttons, secondaryText, () => finish("secondary"), secondaryClass);
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

function toast(summary, detail = "", severity = "info") {
  app.extensionManager?.toast?.add({severity, summary, detail, life: 3000});
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
    element("p", "这是交给提示词小助手的运镜输入，不是最终 H3 提示词。新项目只需调整镜头方案和表演节奏，生成时长由节点自动控制。旧项目保持原格式可继续生成。", panel, "h3lv-help");
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

async function buildGenerationPayload() {
  const snapshot = await app.graphToPrompt();
  const loaders = Object.entries(snapshot.output).filter(([, node]) =>
    node.class_type === "H3LVUnified");
  const videos = Object.entries(snapshot.output).filter(([, node]) => node.class_type === "VHS_VideoCombine");
  const formatters = Object.entries(snapshot.output).filter(([, node]) => node.class_type === "PromptExpand");
  if (loaders.length !== 1 || videos.length !== 1 || formatters.length !== 1) {
    throw new Error("当前工作流需要且只能有一个 H3 长视频节点、一个提示词小助手和一个 VHS 输出节点。");
  }
  const source = formatters[0][1].inputs?.source_text;
  if (!Array.isArray(source) || String(source[0]) !== String(loaders[0][0]) || Number(source[1]) !== 2) {
    throw new Error("请把长视频节点的 segment_brief 输出连接到提示词小助手的 source_text。");
  }
  return {prompt: snapshot.output, workflow: snapshot.workflow,
    loader_id: loaders[0][0], video_id: videos[0][0], client_id: api.clientId || ""};
}

const startingProjects = new Set();

async function startApprovedSequence(owner, plan) {
  if (startingProjects.has(plan.id)) {
    toast("顺序生成正在启动", "本次点击没有重复提交任务。");
    return;
  }
  if (["running", "pausing", "stopping", "merging"].includes(plan.run_status)) {
    toast("顺序生成正在进行", "本次点击没有重复提交任务。");
    return;
  }
  startingProjects.add(plan.id);
  try {
    const payload = await buildGenerationPayload();
    const failedIndex = plan.segments.findIndex(row => row.job?.status === "failed");
    if (failedIndex >= 0) {
      const failedRow = plan.segments[failedIndex];
      const failureReason = String(failedRow.job?.error || "上一次任务被停止，或节点没有生成可用视频。");
      const decision = await confirmDialog({
        title: `第 ${failedIndex + 1} 段上次没有生成完成`,
        message: `原因：${failureReason}\n\n将重新生成第 ${failedIndex + 1} 段；成功后自动继续后续分段。`,
        confirmText: `重跑第 ${failedIndex + 1} 段并继续`,
        secondaryText: "重新分析分段",
      });
      if (decision === "secondary") {
        await reanalyzeProject(owner, {ask: false});
        return;
      }
      if (!decision) return;
      await request(`/h3lv/project/${plan.id}/retry`, {index: failedIndex});
      payload.replace_snapshot = true;
    }
    await request(`/h3lv/project/${plan.id}/run`, payload);
    clearVideoNodePreview();
    toast("已开始顺序生成", "可在 ComfyUI 任务队列中查看进度；分段审核界面不会自动打开。");
  } finally {
    startingProjects.delete(plan.id);
  }
}

async function analyzeOnly(owner) {
  const snapshot = await app.graphToPrompt();
  const promptNode = snapshot.output?.[String(owner.id)];
  if (!promptNode) throw new Error("没有在执行图中找到 H3 一体化节点。");
  promptNode.inputs.project_id = "";
  promptNode.inputs.segment_index = 0;
  await api.queuePrompt(0, snapshot, {partialExecutionTargets: [String(owner.id)]});
}

async function reanalyzeProject(owner, {ask = true} = {}) {
  const projectId = String(owner.properties?.h3lv_project || "").trim();
  if (projectId) {
    try {
      const current = await request(`/h3lv/project/${projectId}`);
      if (["running", "pausing", "stopping", "merging"].includes(current.run_status)) {
        throw new Error("生成任务正在运行，不能重新分析。");
      }
    } catch (error) {
      if (String(error?.message || error).includes("生成任务正在运行")) throw error;
    }
  }
  if (ask && !await confirmDialog({
    title: "重新分析并分段？",
    message: "将按节点当前设置创建一个新的分段项目；现有项目和已生成文件不会删除。",
    confirmText: "重新分析并分段",
  })) return false;
  owner.properties = {...owner.properties, h3lv_project: ""};
  const projectWidget = owner.widgets?.find(item => item.name === "project_id");
  if (projectWidget) projectWidget.value = "";
  document.getElementById("h3lv-panel")?.remove();
  await analyzeOnly(owner);
  return true;
}

async function openDirectorRules(owner) {
  const nodeMode = owner?.widgets?.find(item => item.name === "mode")?.value;
  const selectedMode = nodeMode === "speaking" ? "speaking" : "singing";
  const modeLabel = selectedMode === "speaking" ? "口播" : "唱歌";
  document.getElementById("h3lv-rules")?.remove();
  const shade = element("div", undefined, document.body, "h3lv-shade h3lv-settings-shade");
  shade.id = "h3lv-rules";
  const panel = element("div", undefined, shade, "h3lv-settings-panel h3lv-rules-panel");
  const title = element("div", undefined, panel, "h3lv-title-row");
  element("h2", `${modeLabel}运镜规则`, title);
  actionButton(title, "关闭", () => shade.remove(), "h3lv-close");
  const explanation = selectedMode === "speaking"
    ? "当前节点选择了 speaking。这里只编辑口播规则：连续固定机位和跨段一致构图。"
    : "当前节点选择了 singing。这里只编辑唱歌规则：按音频能量安排运镜，并避免相邻片段重复同类运动。";
  element("p", `${explanation} 规则不判断图片内容，也不决定音频被切成几段；保存后只用于重新分析的新项目。`, panel, "h3lv-help");
  const rules = await request("/h3lv/rules");
  let fullConfig = JSON.parse(rules.config_text);
  const location = element("p", `当前模式：${modeLabel} · 保存位置：${rules.directory}`, panel, "h3lv-notice");
  const form = element("div", undefined, panel, "h3lv-settings-form");
  const configLabel = element("label", `${modeLabel}运镜配置（JSON）`, form);
  const config = element("textarea", undefined, configLabel, "h3lv-rule-editor h3lv-rule-config");
  config.value = JSON.stringify(fullConfig[selectedMode], null, 2);
  config.rows = selectedMode === "speaking" ? 10 : 28;
  const buttons = element("div", undefined, panel, "h3lv-actions");
  actionButton(buttons, "保存并校验", async () => {
    try {
      fullConfig[selectedMode] = JSON.parse(config.value);
    } catch (_error) {
      throw new Error(`${modeLabel}规则不是有效的 JSON。`);
    }
    const saved = await request("/h3lv/rules", {
      config_text: JSON.stringify(fullConfig),
    });
    fullConfig = JSON.parse(saved.config_text);
    config.value = JSON.stringify(fullConfig[selectedMode], null, 2);
    location.textContent = `当前模式：${modeLabel} · 保存位置：${saved.directory} · 版本 ${saved.revision}`;
    await messageDialog({title: `${modeLabel}规则已保存`, message: `另一套模式的规则没有改变。重新分析音频后，新项目会使用这套${modeLabel}规则。`});
  }, "primary");
  actionButton(buttons, `恢复${modeLabel}默认规则`, async () => {
    if (!await confirmDialog({
      title: `恢复${modeLabel}默认规则？`,
      message: `只覆盖${modeLabel}规则，另一套模式和已经生成的项目不会改变。`,
      confirmText: "恢复默认",
      tone: "warning",
    })) return;
    const reset = await request("/h3lv/rules/reset", {mode: selectedMode});
    fullConfig = JSON.parse(reset.config_text);
    config.value = JSON.stringify(fullConfig[selectedMode], null, 2);
    location.textContent = `当前模式：${modeLabel} · 保存位置：${reset.directory} · 版本 ${reset.revision}`;
  });
  shade.onclick = event => { if (event.target === shade) shade.remove(); };
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

function confidenceClass(value, kind) {
  if (kind === "endpoint") return "neutral";
  if (value === null || value === undefined || value < .5) return "risk";
  return value >= .8 ? "safe" : "review";
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
      if (needsReplacement(row)) element("span", "待重生成", summary, "h3lv-chip risk");
      else if (row.job?.status) element("span", row.job.status, summary, "h3lv-chip neutral");
      card.ontoggle = () => { if (card.open) updateSelected(row.index); };
      const inner = element("div", undefined, card, "h3lv-card-body");
      if (row.video_preview?.filename) {
        inner.classList.add("has-preview");
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
      const end = document.createElement("input");
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
      const promptActions = element("div", undefined, inner, "h3lv-actions h3lv-prompt-actions");
      const prompt = document.createElement("textarea");
      prompt.value = row.prompt;
      prompt.oninput = markDirty;
      actionButton(promptActions, "编辑本段镜头简报", async () => {
        const updated = await editPromptDialog(row.index, prompt.value);
        if (updated === null || updated === prompt.value) return;
        prompt.value = updated;
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
          const payload = await buildGenerationPayload();
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
    notice.textContent = analysis.available === false ? analysis.reason :
      `诊断：${analysis.phrases?.length || 0} 个识别句段 · ${analysis.sections?.length || 0} 个疑似无人声区 · `+
      `${analysis.rhythm?.tempo_bpm ? `约 ${analysis.rhythm.tempo_bpm} BPM（仅次级参考）` : "未取得稳定节拍参考"}`+
      ` · 运镜：${plan.mode === "speaking" ? "口播固定机位规则" : "唱歌动态规则"}`+
      `${analysis.legacy_notice ? ` · ${analysis.legacy_notice}` : ""}`;
    renderCards();
    updateSelected(selected);
    if (details[selected]) details[selected].open = true;
    if (plan.final_video && plan.final_preview?.filename) {
      const result = element("section", undefined, segmentsBody, "h3lv-result");
      const resultHeader = element("div", undefined, result, "h3lv-result-header");
      element("h3", plan.final_stale ? "当前旧版成片（有片段待更新）" : "合并结果", resultHeader);
      const revealButton = actionButton(resultHeader, "打开文件位置", async () => {
        await request(endpoint("/reveal-final"), {});
        revealButton.textContent = "已打开并选中文件";
        await new Promise(resolve => setTimeout(resolve, 1200));
        revealButton.textContent = "打开文件位置";
      }, "reveal-final");
      const video = element("video", undefined, result);
      video.controls = true;
      video.preload = "metadata";
      video.playsInline = true;
      video.src = outputPreviewUrl(plan.final_preview);
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
  actionButton(controls, "重新分析分段", async () => {
    await reanalyzeProject(owner);
  });
  const runButton = actionButton(controls, "▶ 开始顺序生成", async () => {
    if (dirty) throw new Error("请先保存修改并重新确认。");
    if (!plan?.approved) throw new Error("请先确认分段。");
    const payload = await buildGenerationPayload();
    const failedIndex = plan.segments.findIndex(row => row.job?.status === "failed");
    if (failedIndex >= 0) {
      const failedRow = plan.segments[failedIndex];
      const failureReason = String(failedRow.job?.error || "上一次任务被停止，或节点没有生成可用视频。");
      const decision = await confirmDialog({
        title: `第 ${failedIndex + 1} 段上次没有生成完成`,
        message: `原因：${failureReason}\n\n将重新生成第 ${failedIndex + 1} 段；成功后自动继续后续分段。`,
        confirmText: `重跑第 ${failedIndex + 1} 段并继续`,
        secondaryText: "重新分析分段",
      });
      if (decision === "secondary") {
        await reanalyzeProject(owner, {ask: false});
        return;
      }
      if (!decision) return;
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
    app.queuePrompt = async function (number, batchCount = 1, options = {}) {
      const nodes = app.graph?._nodes || [];
      const unified = nodes.filter(node => node.comfyClass === "H3LVUnified");
      if (unified.length === 1) {
        const node = unified[0];
        const requestedTargets = Array.isArray(options) ? options :
          (options?.queueNodeIds ?? options?.partialExecutionTargets);
        const partialTargets = Array.isArray(requestedTargets)
          ? requestedTargets.map(item => String(item?.nodeId ?? item)) : [];
        if (partialTargets.length) {
          const selectedItems = app.canvas?.selectedItems;
          const nodeSelected = Boolean(selectedItems?.has?.(node) ||
            app.canvas?.selected_nodes?.[node.id] === node || node.selected);
          if (partialTargets.includes(String(node.id)) || nodeSelected) {
            await analyzeOnly(node);
            return;
          }
          return originalQueuePrompt.apply(this, arguments);
        }
        const previousProject = String(node.properties?.h3lv_project || "").trim();
        if (previousProject) {
          try {
            const plan = await request(`/h3lv/project/${previousProject}`);
            if (plan.approved) {
              await startApprovedSequence(node, plan);
            } else {
              await openReview(node);
            }
            return;
          } catch (error) {
            if (!String(error?.message || error).includes("项目文件不完整")) throw error;
            node.properties = {...node.properties, h3lv_project: ""};
            const projectWidget = node.widgets?.find(item => item.name === "project_id");
            if (projectWidget) projectWidget.value = "";
          }
        }
        await analyzeOnly(node);
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
        asr_python: "语音识别 Python 覆盖（可选）",
        asr_model: "语音识别模型覆盖（可选）",
        asr_device: "语音识别设备",
      };
      for (const item of this.widgets || []) {
        if (directorLabels[item.name]) item.label = directorLabels[item.name];
      }
      for (const name of ["director_mode", "project_id", "segment_index"]) {
        const internal = this.widgets?.find(item => item.name === name);
        setWidgetHidden(internal, true);
      }
      const inputLabels = {vocals: "分离人声（可选）"};
      for (const input of this.inputs || []) {
        if (inputLabels[input.name]) input.label = inputLabels[input.name];
      }
      const rulesWidget = this.addWidget("button", "运镜规则：查看与修改", null,
        () => openDirectorRules(this).catch(error => messageDialog({
          title: "无法打开运镜规则", message: error.message, tone: "error"})));
      rulesWidget.serialize = false;

      const technicalWidgets = ["asr_python", "asr_model", "asr_device"]
        .map(name => this.widgets?.find(item => item.name === name)).filter(Boolean);
      for (const item of technicalWidgets) setWidgetHidden(item, true);
      const reanalyzeWidget = this.addWidget("button", "重新分析并分段", null,
        () => reanalyzeProject(this).catch(error => messageDialog({
          title: "无法重新分析分段", message: error.message, tone: "error"})));
      reanalyzeWidget.serialize = false;
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
