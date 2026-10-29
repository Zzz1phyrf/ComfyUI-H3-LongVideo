import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { reportUiError, withUiLogging } from "./diagnostics.js";

async function request(path, body) {
  const response = await api.fetchApi(path, body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "请求失败");
  return result;
}
function element(tag, parent, text) {
  const item = document.createElement(tag); if (text !== undefined) item.textContent = text;
  parent.append(item); return item;
}
function dialog(title) {
  const shade = element("div", document.body); shade.className = "h3lv-shade";
  const panel = element("div", shade); panel.className = "h3lv-panel h3lv-expansion-panel";
  const header = element("header", panel); header.className = "h3lv-header";
  element("h2", header, title);
  let beforeClose = () => true, dispose = () => {};
  const close = element("button", header, "关闭"); close.onclick = () => {
    if (!beforeClose()) return;
    dispose(); shade.remove();
  };
  const main = element("main", panel); main.className = "h3lv-content";
  const status = element("p", main); status.setAttribute("role", "status");
  return {main, status, onClose(check, cleanup) {beforeClose = check; dispose = cleanup || (() => {});}};
}
function button(parent, text, status, action) {
  const b = element("button", parent, text); b.className = "h3lv-button";
  b.onclick = async () => {b.disabled = true; status.textContent = "处理中…";
    try {await action();} catch(error) {
      void reportUiError(error, {source:"expansion", action:text});
      status.textContent = error.message;}
    finally {b.disabled = false;}};
  return b;
}
const widget = (node, name) => node.widgets?.find(item => item.name === name);
const options = node => ({mode:widget(node,"mode").value, model:widget(node,"model").value,
  rule:widget(node,"rule").value, expansion_revision:Number(widget(node,"revision").value),
  provider:widget(node,"provider")?.value || "api", codex_model:widget(node,"codex_model")?.value || "",
  codex_reasoning:widget(node,"codex_reasoning")?.value || "default",
  codex_timeout:Number(widget(node,"codex_timeout")?.value || 300)});

async function settingsDialog(node) {
  const {main, status} = dialog("MiniMax H3 · MV Prompt Expander — API 设置");
  const profile = await request("/h3lv/expansion/settings");
  element("p", main, "配置仅保存在本机，不随工作流导出。更换服务后请填写对应密钥。");
  const addressLabel = element("label", main, "API 基础地址");
  const address = element("input", addressLabel); address.value = profile.base_url; address.type = "url";
  const keyLabel = element("label", main, "API Key");
  const key = element("input", keyLabel); key.type = "password"; key.autocomplete = "off";
  key.placeholder = profile.configured ? "已配置，留空保留当前密钥" : "粘贴密钥";
  const timeoutLabel = element("label", main, "连接／读取等待超时（秒）");
  const timeout = element("input", timeoutLabel); timeout.type = "number"; timeout.min = "10"; timeout.max = "3600";
  timeout.value = profile.timeout_seconds ?? 300;
  const responseLabel = element("label", main, "响应方式");
  const responseMode = element("select", responseLabel);
  element("option", responseMode, "普通 JSON（兼容优先）").value = "json";
  element("option", responseMode, "流式 SSE（服务需支持 stream）").value = "stream";
  responseMode.value = profile.response_mode || "json";
  const thinkingLabel = element("label", main, "思考参数");
  const thinking = element("select", thinkingLabel);
  element("option", thinking, "自动适配已知接口／型号，未知服务不注入").value = "auto";
  element("option", thinking, "遵循服务默认（不自动注入思考参数）").value = "provider_default";
  thinking.value = profile.thinking_mode || "auto";
  const advanced = element("details", main);
  element("summary", advanced, "额外请求参数（高级，JSON 对象）");
  element("p", advanced, "按服务文档填写，例如 {\"enable_thinking\":false}。覆盖自动参数；null 可移除字段。不能改写模型、素材、stream 或密钥。仅保存在本机。");
  const extra = element("textarea", advanced); extra.rows = 5; extra.value = JSON.stringify(profile.extra_body || {}, null, 2);
  extra.setAttribute("aria-label", "额外请求参数");
  element("p", main, "不自动重试：超时请求可能已产生费用。支持标准 /chat/completions 的服务；多图扩写还需模型支持图片输入。");
  button(main, "保存本机配置", status, async () => {
    let extraBody;
    try {extraBody = JSON.parse(extra.value);} catch {throw new Error("额外请求参数不是有效 JSON，请修正或填写 {}。");}
    await request("/h3lv/expansion/settings", {base_url:address.value, api_key:key.value,
      timeout_seconds:Number(timeout.value), response_mode:responseMode.value,
      thinking_mode:thinking.value, extra_body:extraBody});
    key.value = ""; status.textContent = "配置已保存。";
  });
  const search = element("input", main); search.placeholder = "筛选模型名称，例如 qwen";
  const models = element("select", main); models.size = 9;
  let names = [];
  const render = () => {models.replaceChildren(); names.filter(name => name.toLowerCase().includes(search.value.toLowerCase())).forEach(name => element("option", models, name).value = name);};
  search.oninput = render;
  button(main, "读取模型列表", status, async () => {names = (await request("/h3lv/expansion/models")).models; render(); status.textContent = `读取到 ${names.length} 个模型；多图扩写需选择支持视觉的模型。`;});
  button(main, "使用所选模型", status, async () => {if (!models.value) throw new Error("请先选择模型。"); widget(node,"model").value = models.value; node.setDirtyCanvas(true); status.textContent = `已选择 ${models.value}`;});
}
const batchSuccess = item => ["succeeded", "cached"].includes(item.status) && Boolean(item.text);
const batchActive = job => ["pending", "running", "cancelling"].includes(job?.state);
function batchApplyBody(plan, job, selected, edits) {
  const indices = job.items.filter(item => selected.has(item.index) && batchSuccess(item)).map(item => item.index);
  if (!indices.length) throw new Error("请勾选要保存的成功结果。");
  return {revision:plan.revision, indices, edits:Object.fromEntries(indices.filter(index => edits.has(index)).map(index => [index, edits.get(index)]))};
}
async function previewDialog(node) {
  const view = dialog("MiniMax H3 · MV Prompt Expander — 分段预览与批量扩写"), {main, status} = view;
  element("p", main, "先在MV Generator 面板保存素材与导演简报。批次结果先供预览，保存后需重新确认项目。关闭此面板后后台继续；取消只影响当前批次，已发出的 API 请求可能仍产生费用。");
  element("p", main, "单段与批量扩写均按 H3 规则生成中文正文，六个章节名及标签保留原格式。失败结果不会自动应用，成功候选请预览后保存。");
  const projects = element("select", main); projects.setAttribute("aria-label", "分析项目");
  const list = await request("/h3lv/projects");
  for (const p of list) element("option", projects, `${p.mode} · ${p.count}段 · ${p.id.slice(0,8)}`).value = p.id;
  const link = app.graph.links[node.inputs?.find(input => input.name === "material")?.link];
  const source = link && app.graph.getNodeById(link.origin_id);
  const current = source && (source.properties?.h3lv_project || widget(source,"project_id")?.value);
  if (list.some(p => p.id === current)) projects.value = current;
  let plan, previewKey, previewOptions, singleDirty = false, alive = true, epoch = 0, singleEpoch = 0, timer;
  let job = null, selectedJobId = "", batchSignature = "", edits = new Map(), selected = new Set(), segmentSelection = new Set();
  const segments = element("select", main); segments.setAttribute("aria-label", "单段预览");
  const description = element("p", main), text = element("textarea", main); text.rows = 12; text.setAttribute("aria-label", "本段提示词");
  text.oninput = () => {singleDirty = true; singleEpoch++;};
  const singleActions = element("div", main); singleActions.className = "h3lv-actions";
  const batch = element("section", main); batch.className = "h3lv-batch";
  element("h3", batch, "批量扩写");
  element("p", batch, "默认补齐未生成片段并保护已保存的人工提示词。手写直通提示词始终保留，勾选重新生成也不会覆盖。有效缓存会复用；批量按段请求模型，并不会减少每段服务用量。");
  const targets = element("div", batch); targets.className = "h3lv-batch-targets";
  const controls = element("div", batch); controls.className = "h3lv-actions h3lv-batch-controls";
  button(controls, "全部分段", status, async () => {segmentSelection = new Set(plan.segments.map(row => row.index)); renderTargets(); status.textContent = "已选择全部分段。";});
  button(controls, "清空选择", status, async () => {segmentSelection.clear(); renderTargets(); status.textContent = "请选择分段。";});
  const forceLabel = element("label", controls), force = element("input", forceLabel); force.type = "checkbox"; element("span", forceLabel, "重新生成已有扩写结果（保留原文）");
  const concurrencyLabel = element("label", controls, "并发"), concurrency = element("select", concurrencyLabel);
  for (const value of [1, 2, 3, 4]) element("option", concurrency, String(value)).value = value; concurrency.value = "2";
  const jobs = element("select", batch); jobs.setAttribute("aria-label", "历史批次");
  const progress = element("p", batch); progress.setAttribute("role", "status");
  const resultActions = element("div", batch); resultActions.className = "h3lv-actions";
  const results = element("div", batch); results.className = "h3lv-batch-results";
  const stateLabels = {pending:"待处理", running:"运行中", cached:"缓存复用", succeeded:"已完成", protected:"人工保留", failed:"失败", cancelled:"已取消", conflict:"输入冲突", interrupted:"已中断", cancelling:"取消中", completed:"已结束", paused:"已暂停", succeeded:"已完成"};
  const sourceLabels = {api:"API", codex_cli:"Codex CLI", cache:"缓存", saved:"已保存", manual:"导演简报"};
  const unsaved = () => singleDirty || edits.size > 0;
  function allowDiscard() {return !unsaved() || window.confirm("提示词还有未保存的编辑。确认放弃这些编辑？后台批次会继续运行。");}
  function stopPolling() {clearTimeout(timer); timer = null;}
  view.onClose(allowDiscard, () => {alive = false; epoch++; stopPolling();});
  function renderTargets() {
    targets.replaceChildren();
    for (const row of plan.segments) {
      const label = element("label", targets), check = element("input", label); check.type = "checkbox"; check.checked = segmentSelection.has(row.index);
      check.onchange = () => {if (check.checked) segmentSelection.add(row.index); else segmentSelection.delete(row.index);};
      const inherited = (row.reference_source || (row.refs?.length ? "custom" : "default")) === "default";
      const refs = inherited ? plan.default_refs : row.refs;
      const note = inherited ? plan.default_material_note : row.material_note;
      element("span", label, `第 ${row.index+1} 段 · ${row.start.toFixed(2)}–${row.end.toFixed(2)}s · ${row.visual_type === "environment" ? "空镜" : "人物"} · ${refs?.length || 0} 张${inherited ? "默认图" : "自定义图"} · ${row.expanded_prompt ? "已有提示词" : "待补齐"}`);
      label.title = note || "暂无素材说明";
    }
  }
  function updateSingle() {
    const row = plan?.segments.find(item => item.index === Number(segments.value)); if (!row) return;
    const inherited = (row.reference_source || (row.refs?.length ? "custom" : "default")) === "default";
    description.textContent = `${row.visual_type === "environment" ? "空镜" : "人物表演"} · ${(inherited ? plan.default_refs : row.refs)?.length || 0} 张图 · ${inherited ? plan.default_material_note || "" : row.material_note || ""}`;
    singleEpoch++; text.value = row.expanded_prompt || ""; singleDirty = false; previewKey = null;
  }
  function showJob(next) {
    job = next;
    const counts = new Map(); next.items.forEach(item => counts.set(item.status, (counts.get(item.status) || 0)+1));
    progress.textContent = `${stateLabels[next.state] || next.state} · ${[...counts].map(([key, count]) => `${stateLabels[key] || key} ${count}`).join(" · ")} · ${sourceLabels[next.settings?.provider] || "API"}`;
    const signature = JSON.stringify(next.items.map(item => [item.index,item.status,item.text,item.error,item.source]));
    if (signature === batchSignature) return; batchSignature = signature;
    results.replaceChildren();
    for (const item of next.items) {
      const card = element("article", results); card.className = "h3lv-batch-item";
      const label = element("label", card), check = element("input", label); check.type = "checkbox"; check.checked = selected.has(item.index);
      check.onchange = () => {if (check.checked) selected.add(item.index); else selected.delete(item.index);};
      element("span", label, `第 ${item.index+1} 段 · ${stateLabels[item.status] || item.status}${item.source ? ` · ${sourceLabels[item.source] || item.source}` : ""}`);
      if (item.error) element("p", card, String(item.error));
      const original = plan.segments.find(row => row.index === item.index)?.expanded_prompt;
      if (original) {const old = element("details", card); element("summary", old, "已保存原文（保留供对比）"); element("pre", old, original);}
      if (item.text) {
        const candidate = element("textarea", card); candidate.rows = 7; candidate.value = edits.get(item.index) ?? item.text; candidate.disabled = !batchSuccess(item);
        candidate.setAttribute("aria-label", `第 ${item.index+1} 段批次候选`);
        candidate.oninput = () => {if (candidate.value === item.text) edits.delete(item.index); else edits.set(item.index, candidate.value);};
      }
    }
  }
  async function poll() {
    stopPolling(); if (!alive || !selectedJobId || !plan) return;
    const pid = plan.id, jid = selectedJobId, token = epoch;
    try {
      const next = await request(`/h3lv/project/${pid}/expansion-batches/${jid}`);
      if (!alive || epoch !== token || plan.id !== pid || selectedJobId !== jid) return;
      showJob(next);
      if (batchActive(next)) timer = setTimeout(poll, 1500);
    } catch (error) {if (alive && epoch === token) {progress.textContent = `${error.message}；可点击刷新批次重试。`;}}
  }
  async function listJobs(preferred = "") {
    const pid = plan.id, token = epoch, data = await request(`/h3lv/project/${pid}/expansion-batches`);
    if (!alive || token !== epoch || plan.id !== pid) return;
    jobs.replaceChildren(); element("option", jobs, "选择历史批次").value = "";
    for (const item of data.jobs) element("option", jobs, `${item.id.slice(0,12)} · ${stateLabels[item.state] || item.state}`).value = item.id;
    jobs.value = preferred; selectedJobId = jobs.value;
    if (selectedJobId) await poll();
  }
  async function load() {
    stopPolling(); const token = ++epoch, pid = projects.value;
    if (!pid) {status.textContent = "还没有分析项目。"; return;}
    const next = await request(`/h3lv/project/${pid}`); if (!alive || token !== epoch) return;
    plan = next; segments.replaceChildren();
    plan.segments.forEach(row => element("option", segments, `第 ${row.index+1} 段 · ${row.start.toFixed(2)}–${row.end.toFixed(2)}s`).value = row.index);
    previousSegment = String(plan.segments[0]?.index ?? "0"); segmentSelection = new Set(plan.segments.map(row => row.index)); job = null; selectedJobId = ""; edits.clear(); selected.clear(); batchSignature = ""; results.replaceChildren(); progress.textContent = "";
    renderTargets(); updateSingle(); await listJobs();
  }
  projects.onchange = () => {
    if (!allowDiscard()) {projects.value = plan?.id || ""; return;}
    void load().catch(error => status.textContent = error.message);
  };
  let previousSegment = "0";
  segments.onchange = () => {if (singleDirty && !window.confirm("放弃本段未保存的提示词编辑？")) {segments.value = previousSegment; return;} previousSegment = segments.value; updateSingle();};
  jobs.onchange = () => {
    if (edits.size && !window.confirm("放弃当前批次未保存的编辑并切换批次？")) {jobs.value = selectedJobId; return;}
    selectedJobId = jobs.value; edits.clear(); selected.clear(); batchSignature = ""; results.replaceChildren(); job = null;
    void poll();
  };
  button(singleActions, "生成提示词（自动复用缓存）", status, async () => {
    if (!plan?.materials_version) throw new Error("请先在MV Generator 面板启用内置素材管理。");
    if (singleDirty && !window.confirm("放弃未保存的编辑并生成本段候选？")) return;
    const pid = plan.id, index = Number(segments.value), token = epoch, singleToken = ++singleEpoch, settings = options(node);
    const result = await request(`/h3lv/project/${pid}/expand`, {revision:plan.revision, index, ...settings});
    if (!alive || token !== epoch || singleToken !== singleEpoch || plan.id !== pid || Number(segments.value) !== index) return;
    text.value = result.text; previewKey = result.key; previewOptions = settings; singleDirty = true;
    status.textContent = `来源：${sourceLabels[result.source] || result.source}；可编辑后保存。`;
  });
  button(singleActions, "保存本段提示词", status, async () => {
    if (!previewKey) throw new Error("请先扩写或读取当前设置的缓存。");
    if (JSON.stringify(options(node)) !== JSON.stringify(previewOptions)) throw new Error("节点设置已变化，请重新扩写。");
    const pid = plan.id, index = Number(segments.value), token = epoch, submittedText = text.value, singleToken = singleEpoch;
    const result = await request(`/h3lv/project/${pid}/expand`, {revision:plan.revision, index, ...previewOptions, key:previewKey, text:submittedText});
    if (!alive || token !== epoch || plan.id !== pid) return;
    plan.revision = result.revision; plan.segments.find(row => row.index === index).expanded_prompt = submittedText; if (singleToken === singleEpoch && text.value === submittedText) singleDirty = false;
    status.textContent = "已保存。请在MV Generator 面板刷新并确认后生成。";
  });
  async function adoptJob(next, token = epoch) {
    if (!alive || token !== epoch || next.project_id !== plan?.id) return;
    edits.clear(); selected = new Set(next.items.filter(batchSuccess).map(item => item.index)); batchSignature = "";
    selectedJobId = next.id; await listJobs(next.id); status.textContent = "批次已创建，关闭面板后后台继续。";
  }
  button(controls, "启动批量扩写", status, async () => {
    if (!plan?.materials_version) throw new Error("请先启用内置素材管理并保存素材。");
    if (!segmentSelection.size) throw new Error("请先选择分段。");
    if (unsaved()) throw new Error("请先保存编辑，或刷新面板并确认放弃编辑。");
    if (force.checked && !window.confirm("为选中分段重新生成已有扩写结果？手写直通提示词始终保留；已保存扩写原文保留，只有明确保存选中结果后才会替换。每段请求可能产生用量。")) return;
    const pid = plan.id, token = epoch, next = await request(`/h3lv/project/${pid}/expansion-batches`, {revision:plan.revision, indices:[...segmentSelection], force:Boolean(force.checked), concurrency:Number(concurrency.value), ...options(node)});
    await adoptJob(next, token);
  });
  button(resultActions, "刷新项目并重新校验", status, async () => {
    if (!plan) return;
    if (singleDirty) throw new Error("请先保存本段编辑。");
    const pid = plan.id, token = epoch, next = await request(`/h3lv/project/${plan.id}`);
    if (!alive || token !== epoch || plan.id !== pid) return;
    plan = next; updateSingle(); renderTargets(); batchSignature = ""; if (job) showJob(job);
    status.textContent = "项目已重新读取；批次候选和编辑仍保留，保存时会重新校验素材与简报。";
  });
  button(resultActions, "刷新批次", status, async () => {await listJobs(selectedJobId); status.textContent = "批次已刷新。";});
  button(resultActions, "取消当前批次", status, async () => {
    if (!job || !batchActive(job)) throw new Error("当前没有运行中的批次。");
    await request(`/h3lv/project/${plan.id}/expansion-batches/${job.id}/cancel`, {}); await poll(); status.textContent = "已请求取消当前批次。";
  });
  button(resultActions, "选中成功结果", status, async () => {if (!job) return; job.items.filter(batchSuccess).forEach(item => selected.add(item.index)); batchSignature = ""; showJob(job); status.textContent = "已选中成功结果。";});
  async function retry(onlyFailed) {
    if (!job) throw new Error("请先选择批次。");
    if (unsaved()) throw new Error("请先保存当前编辑。");
    if (batchActive(job)) throw new Error("请等待当前批次结束或取消后再续跑。");
    const indices = job.items.filter(item => onlyFailed ? item.status === "failed" && selected.has(item.index) : ["pending", "running", "failed", "cancelled", "interrupted", "conflict"].includes(item.status)).map(item => item.index);
    if (!indices.length) throw new Error(onlyFailed ? "请勾选失败段。" : "没有需要续跑的分段。");
    if (!window.confirm("超时或中断的请求可能已被服务处理。确认创建新批次重试这些分段？")) return;
    const token = epoch;
    await adoptJob(await request(`/h3lv/project/${plan.id}/expansion-batches/${job.id}/retry`, {revision:plan.revision, indices, concurrency:Number(concurrency.value)}), token);
  }
  button(resultActions, "续跑未完成段", status, () => retry(false));
  button(resultActions, "仅重试选中失败段", status, () => retry(true));
  button(resultActions, "保存选中成功结果", status, async () => {
    if (!job) throw new Error("请先选择批次。");
    if (singleDirty) throw new Error("请先保存本段编辑。");
    const pid = plan.id, jid = job.id, token = epoch;
    const body = {...options(node), ...batchApplyBody(plan, job, selected, edits)};
    const result = await request(`/h3lv/project/${pid}/expansion-batches/${jid}/apply`, body);
    if (!alive || epoch !== token || plan.id !== pid || selectedJobId !== jid) return;
    const next = result.plan || await request(`/h3lv/project/${pid}`);
    if (!alive || epoch !== token || plan.id !== pid) return;
    plan = next; for (const [index, submitted] of Object.entries(body.edits)) {if (edits.get(Number(index)) === submitted) edits.delete(Number(index));}
    if (!singleDirty) updateSingle(); renderTargets(); status.textContent = `已一次保存 ${result.changed.length} 段变更。请在MV Generator 面板刷新并确认后生成。`;
  });
  await load();
}
app.registerExtension({name:"H3LV.PromptExpansion", async beforeRegisterNodeDef(nodeType, nodeData) {
  if (nodeData.name !== "H3LVPromptExpand") return;
  const original = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function() {
    const result = original?.apply(this, arguments);
    for (const [name, label, tooltip] of [
      ["provider", "生成服务", "api 使用下方 API 配置；codex_cli 使用当前 Windows 用户已登录的 Codex CLI。"],
      ["model", "API 模型", "仅 api 方式使用。"],
      ["codex_model", "Codex 模型（留空沿用 CLI）", "仅 codex_cli 方式使用，不会覆盖本机 Codex 配置。"],
      ["codex_reasoning", "Codex 推理档位", "default 沿用 CLI 配置；可选择 low / medium / high / xhigh。"],
      ["codex_timeout", "Codex 等待超时（秒）", "超时或取消会停止本次 Codex 会话及子进程，不自动重试。"],
    ]) {
      const item = widget(this, name);
      if (item) {item.label = label; item.options = {...item.options, tooltip};}
    }
    const ruleWidget = widget(this, "rule");
    if (ruleWidget) {
      ruleWidget.label = "扩写规则（指导模型如何反推 H3 提示词）";
      ruleWidget.options = {...ruleWidget.options,
        tooltip:"这不是本段最终提示词；它会与内置 H3 规则、导演简报、画面类型、声音关系和参考图一起发给模型，指导模型如何组织结果。正文使用中文，固定六章节及标签保留原格式。"};
    }
    this.addWidget("button", "API 设置与模型选择", null, () => withUiLogging(() => settingsDialog(this),
      {source:"expansion", action:"API 设置", node_id:this.id})().catch(error => window.alert(error.message))).serialize = false;
    this.addWidget("button", "分段扩写预览 / 编辑", null, () => withUiLogging(() => previewDialog(this),
      {source:"expansion", action:"扩写预览", node_id:this.id})().catch(error => window.alert(error.message))).serialize = false;
    this.size[0] = Math.max(this.size[0], 380);
    return result;
  };
}});
