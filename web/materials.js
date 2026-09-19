import { api } from "../../scripts/api.js";

function el(tag, parent, text) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  parent.append(item); return item;
}

export function materialEditor(parent, {projectId, index = 0, refs = [], note = "", defaults, source = "default", changed}) {
  const box = el("section", parent); box.className = "h3lv-material-editor";
  const value = [...refs];
  let sourceSelect;
  if (defaults) {
    const controls = el("div", box); controls.className = "h3lv-actions";
    const sourceLabel = el("label", controls, "参考图来源 ");
    sourceSelect = el("select", sourceLabel);
    for (const [id, title] of [["default", "使用项目默认"], ["custom", "本段自定义"]]) el("option", sourceSelect, title).value = id;
    sourceSelect.value = source;
  }
  const list = el("div", box); list.className = "h3lv-reference-list";
  const actions = el("div", box); actions.className = "h3lv-actions";
  const upload = el("input", box); upload.type = "file"; upload.accept = "image/*"; upload.multiple = true; upload.hidden = true;
  const add = el("button", actions, "＋ 上传图片（最多 6 张）"); add.type = "button"; add.className = "h3lv-button";
  add.onclick = () => upload.click();
  const textarea = el("textarea", box); textarea.className = "h3lv-material-note"; textarea.value = note;
  textarea.placeholder = "按顺序说明用途，例如：图1为人物三视图，图2为背景；三视图是同一个人。";
  const hint = el("p", box, "图片顺序就是扩写与 H3 的图号。更换或排序后请核对用途说明。"); hint.className = "h3lv-help";
  let customNote = note;
  let uploading = false;
  textarea.oninput = () => {customNote = textarea.value; changed();};
  const inherited = () => defaults && sourceSelect.value === "default";
  function render() {
    const linked = inherited();
    const names = linked ? defaults().refs : value;
    textarea.value = linked ? defaults().note : customNote;
    textarea.disabled = Boolean(linked); add.disabled = Boolean(linked) || uploading;
    add.classList.toggle("is-inherited", Boolean(linked));
    add.title = linked ? "选择“本段自定义”后可上传图片" : "";
    upload.disabled = Boolean(linked) || uploading;
    list.replaceChildren();
    if (!names.length) el("p", list, linked ? "项目默认图尚未上传。" : "请为本段上传参考图。");
    names.forEach((name, position) => {
      const item = el("figure", list); item.className = "h3lv-reference-item";
      const image = el("img", item); image.src = api.apiURL(`/h3lv/project/${projectId}/refs/${encodeURIComponent(name)}`); image.alt = `图${position+1}`; image.loading = "lazy";
      el("figcaption", item, `图${position+1}`);
      if (linked) return;
      const tools = el("div", item); tools.className = "h3lv-reference-tools";
      for (const [title, step] of [["←", -1], ["→", 1], ["移除", 0]]) {
        const button = el("button", tools, title); button.type = "button"; button.className = "h3lv-reference-tool";
        button.disabled = step !== 0 && (position+step < 0 || position+step >= value.length);
        button.onclick = () => {
          if (!step) value.splice(position, 1);
          else [value[position], value[position+step]] = [value[position+step], value[position]];
          render(); changed();
        };
      }
    });
  }
  if (sourceSelect) {
    sourceSelect.onchange = () => {render(); changed();};
    const copy = el("button", actions, "复制默认图到本段"); copy.type = "button"; copy.className = "h3lv-button";
    copy.onclick = () => {value.splice(0, value.length, ...defaults().refs); customNote = defaults().note; sourceSelect.value = "custom"; render(); changed();};
  }
  upload.onchange = async () => {
    const files = [...upload.files]; upload.value = "";
    if (value.length + files.length > 6) {window.alert("每段最多 6 张图片，请减少所选文件。"); return;}
    uploading = true; add.disabled = true;
    try {
      for (const file of files) {
        const form = new FormData(); form.append("index", String(index)); form.append("image", file, file.name);
        const response = await api.fetchApi(`/h3lv/project/${projectId}/refs`, {method:"POST", body:form});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "上传失败");
        value.push(result.name); changed(); render();
      }
    } catch (error) {window.alert(error.message);}
    finally {uploading = false; render();}
  };
  render();
  return {refs:value, note:textarea, source:sourceSelect, renderRefs:render,
    getNote: () => customNote};
}
