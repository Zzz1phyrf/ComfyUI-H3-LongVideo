import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../web/diagnostics.js", import.meta.url), "utf8")
  .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, "");

function moduleContext(extra) {
  return vm.runInNewContext(`${source}\n({reportUiError, withUiLogging, downloadErrorLog})`,
    {AbortController, setTimeout, clearTimeout, ...extra});
}

test("UI error reports are bounded and contain no arbitrary input payload", async () => {
  const requests = [];
  const functions = moduleContext({api: {async fetchApi(path, options) {
    requests.push({path, payload: JSON.parse(options.body)}); return {ok: true};
  }}});
  const error = {message: "中文".repeat(5000), stack: "stack".repeat(5000)};
  assert.equal(await functions.reportUiError(error, {source: "h3lv", node_id: 7,
    project_id: "abc", inputs: "PRIVATE-AUDIO", prompt: "PRIVATE-PROMPT"}), true);
  assert.equal(requests[0].path, "/h3lv/logs/frontend");
  assert.equal(requests[0].payload.message.length, 2000);
  assert.equal(requests[0].payload.stack.length, 4000);
  assert.equal(requests[0].payload.node_id, "7");
  assert.equal(requests[0].payload.inputs, undefined);
  assert.equal(requests[0].payload.prompt, undefined);
  assert.ok(Buffer.byteLength(JSON.stringify(requests[0].payload)) < 32768);
});

test("report failures are swallowed and cancellation is not an error", async () => {
  let calls = 0;
  const functions = moduleContext({api: {async fetchApi() {calls++; throw new Error("offline");}}});
  assert.equal(await functions.reportUiError(new Error("failed action")), false);
  assert.equal(await functions.reportUiError(new Error("已取消选择视频输出；没有提交生成任务。")), false);
  assert.equal(calls, 1);
});

test("UI wrapper preserves this, arguments, result and original exception without retry", async () => {
  const requests = [];
  const functions = moduleContext({api: {async fetchApi(path, options) {
    requests.push(JSON.parse(options.body)); return {ok: true};
  }}});
  const owner = {number: 5};
  const action = functions.withUiLogging(function (amount) {return this.number + amount;}, {source: "h3lv"});
  assert.equal(await action.call(owner, 2), 7);
  assert.equal(requests.length, 0);
  const error = new Error("original failure");
  let calls = 0;
  const failing = functions.withUiLogging(() => {calls++; throw error;}, {action: "运行工作流"});
  await assert.rejects(failing(), received => received === error);
  assert.equal(calls, 1);
  assert.equal(requests[0].action, "运行工作流");
});

test("download button fetches a log attachment and cleans up its blob URL", async () => {
  const actions = [];
  const link = {click() {actions.push("click");}, remove() {actions.push("remove");}};
  const functions = moduleContext({api: {async fetchApi(path) {
    assert.equal(path, "/h3lv/logs/errors"); return {ok: true, blob: async () => "log-blob"};
  }}, document: {createElement: () => link, body: {append() {actions.push("append");}}},
  URL: {createObjectURL(blob) {assert.equal(blob, "log-blob"); return "blob:test";},
    revokeObjectURL(url) {actions.push(url);}}, setTimeout: callback => callback()});
  await functions.downloadErrorLog();
  assert.equal(link.download, "H3LongVideo-errors.log");
  assert.equal(link.href, "blob:test");
  assert.deepEqual(actions, ["append", "click", "remove", "blob:test"]);
});

test("download failure is visible and never saves an error response as a log", async () => {
  const functions = moduleContext({api: {async fetchApi() {return {ok: false};}}});
  await assert.rejects(functions.downloadErrorLog(), /无法下载错误日志/);
});

test("long-video node exposes a non-serialized download widget without changing inputs", async () => {
  let extension, downloads = 0;
  const app = {registerExtension(value) {extension = value;}};
  const script = fs.readFileSync(new URL("../web/h3lv.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace('new URL("./h3lv.css", import.meta.url).href', '"h3lv.css"');
  vm.runInNewContext(script, {app, document: {querySelector: () => ({})},
    setTimeout: callback => callback(), downloadErrorLog: async () => {downloads++;}});
  function Node() {}
  await extension.beforeRegisterNodeDef(Node, {name: "H3LVUnified"});
  const node = {widgets: [], inputs: [],
    addWidget(type, name, value, callback) {
      const item = {type, name, callback}; this.widgets.push(item); return item;
    }};
  Node.prototype.onNodeCreated.call(node);
  const button = node.widgets.find(item => item.name === "下载错误日志");
  assert.equal(button.serialize, false);
  await button.callback();
  assert.equal(downloads, 1);
  assert.deepEqual(node.inputs, []);
});

test("unrelated workflow queue errors are not collected by the plugin", async () => {
  let extension, wrapped = 0;
  const failure = new Error("unrelated queue error");
  const app = {graph: {_nodes: [{comfyClass: "OtherNode"}]},
    registerExtension(value) {extension = value;}, async queuePrompt() {throw failure;}};
  const script = fs.readFileSync(new URL("../web/h3lv.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "")
    .replace('new URL("./h3lv.css", import.meta.url).href', '"h3lv.css"');
  vm.runInNewContext(script, {app, api: {addEventListener() {}},
    document: {querySelector: () => ({})}, withUiLogging() {wrapped++;}});
  await extension.setup();
  await assert.rejects(app.queuePrompt(1), error => error === failure);
  assert.equal(wrapped, 0);
});
