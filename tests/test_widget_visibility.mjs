import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const {syncHiddenWidgetConnections}=await import('data:text/javascript,'+encodeURIComponent(fs.readFileSync(new URL('../web/widget_visibility.js',import.meta.url),'utf8')));

test('hidden virtual widget connections stop drawing while audio and actual wires remain untouched',()=>{
 const node={properties:{ue_properties:{widget_ue_connectable:{secret:true,visible:true,wired:true}}},
  inputs:[{name:'audio',link:1},{name:'secret',widget:{name:'secret'},link:null},{name:'visible',widget:{}},{name:'wired',widget:{},link:2}],
  widgets:[{name:'secret',hidden:true},{name:'visible',hidden:false},{name:'wired',hidden:true}]};
 const original=JSON.stringify(node.inputs);syncHiddenWidgetConnections(node);
 assert.deepEqual(node.properties.ue_properties.widget_ue_connectable,{secret:false,visible:true,wired:true});
 assert.equal(JSON.stringify(node.inputs),original);
 const restored=JSON.parse(JSON.stringify(node));restored.widgets[0].hidden=false;
 syncHiddenWidgetConnections(restored);
 assert.equal(restored.properties.ue_properties.widget_ue_connectable.secret,true);
 assert.equal(restored.properties.h3lv_hidden_ue_widgets,undefined);
});

test('no UE or disabled preference adds no metadata and changing backend restores previous preference',()=>{
 const absent={inputs:[],widgets:[]};syncHiddenWidgetConnections(absent);assert.equal(absent.properties,undefined);
 const node={properties:{ue_properties:{widget_ue_connectable:{workflow_id:false}}},inputs:[{name:'workflow_id',widget:{}}],widgets:[{name:'workflow_id',hidden:true}]};
 syncHiddenWidgetConnections(node);node.widgets[0].hidden=false;syncHiddenWidgetConnections(node);
 assert.equal(node.properties.ue_properties.widget_ue_connectable.workflow_id,false);
});
