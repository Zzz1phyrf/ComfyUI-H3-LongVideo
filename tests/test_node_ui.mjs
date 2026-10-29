import test from 'node:test';
import assert from 'node:assert/strict';
import {alignProjectNode} from '../web/node_ui.js';

const common=['RH API 设置','检查 RH 配置（不生成）','重新分析并分段','打开分段与生成控制','下载错误日志'];
function fixture(comfyClass) {
  const node={comfyClass,size:[390,1100],inputs:[{name:'audio',link:12}],outputs:[{name:'project_id',links:[13]}],
    widgets:[{name:'backend',value:'本地',type:'combo',computeSize:()=>[200,20]},
      {name:'project_id',value:'saved',hidden:true,computeSize:()=>[0,-4]},
      {name:comfyClass==='H3LVUnified'?'rh_workflow_id':'workflow_id',value:'keep-cloud-id',type:'text',computeSize:()=>[200,20]},
      {name:'prompt',value:'user text',type:'text',computeSize:()=>[200,100]},
      {name:'项目进度',type:'h3talk-progress',serialize:false,computeSize:()=>[200,64]},
      ...common.toReversed().map(name=>({name,type:'button',serialize:false,computeSize:()=>[200,20]}))],
    computeSize(){return [300,30+this.widgets.reduce((total,w)=>total+(w.computeSize?.()[1] ?? 20)+4,0)];},
    setSize(value){this.size=value;},setDirtyCanvas(){}};
  return node;
}
test('both public nodes share action order, palette, width and progress placement without touching values or links',()=>{
  const nodes=['H3LVUnified','H3LVTalkingAvatar'].map(fixture);
  for(const node of nodes) {
    const parameters=node.widgets.filter(w=>w.serialize!==false),values=parameters.map(w=>w.value);
    const ports=JSON.stringify([node.inputs,node.outputs]);
    alignProjectNode(node);alignProjectNode(node);
    assert.deepEqual(node.widgets.filter(w=>w.type==='button').map(w=>w.name),common);
    assert.equal(node.widgets.at(-1).type,'h3talk-progress');
    assert.deepEqual(node.widgets.filter(w=>w.serialize!==false),parameters);
    assert.deepEqual(parameters.map(w=>w.value),values);assert.equal(JSON.stringify([node.inputs,node.outputs]),ports);
    assert.equal(node.size[0],420);assert.ok(node.size[1]<600);
  }
  assert.equal(nodes[0].color,nodes[1].color);assert.equal(nodes[0].bgcolor,nodes[1].bgcolor);
});
test('MV hides RH fields locally and restores their height when changing backend, with chained callback once',()=>{
  const node=fixture('H3LVUnified'),backend=node.widgets[0],field=node.widgets[2];let calls=0;
  backend.callback=()=>calls++;
  alignProjectNode(node);alignProjectNode(node);assert.equal(field.hidden,true);assert.equal(field.computeSize()[1],-4);
  const localHeight=node.size[1];backend.value='RunningHub';backend.callback();
  assert.equal(calls,1);assert.equal(field.hidden,false);assert.equal(field.computeSize()[1],20);
  assert.ok(node.size[1]>localHeight);assert.equal(field.value,'keep-cloud-id');
  backend.value='本地';backend.callback();assert.equal(calls,2);assert.equal(node.size[1],localHeight);
});
test('MV-only camera rules stay first and a wider user node stays wide',()=>{
  const node=fixture('H3LVUnified');node.size[0]=600;
  node.widgets.push({name:'运镜规则：查看与修改',type:'button',serialize:false});
  alignProjectNode(node);assert.equal(node.size[0],600);
  assert.equal(node.widgets.find(w=>w.type==='button').name,'运镜规则：查看与修改');
});
