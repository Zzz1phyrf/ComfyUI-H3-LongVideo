import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function setup() {
  const source=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8');
  const helper=new URL('../web/talking_progress.js',import.meta.url);
  const progress=fs.existsSync(helper)?fs.readFileSync(helper,'utf8').replace(/^export /gm,''):'';
  const status=source.slice(source.indexOf('function status('),source.indexOf('async function refreshStatus('));
  const ticks=[];let dirty=0;
  const document={createElement:()=>({style:{},setAttribute(){},textContent:''})};
  const node={size:[420,400],widgets:[{name:'project_id',value:'a'.repeat(32)}],comfyClass:'H3LVTalkingAvatar',
    addWidget(type,name,value){const widget={type,name,value};this.widgets.push(widget);return widget;},
    addDOMWidget(name,type,element,options){const widget={name,type,element,options};this.widgets.push(widget);return widget;},
    computeSize(){return [420,470];},setSize(size){this.size=size;},setDirtyCanvas(){dirty++;}};
  const api=vm.runInNewContext(`${progress}\n${status}\n({status,${progress?'syncWorkflowStatus,':''}})`,{document,setInterval(fn){ticks.push(fn);return ticks.length;},clearInterval(){}});
  return {node,api,ticks,document,dirty:()=>dirty};
}

test('workflow status is a visible readonly DOM widget, independent of disabled text drawing and zoom',()=>{
  const {node,api,ticks}=setup();api.status(node,'第 2/8 段 · RH生成中 · 32秒 · 任务 123456 · 1/8 段完成');
  const view=node.widgets.find(w=>w.type==='h3talk-progress');assert.ok(view,'canvas needs a visible progress widget');
  assert.equal(view.options.serialize,false);assert.equal(view.options.hideOnZoom,false);
  assert.ok(view.options.getMinHeight()-2*view.options.margin>=52,'two lines and padding must fit inside the actual DOM area');
  assert.match(view.element.textContent,/第 2\/8 段.*RH生成中/);assert.match(view.element.textContent,/32秒.*1\/8 段完成/s);
  assert.doesNotMatch(view.element.textContent,/123456/);assert.match(view.element.title,/123456/);
  const legacy=node.widgets.find(w=>w.name==='任务状态');assert.equal(legacy.hidden,true);
  legacy.value='已完成 · 8/8 段完成';ticks[0]();assert.match(view.element.textContent,/已完成.*8\/8/s);
  api.status(node,'已暂停 · 1/8 段完成');assert.equal(node.widgets.filter(w=>w.type==='h3talk-progress').length,1);
});

test('panel status bridge updates only its matching workflow node and queues nothing',()=>{
  const {node,api,document}=setup();assert.equal(typeof api.syncWorkflowStatus,'function');
  const other={...node,widgets:[{name:'project_id',value:'b'.repeat(32)}]};
  api.syncWorkflowStatus({document,comfyAPI:{app:{app:{graph:{_nodes:[node,other]}}}}},'a'.repeat(32),'已完成 · 8/8 段完成');
  assert.ok(node.widgets.some(w=>w.type==='h3talk-progress'));assert.equal(other.widgets.length,1);
});
