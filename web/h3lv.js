import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { materialEditor } from "./materials.js";
import { createTimeline, pcmWavePeaks } from "./timeline.js";

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
    element("h2", `编辑第 ${index + 1} 段镜头简报（提示词）`, title);
    element("p", "这是本段输出的文本，可直接连接下游文本输入，也可交给提示词小助手扩写。既可以只写镜头方案和表演节奏，也可以直接粘贴完整提示词（含参考图和主体标签）。生成时长由节点自动控制。", panel, "h3lv-help");
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
  if (loaders.length !== 1 || videos.length !== 1) {
    throw new Error("当前工作流需要且只能有一个 H3 长视频节点和一个 VHS 输出节点。");
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
  const previous = document.getElementById("h3lv-panel");
  previous?.querySelector("canvas")?.disposeTimeline?.();
  previous?.remove();
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
    : "唱歌景别由 allowed_framings 控制，运镜由各能量级别的 energy_movements 控制。这里也可以调整机位角度及相邻运动关系。";
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

function referenceSlotLimit() {
  let limit = 0;
  for (const node of app.graph?._nodes || []) {
    if (node.comfyClass !== "MiniMaxH3ReferenceToVideo") continue;
    limit = Math.max(limit, (node.inputs || []).filter(input =>
      /^ref_images\.ref_image_\d+$/.test(String(input.name || "")) && input.link != null).length);
  }
  return limit;
}

function referencePreviewUrl(projectId, name) {
  return api.apiURL(`/h3lv/project/${encodeURIComponent(projectId)}/refs/${encodeURIComponent(name)}`);
}

async function uploadReferenceImage(projectId, index, file) {
  const form = new FormData();
  form.append("index", String(index));
  form.append("image", file, file.name);
  const response = await api.fetchApi(
    `/h3lv/project/${encodeURIComponent(projectId)}/refs`, {method: "POST", body: form});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || "参考图上传失败。");
  return result.name;
}

async function removeReferenceImage(projectId, name) {
  try {
    await request(`/h3lv/project/${encodeURIComponent(projectId)}/refs/remove`, {name});
  } catch {}
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

async function openReview(owner) {
  const previous = document.getElementById("h3lv-panel");
  previous?.querySelector("canvas")?.disposeTimeline?.();
  previous?.remove();
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
  const defaultRow = element("label", "默认参考图张数", projectRow, "h3lv-default-references");
  const defaultSelect = element("select", undefined, defaultRow);
  defaultSelect.onchange = () => { if (plan) markDirty(); };

  function syncDefaultReferenceControl() {
    const available = referenceSlotLimit();
    const current = plan?.reference_default_count;
    const highest = Math.max(available, Number.isInteger(current) ? current : 0);
    defaultSelect.replaceChildren();
    const fallback = element("option", "全部（沿用画布）", defaultSelect);
    fallback.value = "all";
    for (let count = 0; count <= highest; count += 1) {
      const item = element("option", String(count), defaultSelect);
      item.value = String(count);
    }
    defaultSelect.value = current === null || current === undefined ? "all" : String(current);
    defaultRow.hidden = Boolean(plan?.materials_version) || available === 0;
  }

  function defaultReferenceText() {
    const value = plan?.reference_default_count;
    if (value === null || value === undefined) return "画布上全部已接出的图片";
    if (!value) return "不使用参考图";
    return `画布上的前 ${value} 张`;
  }

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
  let selectedTrack = "original";
  const selectedBar = element("div", undefined, overview, "h3lv-selected-bar");
  const selectedInfo = element("div", undefined, selectedBar, "h3lv-selected-info");
  const trackSwitch = element("div", undefined, selectedInfo, "h3lv-track-switch");
  const trackButtons = {};
  for (const [value, label] of [["original", "原曲"], ["vocals", "人声"]]) {
    const button = element("button", label, trackSwitch, "h3lv-track-button");
    button.type = "button";
    button.onclick = () => selectTrack(value);
    trackButtons[value] = button;
  }
  const selectedText = element("strong", "", selectedInfo);
  const selectedAudio = element("audio", undefined, selectedBar);
  selectedAudio.controls = true;
  selectedAudio.preload = "metadata";
  const defaultMaterials = element("section", undefined, content, "h3lv-default-materials");
  let defaultEditor = null;
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
  function previewUrl(index, vocals = false) {
    const start = index === 0 ? 0 : Number(rows[index-1].end.value);
    const end = Number(rows[index].end.value);
    return api.apiURL(endpoint(`/audio?index=${index}&start=${start.toFixed(6)}&end=${end.toFixed(6)}`+
      `${vocals ? "&vocals=1" : ""}&revision=${plan.revision}`));
  }
  function selectedPreviewUrl(index) {
    return previewUrl(index, selectedTrack === "vocals");
  }
  function syncTrackSwitch() {
    for (const [value, button] of Object.entries(trackButtons)) {
      button.classList.toggle("is-active", value === selectedTrack);
      button.setAttribute("aria-pressed", String(value === selectedTrack));
    }
  }
  function selectTrack(value) {
    if (selectedTrack === value) return;
    selectedTrack = value;
    syncTrackSwitch();
    if (rows.length) selectedAudio.src = selectedPreviewUrl(selected);
    updateSelected(selected, false);
  }
  syncTrackSwitch();
  function refreshPreviewAudio(index) {
    if (!rows[index]) return;
    if (selected === index || selected === index+1) selectedAudio.src = selectedPreviewUrl(selected);
    if (rows[index].audio) rows[index].audio.src = previewUrl(index);
  }
  function updateSelected(index, refreshAudio = true) {
    if (!plan?.segments.length) return;
    selected = Math.max(0, Math.min(plan.segments.length-1, index));
    const start = selected === 0 ? 0 : Number(rows[selected-1].end.value);
    const end = Number(rows[selected].end.value);
    selectedText.textContent = `当前试听（${selectedTrack === "vocals" ? "人声" : "原曲"}）：`+
      `第 ${selected+1} 段 · ${start.toFixed(3)}—${end.toFixed(3)}s`;
    if (refreshAudio) selectedAudio.src = selectedPreviewUrl(selected);
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
    updateSelected(index, false);
  }
  createTimeline(canvas, state, index => updateSelected(index), moveBoundary,
    index => { refreshPreviewAudio(index); updateSelected(selected); },
    async (start, end, signal) => {
      const path = endpoint(`/audio?index=0&start=${start.toFixed(6)}&end=${end.toFixed(6)}`);
      const waves = await Promise.all([false, true].map(async vocals => {
        const response = await api.fetchApi(path+(vocals ? "&vocals=1" : ""), {signal});
        if (!response.ok) throw new Error("局部波形读取失败");
        return pcmWavePeaks(await response.arrayBuffer());
      }));
      return {original: waves[0], vocals: waves[1]};
    });

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
        refreshDraftDisplays(); markDirty(); updateSelected(selected, false); canvas.drawTimeline?.();
      };
      end.onchange = () => { refreshPreviewAudio(row.index); updateSelected(selected); };
      element("p", "本段原曲试听（包含伴奏、前奏和间奏，可拖动进度条）", inner, "h3lv-audio-label");
      const segmentAudio = element("audio", undefined, inner, "h3lv-segment-audio");
      segmentAudio.controls = true;
      segmentAudio.preload = "metadata";
      segmentAudio.src = api.apiURL(endpoint(`/audio?index=${row.index}&revision=${plan.revision}`));
      let note, rowRefs, renderReferences, materialControls, visualTypeSelect;
      if (plan.materials_version) {
        materialControls = materialEditor(inner, {projectId:plan.id, index:row.index,
          refs:row.refs || [], note:row.material_note || "",
          source:row.reference_source || (row.refs?.length ? "custom" : "default"),
          defaults:() => ({refs:defaultEditor.refs, note:defaultEditor.getNote()}), changed:markDirty});
        note = materialControls.note; rowRefs = materialControls.refs; renderReferences = materialControls.renderRefs;
      } else {
      note = element("textarea", undefined, inner, "h3lv-material-note");
      note.value = row.material_note || "";
      note.placeholder = "本段素材说明，例如：图1是人物，图2是场景。编号要和下面的参考图顺序一致。";
      note.oninput = markDirty;
      const referenceBlock = element("div", undefined, inner, "h3lv-references");
      const referenceHeader = element("div", undefined, referenceBlock, "h3lv-reference-header");
      element("span", "本段参考图", referenceHeader, "h3lv-audio-label");
      const referenceLimit = referenceSlotLimit();
      const referenceList = element("div", undefined, referenceBlock, "h3lv-reference-list");
      rowRefs = Array.isArray(row.refs) ? [...row.refs] : [];
      if (!referenceLimit) {
        element("p", "当前工作流还没有接出 ref_image 槽位：请先在画布上用“加载图像”节点依次接到 "
          + "MiniMax H3 视频参考节点的 ref_image_0、ref_image_1……每接一个槽位，可用的参考图就多一张。",
          referenceBlock, "h3lv-reference-empty");
      }
      renderReferences = () => {
        referenceList.replaceChildren();
        if (!rowRefs.length) {
          element("p", `本段未配置参考图，将使用默认参考图（${defaultReferenceText()}）。`,
            referenceList, "h3lv-reference-empty");
          return;
        }
        rowRefs.forEach((name, position) => {
          const item = element("figure", undefined, referenceList, "h3lv-reference-item");
          const image = document.createElement("img");
          image.src = referencePreviewUrl(plan.id, name);
          image.alt = `第 ${position + 1} 张参考图`;
          image.loading = "lazy";
          item.append(image);
          element("figcaption", `图${position + 1}`, item);
          const tools = element("div", undefined, item, "h3lv-reference-tools");
          const move = step => {
            if (position + step < 0 || position + step >= rowRefs.length) return;
            [rowRefs[position], rowRefs[position + step]] = [rowRefs[position + step], rowRefs[position]];
            markDirty();
            renderReferences();
          };
          const earlier = element("button", "←", tools, "h3lv-reference-tool");
          earlier.type = "button";
          earlier.title = "前移";
          earlier.disabled = position === 0;
          earlier.onclick = () => move(-1);
          const later = element("button", "→", tools, "h3lv-reference-tool");
          later.type = "button";
          later.title = "后移";
          later.disabled = position === rowRefs.length - 1;
          later.onclick = () => move(1);
          const drop = element("button", "移除", tools, "h3lv-reference-tool");
          drop.type = "button";
          drop.onclick = async () => {
            rowRefs.splice(position, 1);
            markDirty();
            renderReferences();
            await removeReferenceImage(plan.id, name);
          };
        });
      };
      const fileInput = document.createElement("input");
      fileInput.type = "file";
      fileInput.accept = "image/*";
      fileInput.hidden = true;
      fileInput.onchange = async () => {
        const file = fileInput.files?.[0];
        fileInput.value = "";
        if (!file) return;
        addButton.disabled = true;
        try {
          rowRefs.push(await uploadReferenceImage(plan.id, row.index, file));
          markDirty();
          renderReferences();
        } catch (error) {
          await messageDialog({title: "参考图上传失败", message: error.message || String(error), tone: "error"});
        } finally {
          addButton.disabled = false;
        }
      };
      referenceHeader.append(fileInput);
      const addButton = actionButton(referenceHeader,
        referenceLimit ? `＋ 上传参考图（上限 ${referenceLimit} 张）` : "＋ 上传参考图", async () => {
          if (!referenceLimit) {
            throw new Error("当前工作流还没有接出 ref_image 槽位。请先在画布上用“加载图像”节点接到 "
              + "MiniMax H3 视频参考节点的 ref_image_0、ref_image_1……");
          }
          if (rowRefs.length >= referenceLimit) throw new Error(`本段最多 ${referenceLimit} 张参考图。`);
          fileInput.click();
        }, "reference-add");
      actionButton(referenceHeader, "套用到所有分段", async () => {
        if (!await confirmDialog({
          title: "把本段的参考图套用到所有分段？",
          message: "所有分段的参考图列表与素材说明都会被本段的内容覆盖，其他分段已有的参考图会被替换。",
          confirmText: "套用到所有分段",
        })) return;
        rows.forEach(item => {
          item.refs.splice(0, item.refs.length, ...rowRefs);
          item.note.value = note.value;
          item.renderRefs();
        });
        markDirty();
      }, "reference-copy");
      renderReferences();
      }
      if (plan.materials_version) {
        const shotControl = element("div", undefined, inner, "h3lv-shot-control");
        const shotLabel = element("label", "本段画面", shotControl);
        visualTypeSelect = element("select", undefined, shotLabel);
        for (const [value, label] of [["performance", "人物表演"], ["environment", "空镜环境"]]) {
          const option = element("option", label, visualTypeSelect);
          option.value = value;
        }
        visualTypeSelect.value = row.visual_type || "performance";
        visualTypeSelect.onchange = markDirty;
        element("span", "保存草稿后会按该类型重写本段镜头简报；空镜不会使用人声参考。",
          shotControl, "h3lv-shot-control-help");
      }
      const promptActions = element("div", undefined, inner, "h3lv-actions h3lv-prompt-actions");
      const prompt = document.createElement("textarea");
      prompt.value = row.prompt;
      prompt.oninput = markDirty;
      actionButton(promptActions, "编辑本段镜头简报（提示词）", async () => {
        const updated = await editPromptDialog(row.index, prompt.value);
        if (updated === null || updated === prompt.value) return;
        prompt.value = updated;
        prompt.dispatchEvent(new Event("input"));
      }, "prompt-edit");
      rows.push({end, prompt, note, materialControls, visualType:visualTypeSelect,
        refs: rowRefs, renderRefs: renderReferences, duration, time,
        generationFrames, editFrames, audio:segmentAudio});
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
    if (!plan.materials_version && !plan.segments.some(row => row.job) && owner.outputs?.slice(5).some(output => output.links?.length)) {
      plan = await request(endpoint("/edit"), {revision:plan.revision, materials:{refs:[], note:""},
        segments:plan.segments.map(row => ({...row, reference_source:row.refs?.length ? "custom" : "default"}))});
    }
    syncDefaultReferenceControl();
    defaultMaterials.replaceChildren();
    const defaultHeader = element("div", undefined, defaultMaterials, "h3lv-default-materials-header");
    element("h3", "项目默认参考图", defaultHeader);
    if (plan.materials_version) {
      element("p", "上传一次，所有使用默认的分段自动继承；本段自定义的图片保持独立。", defaultHeader, "h3lv-help");
      defaultEditor = materialEditor(defaultMaterials, {projectId:plan.id, refs:plan.default_refs || [],
        note:plan.default_material_note || "", changed:() => {markDirty(); rows.forEach(row => row.renderRefs());}});
    } else {
      element("p", "当前沿用画布参考图。启用内置素材后，请上传默认图并核对每段用途。", defaultHeader, "h3lv-help");
      actionButton(defaultMaterials, "启用内置素材管理", async () => {
        if (dirty) throw new Error("请先保存当前修改。");
        await request(endpoint("/edit"), {revision:plan.revision, materials:{refs:[], note:""},
          segments:plan.segments.map(row => ({...row, reference_source:row.refs?.length ? "custom" : "default"}))});
        await load();
      });
    }
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

  async function saveDraft() {
    let revision = plan.revision;
    if (dirty) {
      const saved = await request(endpoint("/edit"), {revision,
        reference_default_count: defaultSelect.value === "all" ? null : Number(defaultSelect.value),
        ...(plan.materials_version ? {materials:{refs:defaultEditor.refs, note:defaultEditor.getNote()}} : {}),
        segments: rows.map(row => ({end: Number(row.end.value), prompt: row.prompt.value,
          material_note: row.materialControls ? row.materialControls.getNote() : row.note.value, refs: row.refs,
          ...(row.materialControls ? {reference_source:row.materialControls.source.value,
            visual_type:row.visualType.value} : {})}))});
      plan = saved; revision = saved.revision; dirty = false;
    }
    return revision;
  }
  actionButton(controls, "保存草稿", async () => {await saveDraft(); await load();});
  actionButton(controls, "保存并确认", async () => {
    if (!await confirmDialog({
      title: "保存并确认分段？",
      message: "请确认已经试听并检查所有切点。保存后，这个项目将允许开始顺序生成。",
      confirmText: "保存并确认",
    })) return;
    const revision = await saveDraft();
    try {await request(endpoint("/approve"), {revision});}
    finally {await load();}
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
  actionButton(controls, "刷新状态", load);
  select.onchange = () => {
    selected = 0;
    load().catch(error => messageDialog({title: "项目加载失败", message: error.message, tone: "error"}));
  };
  await load();
  const timer = setInterval(async () => {
    if (!shade.isConnected) { clearInterval(timer); canvas.disposeTimeline?.(); return; }
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
      api.addEventListener("h3lv-model-download", event => {
        const data = event.detail || {};
        const totalMiB = Math.round((Number(data.total) || 0) / 1024 / 1024);
        if (data.state === "started") {
          toast("正在下载人声分离模型", `首次使用约需下载 ${totalMiB} MiB，完成后会自动继续分析。`);
        } else if (data.state === "resuming") {
          const doneMiB = Math.round((Number(data.downloaded) || 0) / 1024 / 1024);
          toast("正在续传人声分离模型", `已下载 ${doneMiB}/${totalMiB} MiB，完成后会自动继续分析。`);
        } else if (data.state === "completed") {
          toast("人声分离模型已就绪", "正在继续音频分析。", "success");
        }
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
    const oldConfigured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (info) {
      const values = info?.widgets_values;
      const oldActivities = new Set(["auto", "moderate", "dynamic"]);
      const oldFramings = new Set(["medium close-up", "medium shot", "full shot", "close-up"]);
      if (Array.isArray(values) && oldActivities.has(values[6]) && oldFramings.has(values[7])) {
        values.splice(6, 2);
        const names = ["mode", "max_seconds", "target_seconds", "asr_python", "asr_model",
          "asr_device", "director_mode", "project_id", "segment_index"];
        names.forEach((name, index) => {
          const widget = this.widgets?.find(item => item.name === name);
          if (widget) widget.value = values[index];
        });
      }
      return oldConfigured?.call(this, info);
    };
    const oldCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = oldCreated?.apply(this, arguments);
      const directorLabels = {
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
