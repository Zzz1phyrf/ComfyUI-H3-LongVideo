import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const source = fs.readFileSync(new URL("../web/panel_drag.js", import.meta.url),"utf8").replaceAll("export ","");
const context = {};vm.runInNewContext(source+"\nglobalThis.clamp = clampPanelPosition;",context);
const plain = value => JSON.parse(JSON.stringify(value));
test("drag position stays fully inside desktop viewport",()=>{
  assert.deepEqual(plain(context.clamp(-100,-200,1296,810,1440,900)),{left:8,top:8});
  assert.deepEqual(plain(context.clamp(9999,9999,1296,810,1440,900)),{left:136,top:82});
});
test("resize clamps a previous desktop position into a narrow viewport",()=>{
  assert.deepEqual(plain(context.clamp(360,60,374,828,390,844)),{left:8,top:8});
  assert.deepEqual(plain(context.clamp(360,60,390,844,390,844)),{left:0,top:0});
});
