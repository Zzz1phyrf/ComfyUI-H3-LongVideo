import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

test("preview options distinguish CLI settings and preserve old node defaults", () => {
  const source = fs.readFileSync(new URL("../web/expansion.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "");
  const context = {app: {registerExtension() {}}, api: {}, reportUiError() {}, withUiLogging: x => x};
  vm.runInNewContext(source+"\nglobalThis.readOptions = options;", context);
  const widgets = Object.entries({mode: "vision", model: "api-model", rule: "rule", revision: 2})
    .map(([name,value]) => ({name,value}));
  const legacy = JSON.parse(JSON.stringify(context.readOptions({widgets})));
  assert.equal(legacy.provider, "api");
  assert.equal(legacy.codex_model, "");
  widgets.push(...Object.entries({provider: "codex_cli", codex_model: "gpt-fixture",
    codex_reasoning: "high", codex_timeout: 600}).map(([name,value]) => ({name,value})));
  const current = JSON.parse(JSON.stringify(context.readOptions({widgets})));
  assert.equal(current.provider, "codex_cli");
  assert.equal(current.codex_model, "gpt-fixture");
  assert.equal(current.codex_reasoning, "high");
  assert.equal(current.codex_timeout, 600);
});

test("API settings UI preserves legacy profiles and sends new options without serializing widgets", async () => {
  const elements = [], requests = [];
  const document = {body: {append() {}}, createElement(tag) {
    const item = {tag, value: "", children: [], append(child) {this.children.push(child);},
      setAttribute(name, value) {this[name] = value;}, remove() {}, replaceChildren() {this.children = [];}};
    elements.push(item); return item;
  }};
  let extension;
  const app = {registerExtension(value) {extension = value;}};
  const api = {async fetchApi(path, options) {
    if (options?.body) requests.push(JSON.parse(options.body));
    return {ok: true, async json() {return {base_url: "https://compatible.test/v1", configured: true};}};
  }};
  const source = fs.readFileSync(new URL("../web/expansion.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "");
  vm.runInNewContext(source, {app, api, document, window: {alert() {}}, reportUiError: async () => true,
    withUiLogging: action => action});
  function Node() {}
  await extension.beforeRegisterNodeDef(Node, {name: "H3LVPromptExpand"});
  const widgets = [];
  const node = {widgets: [{name: "rule", options: {}}], size: [300, 300],
    addWidget(type, name, value, callback) {const widget = {name, callback}; widgets.push(widget); return widget;}};
  Node.prototype.onNodeCreated.call(node);
  await widgets.find(item => item.name === "API 设置与模型选择").callback();
  const find = text => elements.find(item => item.textContent === text);
  const timeout = find("连接／读取等待超时（秒）").children[0];
  assert.equal(timeout.value, 300);
  timeout.value = "600";
  find("响应方式").children[0].value = "stream";
  find("思考参数").children[0].value = "provider_default";
  const extra = elements.find(item => item["aria-label"] === "额外请求参数");
  extra.value = '{"max_tokens":null,"max_completion_tokens":4096}';
  await find("保存本机配置").onclick();
  assert.deepEqual(requests[0], {base_url: "https://compatible.test/v1", api_key: "",
    timeout_seconds: 600, response_mode: "stream", thinking_mode: "provider_default",
    extra_body: {max_tokens: null, max_completion_tokens: 4096}});
  assert.ok(widgets.every(item => item.serialize === false));
  extra.value = "not JSON";
  await find("保存本机配置").onclick();
  assert.equal(requests.length, 1);
  assert.ok(elements.some(item => String(item.textContent).includes("不是有效 JSON")));
});
