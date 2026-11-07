import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

function settingsHarness() {
  const elements = [], calls = [];
  let profile = {base_url: "https://old.test/v1", configured: true};
  let extension, confirmations = 0, discard = false, failSave = false, failModels = false;
  const document = {body: {append() {}}, createElement(tag) {
    const item = {tag, value: "", children: [], append(child) {this.children.push(child);},
      setAttribute(name, value) {this[name] = value;}, remove() {this.removed = true;},
      replaceChildren() {this.children = [];}};
    elements.push(item); return item;
  }};
  const api = {async fetchApi(path, options) {
    calls.push({path, body: options?.body && JSON.parse(options.body), base_url: profile.base_url});
    if (path.endsWith("/models") && failModels) return {ok: false, json: async () => ({error: "模型列表读取失败"})};
    if (options?.body) {
      if (failSave) return {ok: false, json: async () => ({error: "保存失败"})};
      const payload = JSON.parse(options.body);
      profile = {...payload, configured: Boolean(payload.api_key || profile.configured)};
      delete profile.api_key;
    }
    return {ok: true, json: async () => path.endsWith("/models") ? {models: ["vision-test"]} : {...profile}};
  }};
  const source = fs.readFileSync(new URL("../web/expansion.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "");
  vm.runInNewContext(source, {app: {registerExtension(value) {extension = value;}}, api, document,
    window: {confirm() {confirmations++; return discard;}}, reportUiError: async () => true,
    withUiLogging: action => action});
  const node = {widgets: [{name: "model", value: "old-model"}], setDirtyCanvas() {}};
  return {
    calls, node,
    async open() {elements.length = 0;
      // Exercise the registered node's real settings button.
      function Node() {}
      await extension.beforeRegisterNodeDef(Node, {name: "H3LVPromptExpand"});
      const buttons = [];
      node.size = [300, 300];
      node.widgets.push({name: "rule", options: {}});
      node.addWidget = (type, name, value, callback) => {const w = {name, callback}; buttons.push(w); return w;};
      Node.prototype.onNodeCreated.call(node);
      await buttons.find(w => w.name === "API 设置与模型选择").callback();
    },
    find(text) {return elements.find(e => e.textContent === text);},
    get elements() {return elements;},
    get confirmations() {return confirmations;},
    set discard(value) {discard = value;},
    set failSave(value) {failSave = value;},
    set failModels(value) {failModels = value;},
  };
}

test("changing provider then reading models persists the new settings for reopening", async () => {
  const h = settingsHarness(); await h.open();
  h.find("API 基础地址").children[0].value = "https://new.test/v1";
  h.find("API Key").children[0].value = "fixture-new-key";
  await h.find("读取模型列表").onclick();
  assert.equal(h.calls.find(c => c.path.endsWith("/models")).base_url, "https://new.test/v1");
  assert.equal(h.find("API Key").children[0].value, "");
  await h.open();
  assert.equal(h.find("API 基础地址").children[0].value, "https://new.test/v1");
});

test("using a model saves edited settings and save failure cannot switch the model", async () => {
  const h = settingsHarness(); await h.open();
  h.elements.find(e => e.tag === "select" && e.size === 9).value = "vision-test";
  h.find("API 基础地址").children[0].value = "https://new.test/v1";
  h.find("API Key").children[0].value = "fixture-new-key";
  h.failSave = true;
  await h.find("使用所选模型").onclick();
  assert.equal(h.node.widgets[0].value, "old-model");
  assert.equal(h.find("API Key").children[0].value, "fixture-new-key");
  assert.ok(h.elements.some(e => e.textContent === "保存失败"));
  h.failSave = false;
  await h.find("使用所选模型").onclick();
  assert.equal(h.node.widgets[0].value, "vision-test");
  await h.open();
  assert.equal(h.find("API 基础地址").children[0].value, "https://new.test/v1");
});

test("closing dirty settings allows keeping edits or explicitly discarding them", async () => {
  const h = settingsHarness(); await h.open();
  const shade = h.elements.find(e => e.className === "h3lv-shade");
  h.find("API 基础地址").children[0].value = "https://new.test/v1";
  h.find("关闭").onclick();
  assert.equal(h.confirmations, 1);
  assert.ok(!shade.removed);
  h.discard = true; h.find("关闭").onclick();
  assert.equal(shade.removed, true);
  assert.ok(!h.calls.some(c => c.body));
});

test("invalid settings stop model requests instead of contacting the old provider", async () => {
  const h = settingsHarness(); await h.open();
  h.elements.find(e => e["aria-label"] === "额外请求参数").value = "not JSON";
  await h.find("读取模型列表").onclick();
  assert.ok(!h.calls.some(c => c.path.endsWith("/models")));
});

test("a failed model-list request cannot undo saved provider settings", async () => {
  const h = settingsHarness(); await h.open();
  h.find("API 基础地址").children[0].value = "https://new.test/v1";
  h.find("API Key").children[0].value = "fixture-new-key";
  h.failModels = true;
  await h.find("读取模型列表").onclick();
  assert.ok(h.elements.some(e => e.textContent === "模型列表读取失败"));
  h.find("关闭").onclick();
  assert.equal(h.confirmations, 0);
  await h.open();
  assert.equal(h.find("API 基础地址").children[0].value, "https://new.test/v1");
});

test("reading models with unchanged fields does not rewrite credentials", async () => {
  const h = settingsHarness(); await h.open();
  await h.find("读取模型列表").onclick();
  assert.ok(!h.calls.some(c => c.body));
  assert.ok(h.calls.some(c => c.path.endsWith("/models")));
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
