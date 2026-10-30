import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {generationOutputs} from "../web/video_outputs.js";

const source = fs.readFileSync(new URL("../web/h3lv.js", import.meta.url), "utf8");

async function payloadFor(prompt, chooseVideoOutput = () => {throw new Error("unexpected output selection");}) {
  const start = source.indexOf("async function buildGenerationPayload()");
  const end = source.indexOf("const startingProjects", start);
  const context = {app: {async graphToPrompt() {return {output: prompt, workflow: {}};}},
    api: {clientId: "test-client"}, generationOutputs, chooseVideoOutput};
  return vm.runInNewContext(`${source.slice(start, end)}\nbuildGenerationPayload()`, context);
}

test("sequential generation accepts native CreateVideo -> SaveVideo instead of VHS", async () => {
  const prompt = {
    "1": {class_type: "H3LVUnified", inputs: {}},
    "2": {class_type: "VAEDecode", inputs: {samples: ["1", 0]}},
    "3": {class_type: "CreateVideo", inputs: {images: ["2", 0], audio: ["1", 4], fps: 24}},
    "4": {class_type: "SaveVideo", inputs: {video: ["3", 0], filename_prefix: "video/H3", format: "auto", codec: "auto"}},
  };
  const payload = await payloadFor(prompt);
  assert.equal(payload.loader_id, "1");
  assert.equal(payload.video_id, "4");
});

function workflowWithOutputs() {
  return {
    "1": {class_type: "H3LVUnified", inputs: {}},
    "2": {class_type: "VAEDecode", inputs: {samples: ["1", 2]}},
    "3": {class_type: "CreateVideo", inputs: {images: ["2", 0], fps: 24}},
    "4": {class_type: "SaveVideo", inputs: {video: ["3", 0]}},
    "5": {class_type: "VHS_VideoCombine", inputs: {images: ["2", 0]}},
  };
}

test("mixed native and VHS outputs are selectable instead of rejected", async () => {
  const prompt = workflowWithOutputs();
  const payload = await payloadFor(prompt, outputs => {
    assert.deepEqual(outputs.map(([id]) => id), ["4", "5"]);
    return "4";
  });
  assert.equal(payload.video_id, "4");
  assert.equal((await payloadFor(prompt, () => "5")).video_id, "5");
  await assert.rejects(payloadFor(prompt, () => null), /已取消/);
});

test("disconnected and unrelated output nodes are ignored, not counted globally", async () => {
  const prompt = workflowWithOutputs();
  prompt["5"].inputs.images = ["10", 0];
  prompt["5"].inputs.filename_prefix = ["1", 3];
  prompt["10"] = {class_type: "LoadImage", inputs: {image: "unrelated.png"}};
  prompt["11"] = {class_type: "SaveVideo", inputs: {}};
  assert.equal((await payloadFor(prompt)).video_id, "4");
});

test("VHS with a connected fps input remains supported", async () => {
  const prompt = workflowWithOutputs();
  delete prompt["4"];
  prompt["5"].inputs.frame_rate = ["1", 12];
  assert.equal((await payloadFor(prompt)).video_id, "5");
});

test("output discovery terminates on cycles and rejects missing or multiple owners", () => {
  const prompt = workflowWithOutputs();
  prompt["2"].inputs.samples = ["3", 0];
  assert.throws(() => generationOutputs(prompt), /未找到/);
  prompt["6"] = {class_type: "H3LVUnified", inputs: {}};
  assert.throws(() => generationOutputs(prompt), /一个 H3/);
  delete prompt["1"];
  delete prompt["6"];
  assert.throws(() => generationOutputs(prompt), /一个 H3/);
});

test("native final preview targets the selected saver and uses the native output schema", () => {
  const start = source.indexOf("function showFinalOnVideoNode(");
  const end = source.indexOf("function clearVideoNodePreview(", start);
  const saver = {id: 4, comfyClass: "SaveVideo"};
  const vhs = {id: 5, comfyClass: "VHS_VideoCombine", updateParameters() {throw new Error("wrong saver");}};
  const events = [];
  const show = vm.runInNewContext(`${source.slice(start, end)}\nshowFinalOnVideoNode`, {
    app: {graph: {_nodes: [saver, vhs]}},
    api: {dispatchCustomEvent(name, output) {events.push({name, output});}},
  });
  assert.equal(show({filename: "final.mp4", subfolder: "H3LongVideo/final_videos"}, "project", "4"), true);
  assert.equal(events[0].name, "executed");
  assert.equal(events[0].output.node, "4");
  assert.equal(events[0].output.output.images[0].filename, "final.mp4");
  assert.equal(events[0].output.output.animated[0], true);
  assert.equal(show({filename: "final.mp4", subfolder: "H3LongVideo/final_videos"}, "project", "4"), true);
  assert.equal(events.length, 1);
});

test("output selection dialog defaults to native, allows VHS, and cleans up on cancel", async () => {
  const start = source.indexOf("function chooseVideoOutput(");
  const end = source.indexOf("async function buildGenerationPayload()", start);
  const elements = [], buttons = [], handlers = new Map();
  function element(tag, text, parent) {
    const item = {tag, text, children: [], value: "", removed: false,
      setAttribute() {}, focus() {}, remove() {this.removed = true;}};
    parent?.children?.push(item); elements.push(item); return item;
  }
  const choose = vm.runInNewContext(`${source.slice(start, end)}\nchooseVideoOutput`, {
    document: {body: {}}, element, queueMicrotask,
    actionButton(parent, label, callback) {buttons.push({label, callback});},
    window: {addEventListener(name, callback) {handlers.set(name, callback);},
      removeEventListener(name) {handlers.delete(name);}},
  });
  const outputs = [["5", {class_type: "VHS_VideoCombine"}], ["4", {class_type: "SaveVideo"}]];
  const selection = choose(outputs);
  const select = elements.find(item => item.tag === "select");
  assert.equal(select.value, "4");
  select.value = "5";
  buttons.find(button => button.label === "使用此输出").callback();
  assert.equal(await selection, "5");
  assert.equal(elements[0].removed, true);
  assert.equal(handlers.size, 0);
  const cancellation = choose(outputs);
  handlers.get("keydown")({key: "Escape"});
  assert.equal(await cancellation, null);
  assert.equal(handlers.size, 0);
});

test("starting native generation clears only the selected preview and resets final deduplication", () => {
  const start = source.indexOf("function clearVideoNodePreview(");
  const end = source.indexOf("async function restoreFinalVideoPreview()", start);
  const node = {id: 4, comfyClass: "SaveVideo", __h3lvFinalPreview: "final.mp4"};
  const events = [];
  const clear = vm.runInNewContext(`${source.slice(start, end)}\nclearVideoNodePreview`, {
    app: {graph: {_nodes: [node, {id: 5, comfyClass: "VHS_VideoCombine"}]}},
    api: {dispatchCustomEvent(name, detail) {events.push({name, detail});}},
  });
  assert.equal(clear("4"), true);
  assert.equal(node.__h3lvFinalPreview, undefined);
  assert.equal(events[0].detail.node, "4");
  assert.equal(events[0].detail.output.images.length, 0);
});
