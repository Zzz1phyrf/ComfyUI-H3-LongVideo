import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

test("actual execution-error listener displays ASR reason, supports log download and closing", async () => {
  const elements = [], listeners = new Map(), requests = [];
  let extension;
  const document = {body: {append() {}}, querySelector: () => ({}),
    getElementById: id => elements.find(e => e.id === id && !e.removed),
    createElement(tag) {
      const e = {tag, children: [], append(child) {this.children.push(child);},
        setAttribute(k, v) {this[k] = v;}, replaceChildren() {this.children = [];},
        remove() {this.removed = true;}, click() {this.clicked = true;}};
      elements.push(e); return e;
    }};
  const owner = {id: 266, comfyClass: "H3LVUnified"};
  const app = {graph: {_nodes: [owner]}, registerExtension(value) {extension = value;}, queuePrompt() {}};
  const api = {addEventListener(name, fn) {listeners.set(name, fn);}, async fetchApi(path) {
    requests.push(path); return {ok: true, blob: async () => "shared-ASR-report"};
  }};
  const diagnostics = fs.readFileSync(new URL("../web/diagnostics.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");
  const h3lv = fs.readFileSync(new URL("../web/h3lv.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace('new URL("./h3lv.css", import.meta.url).href', '"h3lv.css"');
  vm.runInNewContext(diagnostics + "\n" + h3lv, {document, app, api,
    setWorkflowProgress() {}, setTimeout: fn => fn(),
    URL: {createObjectURL: () => "blob:logs", revokeObjectURL() {}}});
  await extension.setup();
  const failed = listeners.get("execution_error");
  assert.equal(typeof failed, "function");
  const detail = {node_id: "266", node_type: "H3LVUnified",
    exception_type: "plugin.diagnostics.ASRError",
    exception_message: "语音识别（ASR）失败，退出码 1。\nProxyError: Cannot connect to proxy [WinError 10061]"};
  failed({detail: {...detail, exception_type: "ValueError"}});
  failed({detail: {...detail, node_id: "other"}});
  assert.ok(!elements.some(e => e.id === "h3lv-asr-error"));
  failed({detail});
  assert.ok(elements.some(e => String(e.textContent).includes("ProxyError")));
  assert.ok(elements.some(e => String(e.textContent).includes("10061")));
  const download = elements.find(e => e.tag === "button" && e.textContent === "下载错误日志");
  await download.onclick();
  assert.deepEqual(requests, ["/h3lv/logs/errors"]);
  assert.equal(elements.find(e => e.tag === "a").download, "H3LongVideo-errors.log");
  elements.find(e => e.tag === "button" && e.textContent === "关闭").onclick();
  assert.equal(elements.find(e => e.id === "h3lv-asr-error").removed, true);
});
