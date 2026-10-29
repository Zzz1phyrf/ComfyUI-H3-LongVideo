import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {alignProjectNode} from '../web/node_ui.js';
import {syncHiddenWidgetConnections} from '../web/widget_visibility.js';
import {setTalkingStatus} from '../web/talking_progress.js';

test('real MV and talking hooks plus RH settings produce matching actions and progress in either load order',async()=>{
  const common=['RH API 设置','检查 RH 配置（不生成）','重新分析并分段','打开分段与生成控制','下载错误日志'];
  for(const settingsFirst of [true,false])for(const name of ['H3LVUnified','H3LVTalkingAvatar']) {
    const extensions=[],reads=[];
    const app={registerExtension:value=>extensions.push(value),graph:{setDirtyCanvas(){}}};
    const document={querySelector:()=>({}),createElement:()=>({style:{},setAttribute(){}}),
      defaultView:{setInterval:()=>1,clearInterval(){}}};
    const update=(node,text)=>setTalkingStatus(node,text,document);
    const context={alignProjectNode,syncHiddenWidgetConnections,app,document,
      setTalkingStatus:update,setWorkflowProgress:update,
      setTimeout:fn=>fn(),api:{fetchApi(){reads.push('unexpected');throw new Error('No requests on creation');}}};
    const files=name==='H3LVUnified'?['h3lv.js']:['talking_rh.js'];
    settingsFirst?files.unshift('rh_settings.js'):files.push('rh_settings.js');
    for(const file of files) {
      const source=fs.readFileSync(new URL('../web/'+file,import.meta.url),'utf8')
        .replace(/^import[^\n]*\n/gm,'').replace('new URL("./h3lv.css", import.meta.url).href','"h3lv.css"');
      vm.runInNewContext(source,context);
    }
    function Node(){this.comfyClass=name;this.inputs=[];this.size=[390,1200];this.widgets=[
      {name:'backend',type:'combo',value:'本地'},
      {name:name==='H3LVUnified'?'rh_workflow_id':'workflow_id',value:'saved-cloud-id'},
      {name:'project_id',value:''},{name:'fixed_prompt',value:'kept prompt'}];}
    Node.prototype.addWidget=function(type,name,value,callback,options={}){const w={type,name,value,callback,...options};this.widgets.push(w);return w;};
    Node.prototype.addDOMWidget=function(name,type,element,options){const w={name,type,element,options};this.widgets.push(w);return w;};
    Node.prototype.computeSize=function(){return [300,50+this.widgets.reduce((a,w)=>a+(w.computeSize?.(420)?.[1] ?? 22)+4,0)];};
    Node.prototype.setSize=function(value){this.size=value;};
    for(const extension of extensions)await extension.beforeRegisterNodeDef?.(Node,{name});
    const node=new Node();node.onNodeCreated();
    const actions=node.widgets.filter(w=>w.type==='button' && !w.name.startsWith('运镜')).map(w=>w.name);
    assert.deepEqual(actions,common);assert.equal(node.widgets.at(-1).type,'h3talk-progress');
    assert.equal(node.widgets.at(-1).options.getMinHeight(),64);assert.ok(node.size[1]<500);
    assert.equal(node.color,'#263b4b');assert.equal(reads.length,0);
    const field=node.widgets.find(w=>w.name.endsWith('workflow_id'));assert.equal(field.hidden,true);
    const backend=node.widgets.find(w=>w.name==='backend');backend.value='RunningHub';backend.callback();
    assert.equal(field.hidden,false);assert.equal(field.value,'saved-cloud-id');
    assert.equal(node.widgets.find(w=>w.name==='fixed_prompt').value,'kept prompt');
    node.widgets.at(-1).onRemove?.();
  }
});

test('opening talking controls with an empty node selects an existing project without analysis',async()=>{
  const source=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8');
  const start=source.indexOf('async function showPanel('),end=source.indexOf('  document.getElementById("h3talk-modal")',start);
  const body=source.slice(start,end)+'return {pid,projects};}';
  let analyzed=0;
  const show=vm.runInNewContext(body+'\nshowPanel',{validId:id=>/^[0-9a-f]{32}$/.test(id || ''),
    request:async()=>[{id:'a'.repeat(32)}],analyze:async()=>analyzed++});
  const node={widgets:[{name:'project_id',value:''}]};const result=await show(node);
  assert.equal(result.pid,'a'.repeat(32));assert.equal(node.widgets[0].value,result.pid);assert.equal(analyzed,0);
});
