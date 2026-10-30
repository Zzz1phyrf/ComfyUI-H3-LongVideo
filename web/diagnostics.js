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
