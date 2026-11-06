export const busy = plan => ['running','pausing','stopping','merging'].includes(plan?.run_status);
const stages = {
  workflow:'本地已接收 · 检查 RH 工作流', prepare:'准备本段素材（尚未提交生成）',
  expansion:'提示词扩写（尚未提交 RH）', upload:'上传素材（尚未提交 RH）',
  rh_submitting:'正在提交 RH · 等待任务 ID', rh_received:'RH 已接收任务',
  rh_queued:'RH 排队中', rh_running:'RH 生成中', download:'下载视频', validate:'校验视频与准备预览',
  local_queued:'本地任务已入队', local_running:'本地执行中（扩写／模型生成）',
  segment_completed:'本段完成', merge:'合并视频', completed:'已完成', paused:'已暂停', failed:'失败，请查看原因'
};
export function progressText(plan, now=Date.now()/1000) {
  const p=plan?.progress;
  if (!p) return busy(plan) ? '本地已接收请求 · 等待阶段状态' : '';
  if (!busy(plan) && !['completed','failed','paused'].includes(p.stage)) return '';
  const prefix=Number.isInteger(p.index) ? `第 ${p.index+1}/${plan.segments?.length || plan.count} 段 · ` : '';
  const seconds=Math.max(0,Math.floor(now-Number(p.since || now)));
  const elapsed=busy(plan) ? ` · 本阶段 ${Math.floor(seconds/60)}分${seconds%60}秒` : '';
  const row=plan.segments?.[p.index];
  const id=['rh_submitting','rh_received','rh_queued','rh_running','download','validate','segment_completed'].includes(p.stage) ? row?.job?.task_id : '';
  return prefix+(stages[p.stage] || p.stage)+elapsed+(id ? ` · 任务 ${id}` : '');
}

export function canvasProgressText(plan, now=Date.now()/1000) {
  const rows=plan.segments || [];
  const completed=rows.filter(row=>row.job?.status==='completed' && !(row.needs_regeneration && row.job?.video)).length;
  const labels={draft:plan.approved?'待生成':'待确认',completed:'已完成',failed:'失败，请打开面板查看原因',
    paused:'已暂停',stopped:'已停止',merging:'合并中'};
  const seconds=Math.max(0,Math.floor(now-Number(plan.progress?.since || now)));
  const live=progressText(plan,now).replace(/RH /g,'RH').replace(/本阶段 \d+分\d+秒/,`${seconds}秒`);
  const text=(busy(plan) && live) || labels[plan.run_status] || plan.run_status;
  return `${text} · ${completed}/${rows.length} 段完成`;
}
