import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {referenceBranch,captureReference} from '../web/reference_sync.js';

const referenceCode=fs.readFileSync(new URL('../web/reference_sync.js',import.meta.url),'utf8').replace(/^export /gm,'');
const captureIdRuntime=scope=>vm.runInNewContext(referenceCode+';referenceCaptureId;',scope);

function graph() {return {output:{
  '1':{class_type:'LoadImage',inputs:{image:'new.png'}},
  '2':{class_type:'ImageScale',inputs:{image:['1',0],width:768}},
  '3':{class_type:'LoadAudio',inputs:{audio:'speech.wav'}},
  '5':{class_type:'H3LVTalkingAvatar',inputs:{reference_image:['2',0],audio:['3',0]}},
  '6':{class_type:'SamplerCustom',inputs:{positive:['5',1]}},
}};}
test('capture only follows the current image branch, including transforms',()=>{
  const branch=referenceBranch(graph(),'5','unique-capture');
  assert.deepEqual(Object.keys(branch.snapshot.output),['1','2','unique-capture']);
  assert.deepEqual(branch.snapshot.output['unique-capture'].inputs.image,['2',0]);
  const changed=graph();changed.output['1'].inputs.image='different.png';
  assert.notEqual(branch.signature,referenceBranch(changed,'5').signature);
});
test('reject broken or circular image input',()=>{
  const broken=graph();delete broken.output['1'];
  assert.throws(()=>referenceBranch(broken,'5'),/缺失/);
  const loop=graph();loop.output['2'].inputs.image=['5',1];
  assert.throws(()=>referenceBranch(loop,'5'),/循环/);
});
function api(queue) {
  const events=new Map();return {
    addEventListener(name,fn){events.set(name,fn);},removeEventListener(name){events.delete(name);},events,
    emit(name,detail){events.get(name)?.({detail});},queuePrompt:queue,
  };
}
test('unrelated output cannot become reference; successful event before queue reply works',async()=>{
  const branch=referenceBranch(graph(),'5','capture');
  const a=api(async()=>{
    a.emit('executed',{node:'273',output:{images:[{filename:'mv.mp4',type:'output'}]}});
    a.emit('executed',{node:'capture',output:{images:[{filename:'new.png',type:'temp'}]}});
    return {prompt_id:'p'};
  });
  assert.equal((await captureReference(a,branch)).filename,'new.png');assert.equal(a.events.size,0);
});
test('early upstream error, queue rejection, and timeout fail closed and remove listeners',async()=>{
  for(const mode of ['upstream','queue','timeout']) {
    const a=api(async()=>{
      if(mode==='queue')throw new Error('validation failed');
      if(mode==='upstream')a.emit('execution_error',{prompt_id:'p',exception_message:'decode failed'});
      return {prompt_id:'p'};
    });
    await assert.rejects(captureReference(a,referenceBranch(graph(),'5'),{timeoutMs:10}));
    assert.equal(a.events.size,0);
  }
});
test('closing while waiting for capture cancels continuation and removes listeners',async()=>{
  const control=new AbortController(),a=api(async()=>({prompt_id:'queued'}));
  const result=captureReference(a,referenceBranch(graph(),'5'),{signal:control.signal});
  control.abort();await assert.rejects(result,/取消/);assert.equal(a.events.size,0);
});

const parent=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8');
const syncCode=parent.slice(parent.indexOf('async function syncReference('),parent.indexOf('function hideUnusedLegacyMask('));
function syncRuntime({confirm=true,response='updated',busy=false,changeDuringCapture=false,switchAfterCommit=false,browserCrypto=globalThis.crypto}={}) {
  const id='a'.repeat(32),newId='b'.repeat(32),calls=[];
  const owner={id:5,widgets:[{name:'project_id',value:id}],setDirtyCanvas(){}};
  const plan={id,config:{talking_profile:'fl2va-flat-rh-v1'},editing_fingerprint:'old',controller_active:busy};
  const current=graph(),captureIds=[];let captures=0;
  const app={graph:{_nodes:[owner]},graphToPrompt:async()=>current,h3lvConfirmDialog:async()=>confirm};
  const request=async(path,body)=>{
    calls.push({path,body});
    if(response==='confirmation_required' && !body.confirmed_sha256)return {status:response,candidate_sha256:'candidate',plan};
    if(switchAfterCommit)app.graph._nodes=[];
    return {status:response==='confirmation_required'?'forked':response,plan:{...plan,id:response==='confirmation_required'?newId:id}};
  };
  const capture=async(api,branch)=>{captures++;captureIds.push(branch.id);if(changeDuringCapture)current.output['1'].inputs.image='changed.png';return {filename:'capture.png',type:'temp'};};
  const fn=Function('app','api','projectId','currentProject','referenceBranch','captureReference','request','status','talkingStatus','syncTalkingVideo','showMessage','crypto','referenceCaptureId',
    syncCode+';return syncReference;')(app,{},node=>node.widgets[0].value,async()=>plan,referenceBranch,capture,request,()=>{},()=>'',()=>{},async()=>{},browserCrypto,captureIdRuntime({crypto:browserCrypto}));
  return {fn,owner,app,calls,captureIds,id,newId,get captures(){return captures;}};
}

test('actual node reference sync works over HTTP without randomUUID',async()=>{
  const browserCrypto={getRandomValues:bytes=>{bytes.fill(42);return bytes;}};
  const r=syncRuntime({browserCrypto});
  for(let i=0;i<3;i++)assert.equal((await r.fn(r.owner)).status,'updated');
  assert.equal(r.captures,3);assert.equal(r.calls.length,3);
  assert.equal(new Set(r.captureIds).size,3);
  assert.ok(r.captureIds.every(id=>id.startsWith('h3talk-reference-')));
});

test('actual node reference sync also works without browser crypto',async()=>{
  const r=syncRuntime({browserCrypto:null});
  assert.equal((await r.fn(r.owner)).status,'updated');
  assert.equal(r.captures,1);assert.equal(r.owner.__talkingReferenceSync,false);
});

test('non-callable randomUUID does not block reference sync',async()=>{
  const r=syncRuntime({browserCrypto:{randomUUID:'unavailable'}});
  assert.equal((await r.fn(r.owner)).status,'updated');assert.equal(r.captures,1);
});

test('secure browser IDs prefer the native UUID generator',()=>{
  let calls=0;
  const createId=captureIdRuntime({crypto:{randomUUID(){calls++;return 'native-uuid';},getRandomValues(){throw new Error('native UUID available');}}});
  assert.ok(createId().endsWith('-native-uuid'));assert.equal(calls,1);
});

test('fallback IDs stay distinct with a frozen clock and repeated random output',()=>{
  for(const crypto of [undefined,{getRandomValues:bytes=>{bytes.fill(42);return bytes;}}]) {
    const createId=captureIdRuntime({crypto,Date:{now:()=>1000},Math:{random:()=>.5}});
    const ids=Array.from({length:100},()=>createId());
    assert.equal(new Set(ids).size,100);
  }
});

test('concurrent HTTP capture IDs match only their own executed event',async()=>{
  const createId=captureIdRuntime({crypto:{getRandomValues:bytes=>{bytes.fill(42);return bytes;}}});
  const ids=[createId(),createId()],queued=[];
  const a=new EventTarget();
  a.queuePrompt=async(index,snapshot,options)=>{queued.push(options.partialExecutionTargets[0]);return {prompt_id:queued.length.toString()};};
  const captures=ids.map(id=>captureReference(a,referenceBranch(graph(),'5',id)));
  await Promise.resolve();
  assert.deepEqual(queued,ids);
  for(const i of [1,0]) {
    const event=new Event('executed');
    event.detail={node:ids[i],output:{images:[{filename:`reference-${i}.png`,type:'temp'}]}};
    a.dispatchEvent(event);
  }
  assert.deepEqual((await Promise.all(captures)).map(image=>image.filename),['reference-0.png','reference-1.png']);
});
test('actual node sync updates unrendered project and preserves its binding',async()=>{
  const r=syncRuntime();const result=await r.fn(r.owner);
  assert.equal(result.status,'updated');assert.equal(r.owner.widgets[0].value,r.id);
  assert.equal(r.calls[0].body.expected_fingerprint,'old');assert.equal(r.captures,1);
  assert.equal(r.owner.__talkingReferenceSync,false);
});
test('existing results require confirmation; cancel never forks, acceptance rebinds to new project',async()=>{
  for(const confirm of [false,true]) {
    const r=syncRuntime({response:'confirmation_required',confirm});
    assert.equal((await r.fn(r.owner)).status,confirm?'forked':'cancelled');
    assert.equal(r.calls.length,confirm?2:1);
    assert.equal(r.owner.widgets[0].value,confirm?r.newId:r.id);
    if(confirm)assert.equal(r.calls[1].body.confirmed_sha256,'candidate');
  }
});
test('active generation, changed picture during capture, or wrong tab cannot update reference',async()=>{
  for(const options of [{busy:true},{changeDuringCapture:true},{wrongTab:true}]) {
    const r=syncRuntime(options);if(options.wrongTab)r.app.graph._nodes=[];
    await assert.rejects(r.fn(r.owner));assert.equal(r.calls.length,0);
  }
});
test('switching tabs after server commit reports saved project without binding or starting another workflow',async()=>{
  const r=syncRuntime({response:'confirmation_required',switchAfterCommit:true});
  const result=await r.fn(r.owner);
  assert.equal(result.status,'detached');assert.equal(result.plan.id,r.newId);
  assert.equal(r.owner.widgets[0].value,r.id);
});

const panel=fs.readFileSync(new URL('../web/talking_panel.js',import.meta.url),'utf8');
const runCode=panel.slice(panel.indexOf('  async function syncReferenceForRun('),panel.indexOf("  $('audio').src="));
test('actual panel Run syncs before submission and never submits old project on cancel/fork/failure',async()=>{
  for(const outcome of ['updated','unchanged','forked','cancelled','failed','missing','detached']) {
    const calls=[];
    const win={frameElement:outcome==='missing'?{}:{__h3talkSyncReference:async()=>{
      calls.push('sync');if(outcome==='failed')throw new Error('capture failed');
      return {status:outcome,plan:{editing_fingerprint:'verified'}};
    }}};
    const run=Function('window','perform','apply','fail','location','URLSearchParams',
      "let pending=false,dirty=false,promptDirty=false,actionError='',referenceState='',plan={},actionRevision=0;const pid='p',anchored=()=>true;"+
      runCode+';return syncReferenceForRun;')(win,async(action,body)=>calls.push([action,body]),()=>{},()=>{}, {search:''},URLSearchParams);
    await run(true);
    const submitted=calls.filter(x=>Array.isArray(x));
    assert.equal(submitted.length,['updated','unchanged'].includes(outcome)?1:0,outcome);
    if(submitted.length){assert.equal(calls[0],'sync');assert.equal(submitted[0][1].expected_fingerprint,'verified');}
  }
});

test('old status poll cannot overwrite a reference update that finishes while the poll waits',async()=>{
  const code=panel.slice(panel.indexOf('  async function refresh()'),panel.indexOf('  async function syncReferenceForRun('));
  let finish;const updates=[];
  const runtime=Function('window','request','apply','fail',
    'let pending=false,actionRevision=0,referenceState,plan={},promptVersions=null;const pid="p",anchored=()=>true;'+code+
    ';return {refresh,changed:()=>{actionRevision++;}};')({frameElement:{}},()=>new Promise(r=>finish=r),p=>updates.push(p),e=>{throw e;});
  const poll=runtime.refresh();runtime.changed();finish({old:true});await poll;
  assert.equal(updates.length,0);
});

test('real close handler cancels capture and closes without waiting for generation',async()=>{
  const code=panel.slice(panel.indexOf('  async function closePanel('),panel.indexOf('  function syncHost('));
  let cancelled=0,closed=0;
  const win={frameElement:{__h3talkCancelReference:()=>{cancelled++;return true;}}};
  const close=Function('window','fail','let pending=true,closing=false;'+code+';return closePanel;')(win,e=>{throw e;});
  await close({__h3talkOriginalClose:()=>closed++});assert.equal(cancelled,1);assert.equal(closed,1);
});
