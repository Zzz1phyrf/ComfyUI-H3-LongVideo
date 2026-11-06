import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../web/talking_rh.js',import.meta.url),'utf8');
const begin=source.indexOf('function syncTalkingVideo('),end=source.indexOf('async function refreshStatus(',begin);
function runtime(active=true) {
  const owner={comfyClass:'H3LVTalkingAvatar',widgets:[{name:'project_id',value:'talk'}]};
  const saver={id:273,comfyClass:'SaveVideo'},events=[];
  const app={graph:{_nodes:[saver,...(active?[owner]:[{comfyClass:'H3LVUnified'}])]}};
  const show=vm.runInNewContext(source.slice(begin,end)+'\nsyncTalkingVideo',{
    app,projectId:node=>node.widgets?.find(w=>w.name==='project_id')?.value,
    api:{dispatchCustomEvent:(name,detail)=>events.push(detail)},
  });
  return {show,owner,saver,events};
}
const plan={id:'talk',canvas_video_id:273,canvas_preview:{filename:'talk.mp4'},canvas_preview_kind:'final'};

test('late talking poll cannot write the newly active MV canvas',()=>{
  const r=runtime(false);r.show(r.owner,plan);assert.equal(r.events.length,0);
});

test('new talking project with no outputs clears stale canvas preview once',()=>{
  const r=runtime();r.show(r.owner,{...plan,canvas_preview:null});
  assert.equal(r.events.length,1);assert.equal(r.events[0].output.images.length,0);
  r.show(r.owner,{...plan,canvas_preview:null});assert.equal(r.events.length,1);
  r.show(r.owner,plan);assert.equal(r.events.length,2);
  assert.equal(r.events[1].output.images[0].filename,'talk.mp4');
});
