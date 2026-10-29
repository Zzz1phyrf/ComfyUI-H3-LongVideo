import {alignProjectNode} from '../web/node_ui.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const {syncHiddenWidgetConnections}=await import('data:text/javascript,'+encodeURIComponent(fs.readFileSync(new URL('../web/widget_visibility.js',import.meta.url),'utf8')));

test('loading an old MV graph removes vocals through the native slot-removal method',async()=>{
 let extension;
 const script=fs.readFileSync(new URL('../web/h3lv.js',import.meta.url),'utf8').replace(/^import .*;\r?\n/gm,'').replace('new URL("./h3lv.css", import.meta.url).href','"h3lv.css"');
 vm.runInNewContext(script,{alignProjectNode,app:{registerExtension:e=>extension=e},document:{querySelector:()=>({})},syncHiddenWidgetConnections});
 function Node(){this.inputs=[{name:'audio',link:10},{name:'vocals',link:11},{name:'mode',widget:{},link:null}];this.widgets=[];this.removed=[];}
 Node.prototype.removeInput=function(index){this.removed.push(this.inputs[index]);this.inputs.splice(index,1);};
 Node.prototype.onConfigure=function(){return 'configured';};
 await extension.beforeRegisterNodeDef(Node,{name:'H3LVUnified'});
 const node=new Node();assert.equal(node.onConfigure({widgets_values:[]}), 'configured');
 assert.deepEqual(node.inputs.map(i=>i.name),['audio','mode']);
 assert.equal(node.inputs[0].link,10);assert.equal(node.removed[0].link,11);
 node.onConfigure({widgets_values:[]});assert.equal(node.removed.length,1);
});
