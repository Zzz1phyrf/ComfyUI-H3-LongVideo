import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function setup() {
  let extension;
  const app={registerExtension:value=>extension=value};
  const source=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8').replace(/^import[^\n]*\n/,'');
  vm.runInNewContext(source,{app});
  function Node(){this.widgets=[{name:'project_id',value:''},{name:'backend',value:'本地'}];}
  Node.prototype.addWidget=function(type,name,value,callback){this.widgets.push({type,name,value,callback});};
  extension.beforeRegisterNodeDef(Node,{name:'H3LVTalkingRH'});
  return new Node();
}

test('old eight-widget RH canvas retains its backend',()=>{
  const node=setup();node.onConfigure({widgets_values:Array(8).fill('')});
  assert.equal(node.widgets.find(w=>w.name==='backend').value,'RunningHub');
});

test('new talking-head canvas keeps local as the default',()=>{
  const node=setup();node.onConfigure({widgets_values:Array(15).fill('')});
  assert.equal(node.widgets.find(w=>w.name==='backend').value,'本地');
});
