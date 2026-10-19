import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const cases = [
  ['H3LVUnified','H3 长视频 · 音频分析与顺序生成','MiniMax H3 · MV Generator'],
  ['H3LVPromptExpand','H3 长视频 · 分镜提示词生成器','MiniMax H3 · MV Prompt Expander'],
  ['H3LVTalkingRH','H3 口播 · 一镜到底','MiniMax H3 · Talking Avatar'],
];

function setup(name, displayName) {
  let extension;
  const source=fs.readFileSync(new URL('../web/node_names.js',import.meta.url),'utf8').replace(/^import[^\n]*\n/,'');
  vm.runInNewContext(source,{app:{registerExtension:value=>extension=value}});
  function Node() {this.calls=0;}
  Node.prototype.onConfigure=function(info){this.title=info.title;this.calls++;return 'configured';};
  extension.beforeRegisterNodeDef(Node,{name,display_name:displayName});
  return new Node();
}

test('saved stock node titles migrate after the original configure hook',()=>{
  for (const [name,legacy,current] of cases) {
    const node=setup(name,current);
    assert.equal(node.onConfigure({title:legacy}),'configured');
    assert.equal(node.title,current);
    assert.equal(node.calls,1);
  }
});

test('personal node titles remain intact',()=>{
  for (const [name,,current] of cases) {
    const node=setup(name,current);
    node.onConfigure({title:'我的自定义节点标题'});
    assert.equal(node.title,'我的自定义节点标题');
  }
});
