import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function dialog() {
  const all=[],calls=[];
  function element(tag,text,parent,className='') {
    const node={tag,textContent:text,className,value:tag==='select'?'all':'',checked:false,children:[],
      setAttribute(name,value){this[name]=value;},append(...nodes){this.children.push(...nodes);},remove(){},focus(){},
      querySelectorAll(selector){return all.filter(item=>item.className.split(' ').includes(selector.slice(1)));}};
    all.push(node);parent?.append(node);return node;
  }
  const script=fs.readFileSync(new URL('../web/h3lv.js',import.meta.url),'utf8');
  const source=script.slice(script.indexOf('function formatBytes'),script.indexOf('function chooseVideoOutput')).replace(/^export /gm,'');
  const projects=[{id:'current',created:1,duration:7,count:1,status:'completed',mode:'speaking',active:false,project_bytes:100,final_bytes:50,has_final:true},
    {id:'active',created:1,duration:7,count:1,status:'running',mode:'speaking',active:true,project_bytes:100,final_bytes:0}];
  const open=vm.runInNewContext(source+'\nprojectManagerDialog',{app:{},messageDialog(){},element,document:{body:{append(){}}},
    window:{addEventListener(){},removeEventListener(){}},queueMicrotask:fn=>fn(),toast(){},confirmDialog:async()=>false,
    request:async(path,body)=>{calls.push({path,body});return projects;},
    actionButton(parent,label,action,className=''){const node=element('button',label,parent,className);node.onclick=action;return node;}});
  return {all,calls,open};
}

test('talking reuses MV project manager layout and talks only to talking routes',async()=>{
  const d=dialog();const pending=d.open('current','/h3talk');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(d.calls[0].path,'/h3talk/projects/manage');
  assert.ok(d.all.some(node=>node.textContent==='管理口播项目'));
  assert.ok(d.all.some(node=>node.className==='h3lv-manager-current'));
  const items=d.all.filter(node=>node.className.includes('h3lv-project-manager-item'));
  assert.equal(items[1].children[0].disabled,true);
  items[0].children[0].checked=true;items[0].children[0].onchange();
  await d.all.find(node=>node.textContent==='删除所选项目').onclick();
  assert.equal(d.calls.length,1,'cancelled confirmation must not POST deletion');
  d.all.find(node=>node.textContent==='取消').onclick();assert.equal(await pending,null);
});

test('MV keeps its default namespace and management heading',async()=>{
  const d=dialog();const pending=d.open('current');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(d.calls[0].path,'/h3lv/projects/manage');
  assert.ok(d.all.some(node=>node.textContent==='管理分析项目'));
  d.all.find(node=>node.textContent==='关闭').onclick();await pending;
});
