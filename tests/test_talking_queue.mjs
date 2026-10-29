import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const {syncHiddenWidgetConnections}=await import('data:text/javascript,'+encodeURIComponent(fs.readFileSync(new URL('../web/widget_visibility.js',import.meta.url),'utf8')));

const source=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8').replace(/^import[^\n]*\n/gm,'');
function setup() {
  const snapshot={output:{
    '1':{class_type:'LoadAudio',inputs:{}},
    '5':{class_type:'H3LVTalkingRH',inputs:{audio:['1',0],project_id:'existing-project'}},
    '127':{class_type:'UNETLoader',inputs:{unet_name:'user-model'}},
    '270':{class_type:'SamplerCustom',inputs:{model:['127',0],latent_image:['5',3],cfg:1.2}},
    '273':{class_type:'SaveVideo',inputs:{video:['270',0]}},
    '9500':{class_type:'H3LVTalkingMerge',inputs:{project_id:['5',0]}},
    '9501':{class_type:'SaveVideo',inputs:{video:['9500',0]}},
    'other':{class_type:'PreviewImage',inputs:{}},
  },workflow:{nodes:['complete canvas metadata']}};
  let extension;const originalCalls=[],partialCalls=[];
  const app={graph:{_nodes:[{id:5,comfyClass:'H3LVTalkingRH'}]},registerExtension:e=>extension=e,
    async graphToPrompt(){return snapshot;},async queuePrompt(...args){originalCalls.push(args);return 'native';}};
  const api={addEventListener(){},async queuePrompt(...args){partialCalls.push(args);return 'prepared';}};
  vm.runInNewContext(source,{syncHiddenWidgetConnections,app,api,setInterval:()=>0,clearInterval(){},window:{addEventListener(){}}});extension.setup();
  return {app,snapshot,originalCalls,partialCalls,extension};
}

test('native Run executes only preparation while retaining the full capture graph',async()=>{
  const {app,snapshot,originalCalls,partialCalls}=setup();const before=JSON.stringify(snapshot);
  assert.equal(await app.queuePrompt(0,3),'prepared');
  assert.equal(originalCalls.length,0);assert.equal(partialCalls.length,1);
  assert.deepEqual(Array.from(partialCalls[0][2].partialExecutionTargets),['5']);
  assert.equal(partialCalls[0][1],snapshot);assert.equal(JSON.stringify(snapshot),before);
  assert.equal(snapshot.output['270'].inputs.cfg,1.2);
});

test('public compact node uses the same approval gate and explicit final merge branch',async()=>{
  const {app,snapshot,partialCalls,originalCalls}=setup();
  app.graph._nodes[0].comfyClass='H3LVTalkingAvatar';snapshot.output['5'].class_type='H3LVTalkingAvatar';
  await app.queuePrompt(0);assert.equal(partialCalls.length,1);
  await app.queuePrompt(0,1,{queueNodeIds:['9501']});assert.equal(originalCalls.length,1);
});

test('selected sampler or raw output cannot bypass preparation and approval',async()=>{
  for(const options of [{queueNodeIds:['270']},{partialExecutionTargets:['273']},[{nodeId:273}],{queueNodeIds:['9501','273']}]) {
    const {app,partialCalls,originalCalls}=setup();await app.queuePrompt(0,1,options);
    assert.equal(originalCalls.length,0);assert.deepEqual(Array.from(partialCalls[0][2].partialExecutionTargets),['5']);
  }
});

test('explicit final merge and save execute only their selected native branch',async()=>{
  for(const target of ['9500','9501']) {
    const {app,partialCalls,originalCalls}=setup();const options={queueNodeIds:[target]};
    assert.equal(await app.queuePrompt(0,1,options),'native');
    assert.equal(partialCalls.length,0);assert.equal(originalCalls[0][2],options);
  }
});

test('explicit unrelated targets and graphs belonging to MV are left unchanged',async()=>{
  const {app,partialCalls,originalCalls}=setup();
  await app.queuePrompt(0,1,{queueNodeIds:['other']});
  app.graph._nodes.push({id:6,comfyClass:'H3LVUnified'});await app.queuePrompt(0);
  app.graph._nodes=[];await app.queuePrompt(0);
  assert.equal(partialCalls.length,0);assert.equal(originalCalls.length,3);
});

test('guard setup is idempotent and missing owner stops rather than running all outputs',async()=>{
  const {app,snapshot,partialCalls,originalCalls,extension}=setup();
  extension.setup();await app.queuePrompt(0);assert.equal(partialCalls.length,1);
  delete snapshot.output['5'];await assert.rejects(app.queuePrompt(0),/Talking Avatar/);
  assert.equal(originalCalls.length,0);
});
