import { api } from "../../scripts/api.js";
import { withUiLogging } from "./diagnostics.js";
import { mentionedImageNumbers, remapImageMentions } from "./material_mentions.js";

// Keep selection insertion order, including when a filter hides selected cards.
export function toggleLibrarySelection(selection, asset) {
  const index = selection.findIndex(item => item.asset_id === asset.id);
  if (index >= 0) selection.splice(index, 1);
  else selection.push({asset_id:asset.id, version:asset.version});
  return selection;
}
export function mergeLibraryImport(before, oldNote, imported, {mode = "append", noteMode = "append"} = {}) {
  const refs = mode === "replace" ? [...imported.refs] : [...new Set([...before, ...imported.refs])];
  if (refs.length > 6) throw new Error("参考图合计最多 6 张，请减少选择或使用替换。");
  if (noteMode !== "replace") {
    for (const number of mentionedImageNumbers(oldNote)) {
      if (!refs.includes(before[number - 1])) throw new Error("原说明仍引用被替换的图片。请先修改原说明，或明确选择替换说明。");
    }
  }
  const existing = noteMode === "replace" ? "" : remapImageMentions(oldNote, before, refs);
  const descriptions = noteMode === "keep" ? [] : imported.refs.map(name => {
    const origin = imported.origins?.[name] || {};
    const description = String(origin.description || "").replace(/@图\d+|<Picture\s+\d+>/gi, "参考图");
    const title = String(origin.name_at_import || "素材").replace(/@图\d+|<Picture\s+\d+>/gi, "参考图");
    return `@图${refs.indexOf(name)+1}：${title}${description ? `；${description}` : ""}`;
  });
  return {refs, note:[existing, ...descriptions].filter(Boolean).join("\n")};
}
async function request(path, body) {
  const init = body instanceof FormData ? {method:"POST", body} : body === undefined ? {} :
    {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)};
  const response = await api.fetchApi(path, init), result = await response.json();
  if (!response.ok) throw new Error(result.error || "素材库请求失败");
  return result;
}
function el(tag, parent, text) {
  const item = document.createElement(tag); if (text !== undefined) item.textContent = text;
  parent.append(item); return item;
}
function select(parent, label, values) {
  const wrapper = el("label", parent, label), input = el("select", wrapper);
  values.forEach(([value, text]) => {el("option", input, text).value = value;}); return input;
}
export async function openAssetLibrary({projectId, getRevision, onImport, currentCount = 0} = {}) {
  const shade = el("div", document.body); shade.className = "h3lv-shade h3lv-library-shade";
  const panel = el("section", shade); panel.className = "h3lv-panel h3lv-library-panel";
  panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "true"); panel.setAttribute("aria-label", "跨项目素材库");
  const header = el("header", panel); header.className = "h3lv-header";
  el("h2", header, "跨项目素材库 · 角色与场景");
  const main = el("main", panel); main.className = "h3lv-content";
  const status = el("p", main); status.setAttribute("role", "status");
  let alive = true, generation = 0, revision = 0, selected = [], busy = false;
  function close() {if (busy) return; alive = false; generation++; shade.remove();}
  function action(parent, text, callback) {
    const b = el("button", parent, text); b.type = "button"; b.className = "h3lv-button";
    b.onclick = withUiLogging(async () => {
      if (busy) return; busy = true; b.disabled = true;
      try {await callback();} catch (error) {status.textContent = `${error.message}。若素材库已在其他窗口变化，请点击搜索／刷新后重试。`; throw error;}
      finally {busy = false; b.disabled = false;}
    }, {source:"asset_library", action:text});
    const run = b.onclick; b.onclick = () => run().catch(() => {}); return b;
  }
  const closeButton = el("button", header, "关闭"); closeButton.onclick = close;
  const tools = el("div", main); tools.className = "h3lv-actions h3lv-library-tools";
  const category = select(tools, "分类", [["", "全部"], ["character", "角色"], ["scene", "场景"]]);
  const searchLabel = el("label", tools, "搜索名称／标签"), search = el("input", searchLabel); search.type = "search";
  const trash = select(tools, "显示", [["", "素材库"], ["1", "回收站"]]);
  const chosen = el("p", main), cards = el("div", main); cards.className = "h3lv-library-grid";
  function summary() {chosen.textContent = `已选 ${selected.length} 张（按点击顺序导入）${currentCount ? ` · 当前 ${currentCount} 张` : ""}`;}
  async function load() {
    const token = ++generation;
    const query = new URLSearchParams({category:category.value, q:search.value, deleted:trash.value});
    const data = await request(`/h3lv/library?${query}`);
    if (!alive || token !== generation) return;
    revision = data.revision; cards.replaceChildren(); summary();
    for (const asset of data.assets) {
      const card = el("article", cards); card.className = "h3lv-library-card";
      const image = el("img", card); image.loading = "lazy"; image.alt = asset.name;
      image.src = api.apiURL(`/h3lv/library/${encodeURIComponent(asset.id)}/image?version=${asset.version}&thumbnail=1`);
      el("strong", card, asset.name); el("span", card, `${asset.category === "scene" ? "场景" : "角色"} · v${asset.version}`);
      el("p", card, asset.description || "暂无说明"); el("small", card, (asset.tags || []).join(" · "));
      const controls = el("div", card); controls.className = "h3lv-actions";
      if (onImport && !asset.deleted_at) {
        const pick = el("button", controls); pick.type = "button"; pick.className = "h3lv-button";
        const update = () => {const at = selected.findIndex(item => item.asset_id === asset.id); pick.textContent = at < 0 ? "选择" : `已选 ${at+1} · v${selected[at].version} · 取消`; pick.setAttribute("aria-pressed", String(at >= 0));};
        update(); pick.onclick = () => {if (busy) return; if (selected.length >= 6 && !selected.some(item => item.asset_id === asset.id)) {status.textContent = "每次最多选择 6 张。"; return;} toggleLibrarySelection(selected, asset); void load().catch(error => status.textContent = error.message);};
      }
      action(controls, asset.deleted_at ? "恢复" : "移入回收站", async () => {
        if (!asset.deleted_at && !window.confirm(`将“${asset.name}”移入回收站？项目中已有的图片仍可使用。`)) return;
        await request(`/h3lv/library/${encodeURIComponent(asset.id)}/${asset.deleted_at ? "restore" : "delete"}`, {revision});
        if (!asset.deleted_at) selected = selected.filter(item => item.asset_id !== asset.id); await load();
      });
      if (!asset.deleted_at) {
        const edit = el("details", card); el("summary", edit, "编辑名称、说明与图片版本");
        const nameLabel = el("label", edit, "名称"), name = el("input", nameLabel); name.value = asset.name;
        const kind = select(edit, "分类", [["character", "角色"], ["scene", "场景"]]); kind.value = asset.category;
        const descLabel = el("label", edit, "用途说明"), desc = el("textarea", descLabel); desc.value = asset.description || "";
        const tagsLabel = el("label", edit, "标签（逗号分隔）"), tags = el("input", tagsLabel); tags.value = (asset.tags || []).join(", ");
        action(edit, "保存信息", async () => {await request(`/h3lv/library/${encodeURIComponent(asset.id)}/edit`, {revision, name:name.value, category:kind.value, description:desc.value, tags:tags.value.split(/[,，]/).map(x => x.trim()).filter(Boolean)}); await load();});
        const fileLabel = el("label", edit, "新图片版本（原项目保留旧版）"), file = el("input", fileLabel); file.type = "file"; file.accept = "image/*";
        action(edit, "上传为新版本", async () => {
          if (!file.files?.length) throw new Error("请先选择新图片。");
          const form = new FormData(); form.append("image", file.files[0]); form.append("asset_id", asset.id); form.append("category", kind.value); form.append("name", name.value); form.append("description", desc.value); form.append("tags", JSON.stringify(tags.value.split(/[,，]/).map(x => x.trim()).filter(Boolean)));
          await request("/h3lv/library/upload", form); await load();
        });
      }
    }
    if (!data.assets.length) el("p", cards, "没有符合筛选的素材。");
  }
  category.onchange = trash.onchange = () => void load().catch(error => status.textContent = error.message);
  action(tools, "清空选择", async () => {selected = []; await load();});
  action(tools, "搜索／刷新", load); search.onkeydown = event => {if (event.key === "Enter") void load().catch(error => status.textContent = error.message);};
  const uploadDetails = el("details", main); el("summary", uploadDetails, "添加图片（支持多选）");
  const uploadKind = select(uploadDetails, "上传分类", [["character", "角色"], ["scene", "场景"]]);
  const files = el("input", uploadDetails); files.type = "file"; files.accept = "image/*"; files.multiple = true;
  action(uploadDetails, "上传到素材库", async () => {
    if (!files.files?.length) throw new Error("请先选择图片。");
    let count = 0, duplicates = 0;
    for (const file of files.files) {
      const form = new FormData(); form.append("image", file); form.append("category", uploadKind.value); form.append("name", file.name.replace(/\.[^.]+$/, "")); form.append("description", ""); form.append("tags", "[]");
      const result = await request("/h3lv/library/upload", form); count++; if (result.duplicate) duplicates++;
    }
    files.value = ""; await load(); status.textContent = `已处理 ${count} 张图片${duplicates ? `，其中 ${duplicates} 张内容重复` : ""}。`;
  });
  if (onImport) {
    const footer = el("div", main); footer.className = "h3lv-actions h3lv-library-import";
    const mode = select(footer, "参考图", [["append", "追加到现有图片"], ["replace", "替换现有图片"]]);
    const noteMode = select(footer, "素材说明", [["append", "保留原说明并追加库说明"], ["keep", "仅保留原说明"], ["replace", "替换为所选库说明"]]);
    action(footer, "导入所选素材", async () => {
      if (!selected.length) throw new Error("请先选择素材。");
      if (mode.value === "append" && currentCount + selected.length > 6) throw new Error("追加后超过 6 张，请减少选择或改为替换。");
      if ((mode.value === "replace" || noteMode.value === "replace") && !window.confirm("确认替换当前编辑器的图片或说明？导入后仍需在审核面板保存。")) return;
      const imported = await request(`/h3lv/project/${encodeURIComponent(projectId)}/library/import`, {revision:getRevision(), selection:selected});
      await onImport(imported, {mode:mode.value, noteMode:noteMode.value}); busy = false; close();
    });
  }
  await load().catch(error => status.textContent = error.message);
  return {close};
}
