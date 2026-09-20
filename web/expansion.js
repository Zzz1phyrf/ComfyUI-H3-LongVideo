import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

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
  const close = element("button", header, "关闭"); close.onclick = () => shade.remove();
  const main = element("main", panel); main.className = "h3lv-content";
  const status = element("p", main); status.setAttribute("role", "status");
  return {main, status};
}
function button(parent, text, status, action) {
  const b = element("button", parent, text); b.className = "h3lv-button";
  b.onclick = async () => {b.disabled = true; status.textContent = "处理中…";
    try {await action();} catch(error) {status.textContent = error.message;}
    finally {b.disabled = false;}};
  return b;
}
const widget = (node, name) => node.widgets?.find(item => item.name === name);
const options = node => ({mode:widget(node,"mode").value, model:widget(node,"model").value,
  rule:widget(node,"rule").value, expansion_revision:Number(widget(node,"revision").value)});

async function settingsDialog(node) {
  const {main, status} = dialog("H3 分镜提示词生成器 · API 设置");
  const profile = await request("/h3lv/expansion/settings");
  element("p", main, "配置仅保存在本机，不随工作流导出。更换服务后请填写对应密钥。");
  const addressLabel = element("label", main, "API 基础地址");
  const address = element("input", addressLabel); address.value = profile.base_url; address.type = "url";
  const keyLabel = element("label", main, "API Key");
  const key = element("input", keyLabel); key.type = "password"; key.autocomplete = "off";
  key.placeholder = profile.configured ? "已配置，留空保留当前密钥" : "粘贴密钥";
  button(main, "保存本机配置", status, async () => {
    await request("/h3lv/expansion/settings", {base_url:address.value, api_key:key.value});
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
async function previewDialog(node) {
  const {main, status} = dialog("H3 分镜提示词生成器 · 分段预览与编辑");
  element("p", main, "先在长视频节点保存素材与镜头简报。当前模型、扩写规则和分段内容完全一致时自动读取缓存，否则调用 API 重新生成。修改后保存会要求重新确认该项目。");
  const projects = element("select", main);
  const list = await request("/h3lv/projects");
  for (const p of list) element("option", projects, `${p.mode} · ${p.count}段 · ${p.id.slice(0,8)}`).value = p.id;
  const link = app.graph.links[node.inputs?.find(input => input.name === "material")?.link];
  const source = link && app.graph.getNodeById(link.origin_id);
  const current = source && (source.properties?.h3lv_project || widget(source,"project_id")?.value);
  if (list.some(p => p.id === current)) projects.value = current;
  const segments = element("select", main);
  const description = element("p", main);
  const text = element("textarea", main); text.rows = 18;
  let plan, previewKey, previewOptions;
  async function load() {
    if (!projects.value) {status.textContent = "还没有分析项目。"; return;}
    plan = await request(`/h3lv/project/${projects.value}`); segments.replaceChildren();
    plan.segments.forEach(row => element("option", segments, `第 ${row.index+1} 段 · ${row.start.toFixed(2)}–${row.end.toFixed(2)}s`).value = row.index);
    update();
  }
  function update() {
    const row = plan.segments[Number(segments.value)];
    const inherited = (row.reference_source || (row.refs?.length ? "custom" : "default")) === "default";
    description.textContent = `${row.visual_type === "environment" ? "空镜" : "人物表演"} · ${(inherited ? plan.default_refs : row.refs)?.length || 0} 张图 · ${inherited ? plan.default_material_note || "" : row.material_note || ""}`;
    text.value = row.expanded_prompt || ""; previewKey = null; status.textContent = "";
  }
  projects.onchange = () => load().catch(error => status.textContent = error.message);
  segments.onchange = update;
  button(main, "生成提示词（自动复用缓存）", status, async () => {
    if (!plan?.materials_version) throw new Error("请先在长视频面板启用内置素材管理。");
    previewOptions = options(node);
    const result = await request(`/h3lv/project/${plan.id}/expand`, {revision:plan.revision, index:Number(segments.value), ...previewOptions});
    text.value = result.text; previewKey = result.key;
    status.textContent = result.source === "cache" ? "已读取匹配缓存，可以编辑后保存。" :
      (result.source === "saved" ? "已读取本段保存过的提示词。" :
      (result.source === "manual" ? "已读取本段导演简报。" : "已通过 API 生成并写入缓存，可以编辑后保存。"));
  });
  button(main, "保存本段提示词", status, async () => {
    if (!previewKey) throw new Error("请先扩写或读取当前设置的缓存。");
    if (JSON.stringify(options(node)) !== JSON.stringify(previewOptions)) throw new Error("节点设置已变化，请重新扩写。");
    const result = await request(`/h3lv/project/${plan.id}/expand`, {revision:plan.revision, index:Number(segments.value), ...previewOptions, key:previewKey, text:text.value});
    plan.revision = result.revision; status.textContent = "已保存。请在长视频面板刷新并确认后生成。";
  });
  await load();
}
app.registerExtension({name:"H3LV.PromptExpansion", async beforeRegisterNodeDef(nodeType, nodeData) {
  if (nodeData.name !== "H3LVPromptExpand") return;
  const original = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function() {
    const result = original?.apply(this, arguments);
    const ruleWidget = widget(this, "rule");
    if (ruleWidget) {
      ruleWidget.label = "扩写规则（指导模型如何反推 H3 提示词）";
      ruleWidget.options = {...ruleWidget.options,
        tooltip:"这不是本段最终提示词；它会与内置 H3 规则、导演简报、画面类型、声音关系和参考图一起发给模型，指导模型如何组织结果。"};
    }
    this.addWidget("button", "API 设置与模型选择", null, () => settingsDialog(this).catch(error => window.alert(error.message))).serialize = false;
    this.addWidget("button", "分段扩写预览 / 编辑", null, () => previewDialog(this).catch(error => window.alert(error.message))).serialize = false;
    this.size[0] = Math.max(this.size[0], 380);
    return result;
  };
}});
