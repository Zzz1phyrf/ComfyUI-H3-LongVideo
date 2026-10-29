import {alignProjectNode} from '../web/node_ui.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../web/rh_settings.js',import.meta.url),'utf8').replace(/^import[^\n]*\n/gm,'');
function setup() {
  let extension;const calls=[];
  const controls=Object.fromEntries(['site','key','status','save','clear','close'].map(k=>[k,{value:'',disabled:false}]));
  const dialog={style:{},innerHTML:'',querySelector:s=>controls[s.slice(6,-1)],querySelectorAll:()=>[controls.save,controls.clear,controls.close],addEventListener(){},showModal(){},close(){},remove(){}};
  const document={getElementById:()=>null,createElement:()=>dialog,body:{append(){}}};
  const api={async fetchApi(path,options){calls.push({path,options});return {ok:true,json:async()=>({configured:true,source:'local'})};}};
  const funcs=vm.runInNewContext(source+'\n({showRHSettings})',{alignProjectNode,document,api,app:{registerExtension:e=>extension=e}});
  return {extension,calls,controls,dialog,funcs};
}

test('both nodes expose a non-serialized settings button, never a key widget',()=>{
  const {extension}=setup();
  for(const name of ['H3LVUnified','H3LVTalkingRH','H3LVTalkingAvatar']) {
    function Node(){this.widgets=[];}
    Node.prototype.addWidget=function(...args){this.widgets.push(args);};
    extension.beforeRegisterNodeDef(Node,{name});const node=new Node();node.onNodeCreated();
    assert.equal(node.widgets.length,1);assert.equal(node.widgets[0][1],'RH API 设置');
    assert.equal(node.widgets[0][2],null);assert.equal(node.widgets[0][4].serialize,false);
  }
});

test('password dialog submits only through settings API and clears input',async()=>{
  const {calls,controls,dialog,funcs}=setup();const node={widgets:[{name:'rh_region',value:'国内版'}]};
  const before=JSON.stringify(node);await funcs.showRHSettings(node);
  assert.match(dialog.innerHTML,/type="password"/);
  assert.match(calls[0].path,/runninghub.cn/);
  controls.key.value='fixture-private';await controls.save.onclick();
  assert.equal(calls[1].path,'/h3lv/rh/settings');
  assert.equal(calls[1].options.method,'POST');
  assert.deepEqual(JSON.parse(calls[1].options.body),{base_url:'https://www.runninghub.cn',api_key:'fixture-private'});
  assert.equal(controls.key.value,'');assert.equal(JSON.stringify(node),before);
  assert.doesNotMatch(controls.status.textContent,/fixture-private/);
  await controls.save.onclick();assert.equal(calls.length,2);
  await controls.clear.onclick();assert.deepEqual(JSON.parse(calls[2].options.body),{base_url:'https://www.runninghub.cn',clear:true});
});
