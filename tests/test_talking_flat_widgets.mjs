import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

function runtime() {
  let extension;
  const source=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8').replace(/^import[^\n]*\n/gm,'');
  vm.runInNewContext(source,{app:{registerExtension:value=>extension=value},api:{},
    syncHiddenWidgetConnections:()=>{},alignProjectNode:()=>{},setTalkingStatus:()=>{}});
  return extension;
}

test('new public nodes fix the hidden strategy to first-last anchoring for both backends',()=>{
  for(const backend of ['本地','RunningHub']) {
    const extension=runtime();
    function Node() {}
    extension.beforeRegisterNodeDef(Node,{name:'H3LVTalkingAvatar'});
    const node=new Node();node.comfyClass='H3LVTalkingAvatar';node.inputs=[];node.outputs=[];
    node.addOutput=()=>{};
    node.widgets=[{name:'backend',value:backend},{name:'generation_strategy',value:'原有续接'},
      {name:'project_id',value:''},{name:'audio_processing',value:'直接使用'}];
    node.addWidget=(type,name,value,callback)=>{const widget={type,name,value,callback};node.widgets.push(widget);return widget;};
    node.onNodeCreated();
    const strategy=node.widgets.find(w=>w.name==='generation_strategy');
    assert.equal(strategy.value,'原图首尾锚定');assert.equal(strategy.hidden,true);
    assert.equal(strategy.computeSize()[1],-4);
    const saved={};node.onSerialize(saved);assert.equal(saved.widgets_values[7],'原图首尾锚定');
    // Loading a historical graph restores its saved value, but no selector.
    strategy.value='原有续接';node.onConfigure({widgets_values:['本地','','海外版','plus','','old','直接使用','原有续接']});
    assert.equal(strategy.value,'原有续接');assert.equal(strategy.hidden,true);
  }
});

test('flat strategy survives actual workflow migration and serialization',()=>{
  const extension=runtime();
  const data={nodes:[{id:5,type:'H3LVTalkingAvatar',inputs:[],outputs:[],widgets_values:
    ['RunningHub','123','海外版','plus','','natural speech','直接使用','原图首尾锚定（RH测试）']}],links:[]};
  extension.beforeConfigureGraph(data);
  assert.equal(data.nodes[0].widgets_values[7],'原图首尾锚定（RH测试）');
  function Node() {}
  extension.beforeRegisterNodeDef(Node,{name:'H3LVTalkingAvatar'});
  const node=new Node();
  const names=['backend','workflow_id','region','instance_type','project_id','fixed_prompt','audio_processing','generation_strategy'];
  node.widgets=names.map((name,index)=>({name,value:data.nodes[0].widgets_values[index]}));
  const saved={};node.onSerialize(saved);
  assert.equal(saved.widgets_values[7],'原图首尾锚定（RH测试）');
  data.nodes[0].widgets_values=saved.widgets_values;
  extension.beforeConfigureGraph(data);
  assert.equal(data.nodes[0].widgets_values[7],'原图首尾锚定（RH测试）');
});

test('legacy seven-widget workflow retains original strategy',()=>{
  const extension=runtime();
  const data={nodes:[{id:5,type:'H3LVTalkingAvatar',inputs:[],outputs:[],widgets_values:
    ['RunningHub','123','海外版','plus','','old prompt','直接使用']}],links:[]};
  extension.beforeConfigureGraph(data);
  assert.equal(data.nodes[0].widgets_values[7],'原有续接');
  assert.equal(data.nodes[0].widgets_values[5],'old prompt');
});

test('expanded canvas preserves local and RH choices during frontend migration',()=>{
  for(const backend of ['本地','RunningHub']) {
    const extension=runtime();
    const data={nodes:[
      {id:5,type:'H3LVTalkingAvatar',inputs:[],outputs:[],widgets_values:
        ['本地','','海外版','plus','','natural speech','直接使用','原图首尾锚定']},
      {id:20,type:'MiniMaxH3ImageToVideo',inputs:[],outputs:[{name:'latent',type:'LATENT',links:[1]}]},
      {id:21,type:'SamplerCustom',inputs:[{name:'latent_image',type:'LATENT',link:1}],outputs:[]}
    ],links:[[1,20,0,21,0,'LATENT']]};
    const owner=data.nodes.find(n=>n.type==='H3LVTalkingAvatar');owner.widgets_values[0]=backend;
    const links=JSON.stringify(data.links);const count=data.nodes.length;
    extension.beforeConfigureGraph(data);
    assert.equal(owner.widgets_values[0],backend);
    assert.equal(owner.widgets_values[7],'原图首尾锚定');
    assert.equal(data.nodes.length,count);assert.equal(JSON.stringify(data.links),links);
    assert.ok(data.nodes.some(n=>n.type==='SamplerCustom'));
    assert.ok(data.nodes.some(n=>n.type==='MiniMaxH3ImageToVideo'));
  }
});
