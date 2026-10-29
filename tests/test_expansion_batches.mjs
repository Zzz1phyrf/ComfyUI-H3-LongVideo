import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const plain = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture(overrides = {}) {
  const elements = [], calls = [], timers = new Map(); let nextTimer = 0;
  const document = {body:{append() {}},createElement(tag) {
    let value = "";
    const item = {tag,children:[],append(child) {child.parent = this; this.children.push(child);},
      setAttribute(name,value) {this[name] = value;},remove() {this.removed = true;},replaceChildren() {this.children = [];}};
    Object.defineProperty(item,"value",{get:() => value,set:next => {value = String(next); if (tag === "option" && item.parent?.tag === "select" && item.parent.children[0] === item) item.parent.value = value;}});
    elements.push(item); return item;
  }};
  const plan = {id:"p",revision:3,materials_version:1,default_refs:["person.png"],default_material_note:"角色",segments:[0,1].map(index => ({index,start:index*5,end:index*5+5,visual_type:"performer",expanded_prompt:index ? "人工原文" : ""}))};
  const job = {id:"job",project_id:"p",project_revision:3,state:"completed",settings:{provider:"codex_cli"},items:[{index:0,status:"succeeded",text:"候选0",source:"codex_cli"},{index:1,status:"cached",text:"候选1",source:"cache"}]};
  const context = {document,app:{graph:{links:{}},registerExtension() {}}, window:{confirm:() => true},
    reportUiError:async () => {},withUiLogging:fn => fn,
    setTimeout(fn) {timers.set(++nextTimer,fn); return nextTimer;},clearTimeout(id) {timers.delete(id);},
    api:{async fetchApi(path,init) {
      const body = init?.body && JSON.parse(init.body); calls.push({path,body});
      let result;
      if (overrides.fetch) result = await overrides.fetch(path,body);
      if (result === undefined) {
        if (path === "/h3lv/projects") result = [{id:"p",mode:"singing",count:2},{id:"q",mode:"singing",count:2}];
        else if (path.endsWith("/apply")) result = {revision:4,changed:[0,1],plan:{...plan,revision:4}};
        else if (path.endsWith("/retry")) result = {...job,id:"retry"};
        else if (path.endsWith("/expansion-batches")) result = body ? job : {jobs:[job]};
        else if (path.includes("/expansion-batches/")) result = job;
        else result = {...plan,id:path.split("/").at(-1)};
      }
      return {ok:true,json:async () => result};
    }}};
  const source = fs.readFileSync(new URL("../web/expansion.js",import.meta.url),"utf8").replace(/^import .*;\r?\n/gm,"");
  vm.createContext(context); vm.runInContext(source+"\nglobalThis.testing = {previewDialog,batchApplyBody};",context);
  const node = {widgets:Object.entries({mode:"vision",model:"api-model",rule:"rule",revision:1,provider:"codex_cli",codex_model:"fixture",codex_reasoning:"high"}).map(([name,value]) => ({name,value}))};
  return {elements,calls,timers,context,plan,job,node,find:text => elements.find(item => item.textContent === text)};
}

test("batch apply filters successful selection and sends edits together under one revision", () => {
  const h = fixture();
  const job = {...h.job,items:[...h.job.items,{index:2,status:"failed",text:"bad"}]};
  const body = h.context.testing.batchApplyBody(h.plan,job,new Set([0,1,2]),new Map([[0,"编辑0"],[1,"编辑1"],[2,"bad"]]));
  assert.deepEqual(plain(body),{revision:3,indices:[0,1],edits:{0:"编辑0",1:"编辑1"}});
  assert.throws(() => h.context.testing.batchApplyBody(h.plan,job,new Set([2]),new Map()), /勾选/);
});

test("batch start uses node CLI options, selected segments and default concurrency two", async () => {
  const h = fixture(); await h.context.testing.previewDialog(h.node);
  const concurrency = h.find("并发").children.find(item => item.tag === "select");
  assert.deepEqual(concurrency.children.map(item => item.value),["1","2","3","4"]);
  assert.equal(concurrency.value,"2");
  await h.find("启动批量扩写").onclick();
  const start = h.calls.find(item => item.body && item.path.endsWith("/expansion-batches"));
  assert.equal(start.body.concurrency,2); assert.equal(start.body.force,false);
  assert.deepEqual(plain(start.body.indices),[0,1]); assert.equal(start.body.provider,"codex_cli"); assert.equal(start.body.codex_model,"fixture");
});

for (const value of [3,4]) {
  test(`batch start sends selected concurrency ${value}`, async () => {
    const h = fixture(); await h.context.testing.previewDialog(h.node);
    h.find("并发").children.find(item => item.tag === "select").value = String(value);
    await h.find("启动批量扩写").onclick();
    const start = h.calls.find(item => item.body && item.path.endsWith("/expansion-batches"));
    assert.equal(start.body.concurrency,value);
  });

  for (const onlyFailed of [true,false]) {
    test(`${onlyFailed ? "selected failure retry" : "unfinished batch resume"} sends current concurrency ${value}`, async () => {
      const h = fixture();
      h.job.items = [{index:0,status:"failed",error:"request failed"},{index:1,status:"cancelled"}];
      await h.context.testing.previewDialog(h.node);
      await h.find("启动批量扩写").onclick();
      const start = h.calls.find(item => item.body && item.path.endsWith("/expansion-batches"));
      assert.equal(start.body.concurrency,2);
      h.find("并发").children.find(item => item.tag === "select").value = String(value);
      if (onlyFailed) {
        const check = h.find("第 1 段 · 失败").parent.children.find(item => item.tag === "input");
        check.checked = true; check.onchange();
      }
      await h.find(onlyFailed ? "仅重试选中失败段" : "续跑未完成段").onclick();
      const retries = h.calls.filter(item => item.body && item.path.endsWith("/retry"));
      assert.equal(retries.length,1);
      assert.equal(retries[0].body.concurrency,value);
      assert.deepEqual(plain(retries[0].body.indices),onlyFailed ? [0] : [0,1]);
    });
  }
}

test("batch edits apply once and unsaved candidates block close or switching", async () => {
  const h = fixture(); await h.context.testing.previewDialog(h.node);
  const jobs = h.elements.find(item => item["aria-label"] === "历史批次"); jobs.value = "job"; jobs.onchange(); await flush();
  const candidate = h.elements.find(item => item["aria-label"] === "第 1 段批次候选");
  candidate.value = "编辑后的候选"; candidate.oninput();
  h.context.window.confirm = () => false;
  const project = h.elements.find(item => item["aria-label"] === "分析项目"); project.value = "q"; project.onchange();
  assert.equal(project.value,"p"); h.find("关闭").onclick(); assert.equal(h.elements[0].removed,undefined);
  await h.find("选中成功结果").onclick(); await h.find("保存选中成功结果").onclick();
  const writes = h.calls.filter(item => item.path.endsWith("/apply")); assert.equal(writes.length,1);
  assert.deepEqual(plain({revision:writes[0].body.revision, indices:writes[0].body.indices, edits:writes[0].body.edits}),{revision:3,indices:[0,1],edits:{0:"编辑后的候选"}});
  assert.equal(writes[0].body.provider,"codex_cli"); assert.equal(writes[0].body.codex_model,"fixture"); assert.equal(writes[0].body.rule,"rule");
  assert.equal(h.calls.filter(item => item.path.endsWith("/expand")).length,0);
  h.find("关闭").onclick(); assert.equal(h.elements[0].removed,true);
});

test("late job polling cannot populate a newly selected project and closing stops polling without cancel", async () => {
  let resolvePoll;
  const h = fixture({fetch:async path => path.endsWith("/expansion-batches/job") ? new Promise(resolve => {resolvePoll = resolve;}) : undefined});
  await h.context.testing.previewDialog(h.node);
  const jobs = h.elements.find(item => item["aria-label"] === "历史批次"); jobs.value = "job"; jobs.onchange(); await flush();
  const project = h.elements.find(item => item["aria-label"] === "分析项目"); project.value = "q"; project.onchange(); await flush();
  resolvePoll({...h.job,state:"running"}); await flush();
  assert.equal(h.elements.filter(item => item["aria-label"] === "第 1 段批次候选").length,0);
  assert.equal(h.timers.size,0); h.find("关闭").onclick();
  assert.equal(h.calls.filter(item => item.path.endsWith("/cancel")).length,0);
});

test("editing while a single preview is in flight keeps the newly typed text", async () => {
  let resolvePreview;
  const h = fixture({fetch:async path => path.endsWith("/expand") ? new Promise(resolve => {resolvePreview = resolve;}) : undefined});
  await h.context.testing.previewDialog(h.node);
  const pending = h.find("生成提示词（自动复用缓存）").onclick(); await flush();
  const text = h.elements.find(item => item["aria-label"] === "本段提示词"); text.value = "正在编辑的新原文"; text.oninput();
  resolvePreview({text:"迟到的生成结果",key:"key",source:"api"}); await pending;
  assert.equal(text.value,"正在编辑的新原文");
});

test("edits typed during batch apply remain unsaved after the earlier snapshot succeeds", async () => {
  let resolveApply;
  const h = fixture({fetch:async path => path.endsWith("/apply") ? new Promise(resolve => {resolveApply = resolve;}) : undefined});
  await h.context.testing.previewDialog(h.node);
  const jobs = h.elements.find(item => item["aria-label"] === "历史批次"); jobs.value = "job"; jobs.onchange(); await flush();
  await h.find("选中成功结果").onclick();
  const candidate = h.elements.filter(item => item["aria-label"] === "第 1 段批次候选").at(-1);
  candidate.value = "保存的编辑"; candidate.oninput();
  const pending = h.find("保存选中成功结果").onclick(); await flush();
  candidate.value = "保存后新输入的编辑"; candidate.oninput();
  resolveApply({revision:4,changed:[0],plan:{...h.plan,revision:4}}); await pending;
  h.context.window.confirm = () => false; h.find("关闭").onclick(); assert.equal(h.elements[0].removed,undefined);
});
