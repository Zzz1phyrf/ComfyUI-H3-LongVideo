import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const oldId='9d01d98fc21743b9be70acc224239155';
function runtime(response={status:404,body:{code:'PROJECT_NOT_FOUND',error:'本机未找到该项目。'}}) {
  let extension;const queued=[],requests=[],statuses=[];
  const node={id:5,comfyClass:'H3LVTalkingAvatar',widgets:[{name:'project_id',value:oldId}],setDirtyCanvas(){}};
  const app={graph:{_nodes:[node],setDirtyCanvas(){}},registerExtension(value){extension=value;},
    queuePrompt(){throw new Error('Must not execute the whole sampling graph');},
    async graphToPrompt(){return {output:{'5':{class_type:'H3LVTalkingAvatar',inputs:{project_id:node.widgets[0].value}}}};},
    async h3lvConfirmDialog(){return true;}};
  const api={addEventListener(){},async fetchApi(path){requests.push(path);return {ok:response.status===200,status:response.status,async json(){return response.body;}};},
    async queuePrompt(...args){queued.push(args);}};
  const source=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8').replace(/^import[^\n]*\n/gm,'');
  const context={app,api,setTalkingStatus:(_node,text)=>statuses.push(text),talkingStatus:()=> 'existing project',
    setInterval:()=>1,clearInterval(){},window:{addEventListener(){}},
    syncHiddenWidgetConnections(){},alignProjectNode(){}};
  vm.runInNewContext(source+'\nglobalThis.actions={analyze,refreshStatus,showPanel};',context);
  return {app,node,queued,requests,statuses,actions:context.actions,extension};
}

test('reanalysis of imported foreign project queues fresh preparation',async()=>{
  const r=runtime();await r.actions.analyze(r.node);
  assert.equal(r.queued.length,1);
  assert.equal(r.queued[0][1].output['5'].inputs.project_id,'');
  assert.equal(r.node.widgets[0].value,'');
});
test('ordinary Run does not submit missing foreign project ID',async()=>{
  const r=runtime();r.extension.setup();await r.app.queuePrompt(0);
  assert.equal(r.queued.length,1);assert.equal(r.queued[0][1].output['5'].inputs.project_id,'');
});
test('polling releases only confirmed missing binding and does not repeat requests',async()=>{
  const r=runtime();await r.actions.refreshStatus(r.node);await r.actions.refreshStatus(r.node);
  assert.equal(r.requests.length,1);assert.equal(r.node.widgets[0].value,'');assert.equal(r.queued.length,0);
});
test('open panel with missing binding prepares own project without selecting newest unrelated project',async()=>{
  const r=runtime();await r.actions.showPanel(r.node);
  assert.equal(r.queued.length,1);assert.equal(r.queued[0][1].output['5'].inputs.project_id,'');
  assert.ok(!r.requests.includes('/h3talk/projects'));
});
test('existing local binding is retained by ordinary Run',async()=>{
  const r=runtime({status:200,body:{id:oldId,controller_active:false}});r.extension.setup();await r.app.queuePrompt(0);
  assert.equal(r.node.widgets[0].value,oldId);assert.equal(r.queued[0][1].output['5'].inputs.project_id,oldId);
});
test('active project still prevents reanalysis',async()=>{
  const r=runtime({status:200,body:{id:oldId,controller_active:true}});
  await assert.rejects(r.actions.analyze(r.node),/仍在生成/);assert.equal(r.queued.length,0);assert.equal(r.node.widgets[0].value,oldId);
});
test('permission, corrupt data and unrelated 404 errors do not discard binding',async()=>{
  for(const response of [{status:500,body:{error:'permission denied'}},{status:400,body:{error:'corrupt JSON'}},{status:404,body:{error:'route unavailable'}}]) {
    const r=runtime(response);await assert.rejects(r.actions.analyze(r.node));
    assert.equal(r.node.widgets[0].value,oldId);assert.equal(r.queued.length,0);
  }
});
