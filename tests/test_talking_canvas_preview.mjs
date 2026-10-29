import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function setup(nodes=[]) {
  const events=[];
  let extension;
  const source=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8').replace(/^import[^\n]*\n/gm,'');
  const context={app:{graph:{_nodes:nodes},registerExtension(value){extension=value;}},syncHiddenWidgetConnections(){},
    api:{dispatchCustomEvent:(name,event)=>events.push({name,event})}};
  vm.runInNewContext(source+'\nglobalThis.sync=syncTalkingVideo;globalThis.prepare=preparePublicCanvas;',context);
  return {...context,events,extension};
}

test('one selected video output changes from segment to final without a merge node',()=>{
  const owner={widgets:[{name:'project_id',value:'project'}]};
  const video={id:273,comfyClass:'SaveVideo'};
  const unrelated={id:99,comfyClass:'SaveVideo',title:'keep'};
  const {sync,events}=setup([video,unrelated]);
  const plan={id:'project',canvas_video_id:'273',canvas_preview_kind:'segment',canvas_preview_index:1,
    canvas_preview:{filename:'segment.mp4',subfolder:'project/takes',type:'output'}};
  sync(owner,plan);sync(owner,plan);
  assert.equal(events.length,1);assert.equal(events[0].event.node,'273');
  assert.equal(video.title,'视频输出 · 第 2 段原片');assert.equal(unrelated.title,'keep');
  sync(owner,{...plan,canvas_preview_kind:'final',canvas_preview:{filename:'final.mp4',subfolder:'final_videos'}});
  assert.equal(events.length,2);assert.equal(video.title,'视频输出 · 完整成片');
  assert.equal(events[1].event.output.images[0].filename,'final.mp4');
});

test('late polling for a previous project cannot replace the current video',()=>{
  const video={id:273,comfyClass:'SaveVideo'};const {sync,events}=setup([video]);
  sync({widgets:[{name:'project_id',value:'new'}]},
    {id:'old',canvas_video_id:'273',canvas_preview:{filename:'old.mp4'}});
  assert.equal(events.length,0);assert.equal(video.title,undefined);
});

test('legacy ports migrate before core configuration, and late widget loading preserves project',()=>{
  const owner={id:5,type:'H3LVTalkingAvatar',widgets_values:['本地','123','海外版','ultra',9260,'saved-project','prompt'],
    outputs:['project_id','audio','reference_image','frames','width','height','seed','prompt','fps','filename_prefix'].map(name=>({name})),
    inputs:[{name:'audio'},{name:'reference_image'},{name:'seed'},{name:'fixed_prompt'}]};
  const data={nodes:[owner,{id:268}],links:[[1,5,7,268,0,'STRING'],[2,12002,0,5,3,'STRING']]};
  const {prepare,extension}=setup([]);prepare(data);
  assert.equal(owner.outputs.length,9);assert.equal(owner.outputs[6].name,'prompt');
  assert.equal(data.links[0][2],6);assert.equal(data.links[1][4],2);
  assert.deepEqual(Array.from(owner.widgets_values),['本地','123','海外版','ultra','saved-project','prompt']);
  // ComfyUI performs another widget assignment after node.onConfigure.
  const loaded={id:5,comfyClass:'H3LVTalkingAvatar',inputs:[],widgets:[
    {name:'backend',value:'海外版'},{name:'project_id',value:'wrong'},{name:'fixed_prompt',value:'wrong'}]};
  extension.loadedGraphNode(loaded);
  assert.equal(loaded.widgets[0].value,'本地');assert.equal(loaded.widgets[1].value,'saved-project');
  assert.equal(loaded.widgets[2].value,'prompt');
});
