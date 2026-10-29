import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const {talkingStatus}=await import('data:text/javascript,'+encodeURIComponent(fs.readFileSync(new URL('../web/talking_status.js',import.meta.url),'utf8')));
test('submission does not claim a provider task ID or reuse old completed ID',()=>{
 const plan={run_status:'running',controller_active:true,segments:[{job:{task_id:'old'}}],progress:{stage:'submitting',index:0,since:100}};
 assert.match(talkingStatus(plan,105),/提交RH，等待任务ID.*5秒/);
 assert.doesNotMatch(talkingStatus(plan,105),/old/);
 plan.progress.stage='running';plan.segments[0].job.task_id='new';
 assert.match(talkingStatus(plan,105),/RH生成中.*任务 new/);
 plan.config={backend:'local'};assert.match(talkingStatus(plan,105),/本地生成中/);
});
test('interrupted controller and complete results override stale provider progress',()=>{
 const plan={run_status:'running',controller_active:false,segments:[{job:{status:'completed'}}],progress:{stage:'running'}};
 assert.match(talkingStatus(plan),/中断.*1\/1/);
 plan.run_status='completed';assert.match(talkingStatus(plan),/^已完成/);
 assert.doesNotMatch(talkingStatus(plan),/生成中/);
 plan.run_status='draft';plan.approved=true;assert.match(talkingStatus(plan),/^待生成/);
});
