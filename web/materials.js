import { api } from "../../scripts/api.js";
import {insertImageMention, mentionedImageNumbers, remapImageMentions} from "./material_mentions.js";

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
  const mention = el("button", actions, "@ 引用图片"); mention.type = "button"; mention.className = "h3lv-button h3lv-mention-button";
  const textarea = el("textarea", box); textarea.className = "h3lv-material-note"; textarea.value = note;
  textarea.placeholder = "输入 @ 选择图片，例如：@图1 为人物三视图，@图2 为背景；三视图是同一个人。";
  const picker = el("div", box); picker.className = "h3lv-mention-picker"; picker.hidden = true;
  const hint = el("p", box, "输入 @ 或点击“引用图片”可插入图片引用；图片重排时引用会跟随原图。"); hint.className = "h3lv-help";
  let customNote = note;
  let uploading = false;
  let mentionRange = null;
  const currentNames = () => inherited() ? defaults().refs : value;
  function closePicker() {picker.hidden = true; mentionRange = null;}
  function renderPicker() {
    picker.replaceChildren();
    currentNames().forEach((name, position) => {
      const choice = el("button", picker); choice.type = "button"; choice.className = "h3lv-mention-choice";
      const image = el("img", choice); image.src = api.apiURL(`/h3lv/project/${projectId}/refs/${encodeURIComponent(name)}`); image.alt = `图${position+1}`;
      el("span", choice, `@图${position+1}`);
      choice.onclick = () => {
        const range = mentionRange || {start:textarea.selectionStart, end:textarea.selectionEnd};
        const inserted = insertImageMention(customNote, range.start, range.end, position+1);
        customNote = inserted.value; textarea.value = customNote; closePicker(); changed();
        textarea.focus(); textarea.setSelectionRange(inserted.cursor, inserted.cursor);
      };
    });
  }
  function openPicker(replaceTypedAt = false) {
    if (textarea.disabled || !currentNames().length) return;
    const end = textarea.selectionStart;
    mentionRange = {start:replaceTypedAt ? Math.max(0, end-1) : end, end:textarea.selectionEnd};
    renderPicker(); picker.hidden = false;
  }
  mention.onclick = () => {textarea.focus(); openPicker(false);};
  textarea.oninput = () => {
    customNote = textarea.value; changed();
    const cursor = textarea.selectionStart;
    if (cursor > 0 && textarea.value[cursor-1] === "@") openPicker(true);
    else closePicker();
  };
  textarea.onkeydown = event => {if (event.key === "Escape") closePicker();};
  const inherited = () => defaults && sourceSelect.value === "default";
  function render() {
    const linked = inherited();
    const names = linked ? defaults().refs : value;
    textarea.value = linked ? defaults().note : customNote;
    textarea.disabled = Boolean(linked); add.disabled = Boolean(linked) || uploading; mention.disabled = Boolean(linked) || !names.length;
    add.classList.toggle("is-inherited", Boolean(linked));
    add.title = linked ? "选择“本段自定义”后可上传图片" : "";
    mention.title = linked ? "请在项目默认参考图区编辑默认素材说明" : (!names.length ? "请先上传图片" : "插入当前图片的 @图号引用");
    upload.disabled = Boolean(linked) || uploading;
    closePicker();
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
          const before = [...value];
          if (!step) {
            if (mentionedImageNumbers(customNote).has(position+1)) {
              window.alert(`素材说明正在引用 @图${position+1}，请先删除该引用再移除图片。`);
              return;
            }
            value.splice(position, 1);
          }
          else [value[position], value[position+step]] = [value[position+step], value[position]];
          customNote = remapImageMentions(customNote, before, value);
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
