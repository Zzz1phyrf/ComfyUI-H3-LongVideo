import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source=await readFile(new URL('../web/h3lv.js',import.meta.url),'utf8');
const start=source.slice(source.indexOf('async function startApprovedSequence('),source.indexOf('async function checkRunningHub('));
const marker='actionButton(resultControls, "将本段加入重做", async () => {';
const redo=source.slice(source.indexOf(marker)+marker.length,source.indexOf('}, "segment-run");',source.indexOf(marker)));

for(const [backend,oldBackend,label,other] of [['本地','runninghub','本地','RH'],['RunningHub','local','RH','本地']]) {
  test(`start toast uses submitted ${backend}, not previous ${oldBackend}`,async()=>{
    const notices=[],payload={loader_id:'266',prompt:{266:{inputs:{backend}}},video_id:'273'};
    const run=Function('startingProjects','buildGenerationPayload','request','clearVideoNodePreview','toast',
      start+';return startApprovedSequence;')(new Set(),async()=>payload,async()=>({started:true}),()=>{},(...args)=>notices.push(args));
    await run({}, {id:'project',generation_backend:oldBackend,segments:[],controller_active:false});
    assert.equal(notices.length,1);
    assert.ok(notices[0][1].includes(label));
    assert.ok(!notices[0][1].includes(other));
  });
  test(`redo on ${backend} only marks the segment and never submits a task`,async()=>{
    const notices=[],requests=[];
    const run=Function('dirty','plan','row','request','endpoint','toast','load',
      'return async()=>{'+redo+'};')(false,{approved:true,generation_backend:oldBackend},{index:1},
      async(path,body)=>{requests.push({path,body});},x=>x,(...args)=>notices.push(args),async()=>{});
    await run();
    assert.deepEqual(requests,[{path:'/retry',body:{index:1,reroll:true}}]);
    assert.equal(notices[0][0],'已加入重做');
    assert.match(notices[0][1],/开始 \/ 继续生成/);
    assert.match(notices[0][1],/当前结果保留/);
  });
}
