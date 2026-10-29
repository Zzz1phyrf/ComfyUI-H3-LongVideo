import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {progressText,busy,canvasProgressText} from '../web/progress.js';

test('MV uses the same visible two-line readonly progress component as talking',()=>{
 const source=readFileSync(new URL('../web/h3lv.js',import.meta.url),'utf8');
 const body=source.match(/function canvasProgress\(node, text\) \{([\s\S]*?)\n\}/)[1];
 const shared=readFileSync(new URL('../web/talking_progress.js',import.meta.url),'utf8').replace(/^export /gm,'');
 const update=vm.runInNewContext(`${shared}\nconst setWorkflowProgress=setTalkingStatus;\nfunction update(node,text){${body}}\nupdate`,{
  document:{createElement:()=>({style:{},setAttribute(){}})},setInterval(){return 1;},clearInterval(){}});
 const node={comfyClass:'H3LVUnified',widgets:[],size:[420,400],addWidget(type,name,value,callback,options){
  const widget={type,name,value,callback,options};this.widgets.push(widget);return widget;
 },addDOMWidget(name,type,element,options){const widget={name,type,element,options};this.widgets.push(widget);return widget;},setDirtyCanvas(){}};
 update(node,'第 1/8 段 · RH 生成中 · 本阶段 0分3秒 · 任务 123456 · 7/8 段完成');
 const view=node.widgets.find(w=>w.type==='h3talk-progress');
 assert.ok(view,'MV needs the same DOM progress region as talking');
 assert.equal(view.element.textContent,'第 1/8 段 · RH 生成中\n本阶段 0分3秒 · 7/8 段完成');
 assert.match(view.element.title,/123456/);
 assert.equal(view.options.serialize,false);assert.equal(view.options.hideOnZoom,false);
 assert.ok(view.options.getMinHeight()-2*view.options.margin>=52);
 const legacy=node.widgets.find(w=>w.name==='任务状态');assert.equal(legacy.hidden,true);assert.equal(legacy.disabled,true);
 update(node,'已完成 · 8/8 段完成');assert.equal(node.widgets.filter(w=>w.type==='h3talk-progress').length,1);
 assert.equal(view.element.textContent,'已完成 · 8/8 段完成');
 const unrelated={...node,comfyClass:'Other',widgets:[]};update(unrelated,'test');assert.equal(unrelated.widgets.length,0);
});

test('MV canvas summary keeps stage, elapsed time and actual completion count in two-line content',async()=>{
 const {canvasProgressText}=await import('../web/progress.js');assert.equal(typeof canvasProgressText,'function');
 const plan={run_status:'running',approved:true,progress:{stage:'rh_running',index:0,since:100},segments:[
  {job:{status:'running',task_id:'123'}},...Array.from({length:7},()=>({job:{status:'completed',video:'old.mp4'}}))]};
 const text=canvasProgressText(plan,103);assert.match(text,/第 1\/8 段.*RH生成中/);
 assert.match(text,/3秒/);assert.match(text,/7\/8 段完成/);assert.match(text,/任务 123/);
 const {talkingStatus}=await import('../web/talking_status.js');
 assert.equal(text,talkingStatus({...plan,controller_active:true,config:{backend:'RunningHub'},
  progress:{...plan.progress,stage:'running'}},103));
 assert.doesNotMatch(text,/方案已确认|状态：|\n/);
 plan.segments[1].needs_regeneration=true;assert.match(canvasProgressText(plan,103),/6\/8 段完成/);
 plan.run_status='completed';assert.equal(canvasProgressText(plan),'已完成 · 6/8 段完成');
});

function eventHarness(nodes,fetchApi) {
 const source=readFileSync(new URL('../web/h3lv.js',import.meta.url),'utf8');
 const request=source.slice(source.indexOf('async function request('),source.indexOf('function element('));
 const start=source.indexOf("api.addEventListener('h3lv-progress'");
 const listener=source.slice(start,source.indexOf('api.addEventListener("h3lv-final"',start));
 const updates=[];let handler;
 vm.runInNewContext(`${request}\n${listener}`,{app:{graph:{_nodes:nodes}},progressText,canvasProgressText,
  api:{fetchApi,addEventListener(name,callback){handler=callback;}},canvasProgress(node,text){updates.push({node,text});}});
 return {updates,send:detail=>handler({detail})};
}

test('MV stage events read saved progress with GET only and never submit or alter tasks',async()=>{
 const pid='a'.repeat(32),node={comfyClass:'H3LVUnified',properties:{h3lv_project:pid}};
 const other={comfyClass:'H3LVTalkingAvatar',properties:{h3lv_project:pid}};
 const reads=[],plan={run_status:'running',progress:{stage:'rh_running',index:0,since:100},segments:[
  {job:{status:'running',task_id:'EXISTING'}},{job:{status:'completed'}}]};
 const harness=eventHarness([node,other],async(path,options)=>{
  reads.push({path,options});return {ok:true,json:async()=>plan};
 });
 await harness.send({project_id:pid,count:2,progress:plan.progress});
 assert.equal(reads.length,1);assert.equal(reads[0].path,`/h3lv/project/${pid}`);
 assert.equal(reads[0].options.method,undefined);assert.equal(reads[0].options.body,undefined);
 assert.ok(harness.updates.every(update=>update.node===node));
 assert.match(harness.updates.at(-1).text,/1\/2 段完成/);assert.match(harness.updates.at(-1).text,/EXISTING/);
 await harness.send({project_id:'b'.repeat(32)});assert.equal(reads.length,1);
});

test('MV progress response cannot overwrite a node switched to another project',async()=>{
 const pid='a'.repeat(32),node={comfyClass:'H3LVUnified',properties:{h3lv_project:pid}};
 let respond;
 const response=new Promise(resolve=>{respond=resolve;});
 const harness=eventHarness([node],async()=>({ok:true,json:()=>response}));
 const pending=harness.send({project_id:pid,count:1,progress:{stage:'rh_running',index:0,since:100}});
 node.properties.h3lv_project='b'.repeat(32);
 respond({run_status:'completed',segments:[{job:{status:'completed'}}]});await pending;
 assert.equal(harness.updates.length,1);assert.doesNotMatch(harness.updates[0].text,/已完成/);
});
test('accepted locally is not reported as cloud submission',()=>{
 assert.match(progressText({run_status:'running'}),/本地已接收/);
 assert.match(progressText({run_status:'running',progress:{stage:'expansion',index:1,since:100},segments:[{},{}]},115),/尚未提交 RH/);
});
test('progress names segment, true cloud task and elapsed stage',()=>{
 const text=progressText({run_status:'running',progress:{stage:'rh_running',index:1,since:100},segments:[{},{job:{task_id:'123'}}]},175);
 assert.match(text,/第 2\/2 段/);assert.match(text,/1分15秒/);assert.match(text,/任务 123/);
});
test('stale running stage does not misrepresent finished project',()=>{
 assert.equal(progressText({run_status:'completed',progress:{stage:'rh_running'}}),'');
 assert.equal(busy({run_status:'merging'}),true);
});
test('redo preparation never presents the old take task as the new task',()=>{
 const text=progressText({run_status:'running',progress:{stage:'prepare',index:0,since:100},segments:[{job:{status:'completed',task_id:'OLD'}}]},102);
 assert.doesNotMatch(text,/OLD/);assert.match(text,/尚未提交/);
});
