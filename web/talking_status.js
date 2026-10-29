export function talkingStatus(plan, now=Date.now()/1000) {
  const rows=plan.segments || [], done=rows.filter(r=>r.job?.status==='completed').length;
  const labels={draft:'待确认',ready:'待生成',paused:'已暂停',failed:'失败，请打开面板查看原因',generated:'各段完成，待合并',completed:'已完成',merging:'正在合并',pausing:'当前段完成后暂停'};
  const active=['running','pausing','merging'].includes(plan.run_status);
  let text=plan.run_status==='draft' && plan.approved ? '待生成' : labels[plan.run_status] || '生成中';
  if (active && plan.controller_active===false) text='控制进程已中断，请打开面板继续恢复';
  else if (active && plan.run_status!=='merging') {
    const p=plan.progress || {};
    const index=Number.isInteger(p.index) ? p.index : rows.findIndex(r=>r.job?.status!=='completed');
    const job=rows[index]?.job;
    const local=plan.config?.backend==='local';
    const stages={prepare:'准备素材',upload:local?'准备本地输入':'上传素材',submitting:local?'提交本地任务':'提交RH，等待任务ID',
      querying:'查询已提交任务',queued:local?'本地排队中':'RH排队中',running:local?'本地生成中':'RH生成中',
      download:local?'读取生成结果':'下载视频',validate:'校验结果',seams:'分析接缝',accepted:'本地已接收请求'};
    text=`第 ${Math.max(0,index)+1}/${rows.length} 段 · ${stages[p.stage] || '准备生成'}`;
    if (p.since) text+=` · ${Math.max(0,Math.floor(now-p.since))}秒`;
    if (job?.task_id && ['querying','queued','running','download','validate','seams'].includes(p.stage)) text+=` · 任务 ${job.task_id}`;
    if (plan.run_status==='pausing') text+=' · 本段完成后暂停';
  }
  return `${text} · ${done}/${rows.length} 段完成`;
}
