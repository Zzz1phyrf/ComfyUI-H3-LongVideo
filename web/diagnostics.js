import { api } from "../../scripts/api.js";

export async function reportUiError(error, context = {}) {
  if (String(error?.message || error).startsWith("已取消选择视频输出")) return false;
  let timer;
  try {
    const controller = new AbortController();
    timer = setTimeout(() => controller.abort(), 5000);
    const payload = {message: String(error?.message || error).slice(0, 2000),
      stack: String(error?.stack || "").slice(0, 4000)};
    for (const key of ["source", "action", "project_id", "node_id"]) {
      if (context[key] !== undefined) payload[key] = String(context[key]).slice(0, 200);
    }
    const response = await api.fetchApi("/h3lv/logs/frontend", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
      signal: controller.signal});
    return response.ok;
  } catch {
    return false; // Diagnostics never replace the original popup or task error.
  } finally {
    clearTimeout(timer);
  }
}

export function withUiLogging(action, context) {
  return async function (...args) {
    try { return await action.apply(this, args); }
    catch (error) {
      void reportUiError(error, context);
      throw error;
    }
  };
}

export async function downloadErrorLog() {
  const response = await api.fetchApi("/h3lv/logs/errors");
  if (!response.ok) throw new Error("无法下载错误日志，请检查 ComfyUI 是否正在运行。");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = "H3LongVideo-errors.log";
  document.body.append(link);
  try { link.click(); }
  finally {
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export function renderFailure(parent, info, {locate, copy} = {}) {
  parent.replaceChildren();
  parent.hidden = !info;
  if (!info) return;
  const add = (tag, text, host = parent) => {
    const item = document.createElement(tag);
    item.textContent = text;
    host.append(item);
    return item;
  };
  parent.setAttribute("role", "alert");
  add("strong", info.summary || "操作失败");
  // Unstructured legacy errors stay in technical details instead of the status bar.
  if (!String(info.reason || "").includes("prompt_outputs_failed_validation")) add("p", info.reason || "");
  for (const issue of info.issues || []) {
    const row = add("div", "");
    add("p", `${issue.title || issue.node_type || "节点"}${issue.node_id ? ` #${issue.node_id}` : ""}：${issue.reason}`, row);
    add("p", issue.action || "", row);
    if (issue.node_id && locate) {
      const button = add("button", "定位本地节点", row);
      button.type = "button";
      button.onclick = () => locate(issue.node_id, issue.node_type);
    }
  }
  add("p", `下一步：${info.action || "展开详情并下载错误日志，核对失败节点。"}`);
  if (info.preserved) add("p", info.preserved);
  if (info.task_ids?.length) add("p", `RH 任务 ID：${info.task_ids.join("、")}`);
  const details = add("details", "");
  add("summary", "技术详情", details);
  add("pre", JSON.stringify(info, null, 2), details);
  const button = add("button", "复制诊断");
  button.type = "button";
  button.onclick = async () => {
    try {
      await (copy || (text => navigator.clipboard.writeText(text)))(JSON.stringify(info, null, 2));
      button.textContent = "已复制";
    } catch { button.textContent = "复制失败，请展开详情手动复制"; }
  };
}
