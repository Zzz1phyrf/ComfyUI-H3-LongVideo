import {alignProjectNode} from '../web/node_ui.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const {syncHiddenWidgetConnections}=await import('data:text/javascript,'+encodeURIComponent(fs.readFileSync(new URL('../web/widget_visibility.js',import.meta.url),'utf8')));

function setup(name='H3LVTalkingRH') {
  let extension;
  const app={registerExtension:value=>extension=value,graph:{setDirtyCanvas(){}}};
  const source=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8').replace(/^import[^\n]*\n/gm,'');
  const progress=fs.readFileSync(new URL('../web/talking_progress.js',import.meta.url),'utf8').replace(/^export /gm,'');
  vm.runInNewContext(progress+'\n'+source,{alignProjectNode,syncHiddenWidgetConnections,app,api:{clientId:'test-client'},
    document:{createElement:()=>({style:{},setAttribute(){}})},setInterval(){return 1;},clearInterval(){}});
  function Node(){this.widgets=[{name:'project_id',value:''},{name:'backend',value:'本地'}];this.inputs=[{name:'audio',link:null},{name:'reference_image',link:null},{name:'person_mask',link:null}];}
  Node.prototype.removeInput=function(index){this.inputs.splice(index,1);};
  Node.prototype.removeOutput=function(index){this.outputs.splice(index,1);};
  Node.prototype.addWidget=function(type,name,value,callback){const widget={type,name,value,callback};this.widgets.push(widget);return widget;};
  Node.prototype.addDOMWidget=function(name,type,element,options){const widget={name,type,element,options};this.widgets.push(widget);return widget;};
  extension.beforeRegisterNodeDef(Node,{name});
  const node=new Node();node.comfyClass=name;return node;
}

test('public eight-widget node retains local backend and exposes cloud fields only in RH',()=>{
  const node=setup('H3LVTalkingAvatar');
  node.widgets.push({name:'workflow_id',value:'123'},{name:'fixed_prompt',value:'person and scene'});
  node.onConfigure({widgets_values:Array(8).fill('')});
  assert.equal(node.widgets.find(w=>w.name==='backend').value,'本地');
  assert.equal(node.widgets.find(w=>w.name==='workflow_id').hidden,true);
  assert.equal(node.widgets.find(w=>w.name==='fixed_prompt').hidden,false);
  node.onNodeCreated();
  const backend=node.widgets.find(w=>w.name==='backend');backend.value='RunningHub';backend.callback();
  assert.equal(node.widgets.find(w=>w.name==='workflow_id').hidden,false);
});

test('old public MP widget does not shift the saved seed, project or prompt on configure',()=>{
 const node=setup('H3LVTalkingAvatar');
 node.widgets.push({name:'seed',value:1},{name:'fixed_prompt',value:'wrong'});
 node.onConfigure({widgets_values:['RunningHub','123','海外版','ultra',.8,9260,'','saved prompt',null]});
 assert.equal(node.widgets.find(w=>w.name==='seed').value,9260);
 assert.equal(node.widgets.find(w=>w.name==='project_id').value,'');
 assert.equal(node.widgets.find(w=>w.name==='fixed_prompt').value,'saved prompt');
});

test('old public seed widget does not shift project and prompt after sampler owns seed',()=>{
 const node=setup('H3LVTalkingAvatar');node.widgets.push({name:'fixed_prompt',value:'wrong'});
 node.onConfigure({widgets_values:['本地','123','海外版','ultra',9260,'saved-project','saved prompt']});
 assert.equal(node.widgets.find(w=>w.name==='project_id').value,'saved-project');
 assert.equal(node.widgets.find(w=>w.name==='fixed_prompt').value,'saved prompt');
});

test('public single image removes legacy background ports and keeps normal labels',()=>{
 const node=setup('H3LVTalkingAvatar');
 node.inputs.push({name:'background_image',label:'背景图（可选）',link:null});
 node.outputs=[{name:'reference_image'},{name:'background_image'},{name:'frames'}];
 node.inputs.find(i=>i.name==='reference_image').label='人物图（可带背景）';
 node.onConfigure({widgets_values:Array(8).fill('')});
 assert.equal(node.inputs.find(i=>i.name==='reference_image').label,undefined);
 assert.equal(node.inputs.some(i=>i.name==='background_image'),false);
 assert.deepEqual(node.outputs.map(i=>i.name),['reference_image','frames']);
 node.onConfigure({widgets_values:Array(8).fill('')});
 assert.equal(node.outputs.length,2);
});

test('old eight-widget RH canvas retains its backend',()=>{
  const node=setup();node.onConfigure({widgets_values:Array(8).fill('')});
  assert.equal(node.widgets.find(w=>w.name==='backend').value,'RunningHub');
});

test('new talking-head canvas keeps local as the default',()=>{
  const node=setup();node.onConfigure({widgets_values:Array(15).fill('')});
  assert.equal(node.widgets.find(w=>w.name==='backend').value,'本地');
});

test('new nodes hide the legacy optional person mask port',()=>{
  const node=setup();node.onNodeCreated();
  assert.equal(node.inputs.some(i=>i.name==='person_mask'),false);
});

test('read-only task status uses a visible DOM region instead of disabled text drawing',()=>{
  const node=setup();node.onNodeCreated();
  const widget=node.widgets.find(w=>w.name==='任务状态');
  assert.equal(widget.disabled,true);assert.equal(widget.serialize,false);
  assert.equal(widget.hidden,true);
  const view=node.widgets.find(w=>w.type==='h3talk-progress');
  assert.match(view.element.textContent,/尚未准备项目/);
  assert.equal(view.options.hideOnZoom,false);assert.equal(view.serialize,false);
});

test('configured old connected mask is retained for project compatibility',()=>{
  const node=setup();node.inputs.find(i=>i.name==='person_mask').link=42;
  node.onConfigure({inputs:node.inputs,widgets_values:Array(15).fill('')});
  assert.equal(node.inputs.find(i=>i.name==='person_mask').link,42);
});

test('approved native merge runs do not reopen the segment modal',()=>{
  const node=setup();
  assert.doesNotThrow(()=>node.onExecuted({talking_rh_project:['a'.repeat(32)],talking_rh_open_review:[false]}));
  assert.equal(node.widgets.find(w=>w.name==='project_id').value,'a'.repeat(32));
});

test('canvas mode hides managed backend model selectors without deleting their values',()=>{
  const node=setup();node.widgets.push({name:'execution_mode',value:'画布'},{name:'local_model',value:'keep-model'});
  node.onConfigure({widgets_values:['','本地','画布','keep-model']});
  const model=node.widgets.find(w=>w.name==='local_model');
  assert.equal(model.hidden,true);assert.equal(model.value,'keep-model');
  assert.equal(model.computeSize()[1],-4);
});

test('missing execution mode preserves old managed workflows',()=>{
  const node=setup();node.widgets.push({name:'execution_mode',value:'画布'});
  node.onConfigure({widgets_values:['','本地']});
  assert.equal(node.widgets.find(w=>w.name==='execution_mode').value,'内置');
});

test('RH controller hides local model selectors and retains cloud settings',()=>{
  const node=setup();node.widgets.find(w=>w.name==='backend').value='RunningHub';
  node.widgets.push({name:'local_model',value:'keep-model'},{name:'workflow_id',value:'123'});
  node.onNodeCreated();
  assert.equal(node.widgets.find(w=>w.name==='local_model').hidden,true);
  assert.equal(node.widgets.find(w=>w.name==='workflow_id').hidden,false);
});

test('external prompt input stays hidden as a widget in independent mode',()=>{
  const node=setup();node.widgets.push({name:'continuation_mode',value:'独立窗口（自然动作）'},
    {name:'fixed_prompt',value:'kept'});
  node.inputs.push({name:'fixed_prompt',link:12});node.onNodeCreated();
  assert.equal(node.widgets.find(w=>w.name==='fixed_prompt').hidden,true);
  assert.equal(node.inputs.find(i=>i.name==='fixed_prompt').link,12);
});

test('old serialized node never silently adopts independent windows',()=>{
  const node=setup();while(node.widgets.length<17)node.widgets.push({name:'padding'+node.widgets.length});
  node.widgets.push({name:'continuation_mode',value:'独立窗口（自然动作）'});
  node.onConfigure({widgets_values:Array(17).fill('')});
  assert.equal(node.widgets[17].value,'旧版latent续接');
  node.widgets[17].value='独立窗口（自然动作）';
  node.onConfigure({widgets_values:[...Array(17).fill(''),null,null]});
  assert.equal(node.widgets[17].value,'旧版latent续接');
});
