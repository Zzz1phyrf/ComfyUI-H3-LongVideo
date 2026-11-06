import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as timeline from '../web/timeline.js';

const panel=await readFile(new URL('../web/talking_panel.js',import.meta.url),'utf8');
function valid(cuts,duration) {
  let start=0;
  for(const time of cuts) {
    const end=Math.round(time*24),length=end-start;
    assert.ok(length>=107 && length<=345 && (length-5)%17===0,`invalid length ${length}`);
    start=end;
  }
  const tail=Math.ceil(duration*24-1e-6)-start;
  assert.ok(tail>0 && tail<=345);
}

test('screenshot cuts align before saving without changing the total duration',()=>{
  const cuts=timeline.alignAnchoredCuts([9.417,18.739,29.667],39.340417);
  valid(cuts,39.340417);
  assert.deepEqual(cuts.map(t=>Math.round(t*24)),[226,452,712]);
});

test('split and remove align the remaining boundaries, accepted clocks stay identical',()=>{
  const duration=1288192/24000,accepted=[226,367,508,666,807,914,1072].map(f=>f/24);
  assert.deepEqual(timeline.alignAnchoredCuts(accepted,duration),accepted);
  const split=[...accepted];split.splice(0,0,4.5);
  valid(timeline.alignAnchoredCuts(split,duration),duration);
  const removed=[...accepted];removed.splice(1,1);
  valid(timeline.alignAnchoredCuts(removed,duration),duration);
  assert.throws(()=>timeline.alignAnchoredCuts([2,3],5),/切点|分段/);
});

test('actual drag handler cannot leave the screenshot off-grid cut in the draft',()=>{
  const code=panel.slice(panel.indexOf('  function moveCut('),panel.indexOf('  function renderRows('));
  const rows=[226/24,452/24,712/24,39.340417].map(t=>({end:{value:String(t)}}));
  const normalize=()=>{
    const cuts=timeline.alignAnchoredCuts(rows.slice(0,-1).map(r=>Number(r.end.value)),39.340417);
    cuts.forEach((t,i)=>rows[i].end.value=String(t));return true;
  };
  const move=Function('rows','cutBlocked','refreshTimes','mark','normalizeCutDraft','fail',code+';return moveCut;')(
    rows,()=>false,()=>{},()=>{},normalize,error=>{throw error;});
  move(1,18.739);
  valid(rows.slice(0,-1).map(r=>Number(r.end.value)),39.340417);
});

test('close can discard unsaved edits, cancel retains them, pending save blocks close',async()=>{
  const start=panel.indexOf('  async function closePanel(');
  assert.ok(start>=0,'close must offer a way out of invalid unsaved drafts');
  const code=panel.slice(start,panel.indexOf('  function syncHost()',start));
  for(const answer of [false,true]) {
    let closed=0,asked=0;
    const win={h3talkHasUnsavedChanges:()=>true,parent:{comfyAPI:{app:{app:{h3lvConfirmDialog:async()=>{asked++;return answer;}}}}}};
    const close=Function('window','pending','fail','let closing=false;'+code+';return closePanel;')(win,false,error=>{throw error;});
    await close({__h3talkOriginalClose:()=>closed++});
    assert.equal(asked,1);assert.equal(closed,Number(answer));
  }
  let closed=0,error='';
  const close=Function('window','pending','fail','let closing=false;'+code+';return closePanel;')({},true,e=>error=e.message);
  await close({__h3talkOriginalClose:()=>closed++});assert.equal(closed,0);assert.match(error,/完成/);
});
