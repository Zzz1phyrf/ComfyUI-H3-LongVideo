import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const {talkingStatus}=await import('data:text/javascript,'+encodeURIComponent(fs.readFileSync(new URL('../web/talking_status.js',import.meta.url),'utf8')));

function setup({saveError=false}={}) {
  const calls=[],errors=[];let responsePlan;
  class Element {
    constructor(){this.children=[];this.style={};this.dataset={};this.value='';this.textContent='';}
    append(...items){this.children.push(...items);}replaceChildren(){this.children=[];}
    getAttribute(name){return this[name];}setAttribute(name,value){this[name]=value;}
    pause(){this.pauses=(this.pauses || 0)+1;}
  }
  const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const versions={editable:true,windows:[0,1].map(index=>({index,current:{state:'verified',text:'Actual '+index,task_id:'task-'+index},draft:null,history:[],next_prompt:{text:'Actual '+index}}))};
  const source=fs.readFileSync(new URL('../web/talking_panel.js',import.meta.url),'utf8');
  const body=source.slice(source.indexOf('  function independent()'),source.indexOf('  async function refresh()'));
  const api=vm.runInNewContext(`
    let plan,rows=[],dirty=false,pending=false,timeline=null,actionError='',selected=0;
    let promptVersions,promptDirty=false,promptSignature='',promptLoading=false,rowsSignature='';
    const promptEdits=new Map(),base='/h3talk/project/test';
    ${body}
    ({show(value){onShow(value);apply(value);},perform,loadPrompts,editPrompt(index,text){rows[index].editor.value=text;rows[index].editor.oninput();},resetPrompt,savePrompt,splitAt,removeCut,moveCut,saveCuts,rows:()=>rows,setPending(value){pending=value;},hasUnsavedPrompt:()=>promptDirty})
  `,{talkingStatus,$,document:{createElement:()=>new Element()},onShow:value=>responsePlan=value,
    request:async(name,body)=>{
      calls.push([name,body]);if(name==='prompts')return versions;
      if(name==='prompt'){
        if(saveError)throw new Error('save conflict');
        versions.windows[body.index].draft={text:body.text,sha256:'new'};versions.windows[body.index].next_prompt.text=body.text;
      }
      return responsePlan;
    },fail:error=>errors.push(error.message)});
  const plan={id:'a'.repeat(32),editing_fingerprint:'layout-a',duration:12,approved:true,controller_active:false,credential_configured:true,
    config:{talking_profile:'ref2va-overlap-v1',backend:'local',megapixels:1,seed:9260,person_mask_mode:'none'},run_status:'completed',
    overlap_cuts:[0,144,288],preview_intervals:{0:[0,144],1:[144,288]},
    segments:[{end_sample:144000,generation_frames:175,job:{status:'completed',task_id:'one'}},{end_sample:288000,generation_frames:175,job:{status:'completed',task_id:'two'}}]};
  api.show(plan);return {api,plan,versions,calls,errors,$};
}

test('segment card contains its own editable prompt and real generation evidence',async()=>{
  const {api}=setup();await api.loadPrompts();
  assert.equal(api.rows()[1].editor.value,'Actual 1');assert.equal(api.rows()[1].editor.disabled,false);
  assert.equal(api.rows()[1].actual.value,'Actual 1');assert.match(api.rows()[1].actualLabel.textContent,/task-1/);
  assert.match(api.rows()[1].version.textContent,/查看当前与历史提示词/);
});

test('completed local canvas projects expose merge without regenerating takes',()=>{
  const {api,plan,$,calls}=setup();
  plan.config.execution_mode='canvas';plan.run_status='generated';
  api.show(plan);
  assert.equal($('assemble').hidden,false);
  assert.equal($('assemble').disabled,false);
  assert.equal(calls.length,0);
});

test('independent segment redo sets only the selected index and never starts generation',async()=>{
  const {api,calls}=setup();await api.rows()[1].regenerate.onclick();
  assert.match(api.rows()[1].regenerate.textContent,/本段加入重做/);
  assert.equal(calls[0][0],'regenerate');assert.equal(calls[0][1].index,1);
  assert.ok(!calls.some(([name])=>name==='run'));
});

test('busy controller, unsaved cuts and pending redo lock incompatible actions',()=>{
  const {api,plan,$}=setup();
  for(const change of [{controller_active:true},{approved:false},{regeneration:{index:0}}]){
    api.show({...plan,...change});assert.equal(api.rows()[0].regenerate.disabled,true);
  }
  api.show(plan);api.moveCut(0,6.25);assert.equal(api.rows()[0].regenerate.disabled,true);
  assert.equal($('run').disabled,true);assert.equal($('approve').disabled,false);
});

test('preview content selector distinguishes final footage from full generated video',()=>{
  const {api}=setup();const row=api.rows()[0],used=row.video.src;
  assert.match(row.caption.textContent,/成片画面.*6.000/);
  assert.equal(row.mode.children[0].textContent,'成片中使用的画面');
  row.mode.value='raw';row.mode.onchange();assert.match(row.video.src,/kind=raw/);
  assert.match(row.caption.textContent,/完整生成视频.*7.292/);
  row.mode.value='used';row.mode.onchange();assert.equal(row.video.src,used);
});

test('polling preserves text, active segment and player while each edit stays in its segment',async()=>{
  const {api,plan,$}=setup();await api.loadPrompts();const player=api.rows()[0].video;player.currentTime=2;
  api.editPrompt(0,'User first');api.editPrompt(1,'User second');api.rows()[1].card.open=true;api.rows()[1].card.ontoggle();
  api.show(plan);await api.loadPrompts();
  assert.equal(api.rows()[0].editor.value,'User first');assert.equal(api.rows()[1].editor.value,'User second');
  assert.equal(api.rows()[0].video,player);assert.equal(player.currentTime,2);
  assert.equal(api.rows()[1].card.open,true);assert.match($('selected-audio-label').textContent,/第 2 段/);
  assert.equal($('run').disabled,true);assert.equal(api.hasUnsavedPrompt(),true);
});

test('saving one segment does not erase another pending edit or generate',async()=>{
  const {api,calls,$}=setup();await api.loadPrompts();api.editPrompt(0,'First edit');api.editPrompt(1,'Second edit');
  await api.savePrompt(1);const posts=calls.filter(([name])=>name!=='prompts');
  assert.equal(posts.length,1);assert.equal(posts[0][0],'prompt');assert.equal(posts[0][1].index,1);
  assert.equal(posts[0][1].text,'Second edit');assert.equal(posts[0][1].expected_fingerprint,'layout-a');
  assert.equal(api.rows()[0].editor.value,'First edit');assert.equal(api.rows()[1].actual.value,'Actual 1');
  assert.equal(api.rows()[1].editor.value,'Second edit');assert.equal($('run').disabled,true);
  api.resetPrompt(0);assert.equal(api.rows()[0].editor.value,'Actual 0');assert.equal(api.hasUnsavedPrompt(),false);
});

test('failed save preserves entered text and blocks generation',async()=>{
  const {api,errors,$}=setup({saveError:true});await api.loadPrompts();api.editPrompt(0,'Keep this');await api.savePrompt(0);
  assert.deepEqual(errors,['save conflict']);assert.equal(api.rows()[0].editor.value,'Keep this');assert.equal($('run').disabled,true);
});

test('drag changes both segment timing labels and uses clipped source speech',()=>{
  const {api,plan}=setup();api.moveCut(0,6.5);
  assert.equal(Number(api.rows()[0].end.value),6.5);assert.match(api.rows()[1].time.textContent,/6.500—12.000/);
  assert.equal(Number(new URL(api.rows()[0].clipAudio.src,'http://localhost').searchParams.get('end')),6.5);
  assert.equal(Number(new URL(api.rows()[1].clipAudio.src,'http://localhost').searchParams.get('start')),6.5);
  api.show(plan);assert.equal(Number(api.rows()[0].end.value),6.5);
});

test('typing an end time marks unsaved cuts immediately and invalid input blocks confirmation',()=>{
  const {api,$}=setup();const input=api.rows()[0].end;
  input.value='6.5';input.oninput();assert.match(api.rows()[1].time.textContent,/6.500/);
  assert.equal($('run').disabled,true);assert.equal($('approve').disabled,false);
  input.value='2';input.oninput();assert.equal($('approve').disabled,true);
  input.value='6.';input.oninput();assert.equal(input.value,'6.');assert.equal($('approve').disabled,false);
});

test('add and remove cut preserve local source mapping and enforce the MV duration rules',()=>{
  const {api}=setup();api.removeCut(0);assert.equal(api.rows().length,1);
  api.splitAt(5);assert.equal(api.rows().length,2);assert.equal(Number(api.rows()[0].end.value),5);
  assert.equal(api.rows()[1].source,0);assert.throws(()=>api.splitAt(2),/4–15/);
});

test('save and confirm persists the cuts before approval without queuing generation',async()=>{
  const {api,calls}=setup();api.moveCut(0,6.5);await api.saveCuts();
  assert.deepEqual(calls.filter(([n])=>n!=='prompts').map(([n])=>n),['cuts','approve']);
  const body=calls.find(([n])=>n==='cuts')[1];assert.equal(body.cuts[0],6.5);assert.equal(body.expected_fingerprint,'layout-a');
});

test('redo can be cancelled only when its original results can be restored',()=>{
  const {api,plan,$}=setup();api.show({...plan,regeneration:{index:1},regeneration_can_restore:true});
  assert.equal($('restore').hidden,false);assert.equal($('restore').disabled,false);assert.match($('regeneration-note').textContent,/第2段已加入重做/);
  api.show({...plan,regeneration:{index:1},regeneration_can_restore:false});assert.equal($('restore').disabled,true);
});
