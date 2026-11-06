import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source=fs.readFileSync(new URL('../web/h3lv.js',import.meta.url),'utf8');
const footer=source.slice(source.indexOf("  const projectSettings ="),source.indexOf('  let plan = null;',source.indexOf("  const projectSettings =")));
const helpers=source.slice(source.indexOf('function needsReplacement('),source.indexOf('\nfunction ',source.indexOf('function recoveryRows(')+1));
function element(tag,text,parent) {
  const node={tag,textContent:text || '',children:[],value:'',dataset:{},disabled:false,
    setAttribute(){},replaceChildren(){this.children=[];},append(child){this.children.push(child);}};
  parent?.append(node);return node;
}
function harness(plan,request) {
  const content=element('main');
  return Function('element','content','request','endpoint','busy','actionButton','load',
    `let plan=${JSON.stringify(plan)},dirty=false;${helpers}\n${footer}\nreturn {projectSettings,settingsText,recovery,recoverButton,recoveryIndex,recoveryId,setPlan:value=>plan=value};`)(
      element,content,request,path=>path,()=>false,(parent,label,fn)=>{const node=element('button',label,parent);node.onclick=fn;return node;},async()=>{});
}

test('MV footer exposes both sections and renders saved parameters',async()=>{
  const ui=harness({id:'p',max_seconds:15,segments:[]},async()=>({project_id:'p',recorded:true,backend:'RunningHub',fps:24,workflow_id:'123',nodes:[{id:'261',type:'Sampler',values:{seed:123,steps:6}}]}));
  assert.equal(ui.projectSettings.children[0].textContent,'项目设置与生成参数');
  assert.equal(ui.recovery.children[0].textContent,'RH 异常任务恢复');
  ui.projectSettings.open=true;await ui.projectSettings.ontoggle();
  assert.match(ui.settingsText.textContent,/RH 工作流ID：123/);
  assert.match(ui.settingsText.textContent,/seed=123 · steps=6/);
});

test('settings response from another project cannot overwrite current settings',async()=>{
  let finish;const ui=harness({id:'old',segments:[]},()=>new Promise(resolve=>finish=resolve));
  ui.projectSettings.open=true;const loading=ui.projectSettings.ontoggle();
  ui.setPlan({id:'new'});ui.settingsText.textContent='new project';
  finish({project_id:'old',fps:24,recorded:false});await loading;
  assert.equal(ui.settingsText.textContent,'new project');
});

test('footer recovery submits exact existing task ID only for an unknown RH segment',async()=>{
  const calls=[];
  const ui=harness({id:'p',segments:[{index:2,job:{backend:'runninghub',status:'creating_unknown'}}]},async(...args)=>calls.push(args));
  ui.recoveryIndex.value='2';ui.recoveryId.value='123456';await ui.recoverButton.onclick();
  assert.deepEqual(calls,[['/rh-recover',{index:2,task_id:'123456'}]]);
  ui.setPlan({id:'p',segments:[{index:2,job:{backend:'runninghub',status:'submitted'}}]});
  await assert.rejects(ui.recoverButton.onclick(),/没有创建结果未知/);
  assert.equal(calls.length,1);
});

test('segment statuses use Chinese and show stale completed takes as pending redo',()=>{
  const status=Function(helpers+';return segmentStatus;')();
  assert.equal(status({job:{status:'completed'}}),'已完成');
  assert.equal(status({needs_regeneration:true,job:{status:'completed',video:'old.mp4'}}),'待重做');
  assert.equal(status({job:{status:'creating_unknown'}}),'创建结果未知');
  assert.equal(status({job:{status:'submitted'}}),'已提交，待获取结果');
});
