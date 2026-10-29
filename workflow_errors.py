"""User-facing diagnostics built from actual validation/execution evidence."""
import json
import os

from .diagnostics import configured_key, redact


class WorkflowFailure(ValueError):
    def __init__(self, summary, issues=None):
        super().__init__(summary)
        self.issues = issues or []


class ExecutionFailure(WorkflowFailure, RuntimeError):
    pass


def validation_failure(valid, prompt):
    issues = []
    for node_id, node in (valid[3] if len(valid) > 3 else {}).items():
        for error in node.get('errors', []):
            kind = error.get('type', '')
            field = (error.get('extra_info') or {}).get('input_name', '')
            detail = str(error.get('details') or error.get('message') or kind)
            if kind == 'required_input_missing':
                field = field or detail.rsplit(':', 1)[-1].strip()
                label = {'images': '图像序列', 'frame_rate': '帧率', 'fps': '帧率',
                         'audio': '音频', 'model': '模型', 'vae': 'VAE'}.get(field, field)
                reason = f'缺少必填输入：{label}（{field}）'
                action = '连接对应输入或补齐参数；如果更新过节点，请核对新版输入口。'
            elif kind == 'value_not_in_list':
                reason = f'选项不可用：{field or detail}'
                action = '重新选择当前环境中存在的模型或选项，核对模型文件名。'
            elif kind == 'return_type_mismatch':
                reason = f'连线类型不匹配：{field or detail}'
                action = '检查上游输出类型与此输入口是否一致，重新连接正确端口。'
            else:
                reason, action = detail, '查看该节点的参数与连线；展开技术详情获取原始原因。'
            issues.append({'node_id': str(node_id), 'node_type': node.get('class_type', ''),
                           'title': prompt.get(str(node_id), {}).get('_meta', {}).get('title', ''),
                           'field': field, 'reason': reason, 'action': action, 'detail': detail})
    if not issues:
        issues.append({'reason': '工作流校验未通过，服务未返回逐节点详情。',
                       'action': '查看 ComfyUI 控制台并下载错误日志；不能据此判断为文字提示词错误。',
                       'detail': json.dumps(valid[1], ensure_ascii=False)})
    return WorkflowFailure('工作流检查失败，尚未开始该段生成', issues)


def execution_failure(history):
    for kind, detail in history.get('status', {}).get('messages', []):
        if kind == 'execution_error' and isinstance(detail, dict):
            return ExecutionFailure('节点执行失败', [{'node_id': str(detail.get('node_id', '')),
                'node_type': detail.get('node_type', ''),
                'reason': str(detail.get('exception_message') or detail.get('exception_type') or '执行失败'),
                'action': '检查失败节点；修复后重试失败段，已完成段可以保留。'}])
    return ExecutionFailure('该段 ComfyUI 生成失败，请查看控制台与错误日志。')


def failure_report(error, stage='', index=None, plan=None):
    plan = plan or {}
    text = str(error)
    labels = {'workflow_validation': '工作流检查', 'segment_generation': '本地节点执行',
              'rh_segment_generation': 'RH 分段任务', 'rh_workflow_prepare': 'RH 配置检查',
              'segment_prepare': '分段准备', 'video_assembly': '视频合并'}
    category = labels.get(stage, '操作检查')
    issues = getattr(error, 'issues', [])
    combined = text + ' ' + ' '.join(str(i.get('node_type', '')) + ' ' + str(i.get('reason', '')) for i in issues)
    action = '核对下方原因，修复后再继续；不要通过重新分析丢弃已有进度。'
    if 'out of memory' in combined.lower():
        category, action = '显存不足', '降低分辨率或单段时长，或选择显存更大的实例后重试失败段。'
    elif any(word in combined for word in ('H3LVPromptExpand', '扩写', 'PromptExpand')):
        category, action = '提示词扩写', '检查扩写服务的模型、额度和网络；修复后重试失败段。'
        if any(word in combined for word in ('参考图来源定义', '不存在的图片', 'H3 六段结构', '[Shot 1]', 'Audio 1', '误用了人声')):
            action = '扩写正文的格式或参考图引用未通过校验；核对各图编号与用途，修正或重新扩写后重试失败段，已完成段保留。'
    elif '下载' in combined:
        category, action = '结果下载', '检查网络和磁盘空间；有任务 ID 时继续查询/下载同一个任务，不要重复生成。'
    elif stage == 'video_assembly':
        action = '检查分段文件、磁盘空间和 FFmpeg，修复后点击“仅重新合成”。'
    pending = [r.get('job', {}) for r in plan.get('segments', [])
               if r.get('job', {}).get('backend') == 'runninghub'
               and r.get('job', {}).get('status') not in ('completed', 'failed')]
    if any(j.get('status') == 'creating_unknown' for j in pending):
        action = '创建结果未知：先到 RH 任务列表核对并绑定任务 ID，再继续；不要重复提交。'
    elif pending:
        action = 'RH 任务记录已保留；点击继续查询/下载同一个任务，不要重新生成。'
    completed = sum(r.get('job', {}).get('status') == 'completed' or bool(r.get('replacement_previous_job'))
                    for r in plan.get('segments', []))
    task_ids = [str(j['task_id']) for j in pending if j.get('task_id')]
    rows = plan.get('segments', [])
    if isinstance(index, int) and 0 <= index < len(rows):
        row = rows[index]
        job = row.get('job', {})
        if job.get('status') == 'completed' and row.get('needs_regeneration') and row.get('failed_attempts'):
            job = row['failed_attempts'][-1]
        if job.get('task_id'):
            task_ids.append(str(job['task_id']))
    report = {'category': category, 'summary': f'{"第 " + str(index+1) + " 段 · " if index is not None else ""}{category}失败',
              'reason': text, 'action': action, 'issues': issues,
              'preserved': f'保留 {completed} 段已有结果；已有成片不会因本次失败被删除。',
              'task_ids': list(dict.fromkeys(task_ids))}
    # Only allowlisted evidence enters the UI/log; never serialize inputs or graphs.
    key = configured_key()
    remote_key = os.environ.get('RUNNINGHUB_API_KEY', '')
    def clean(value):
        if isinstance(value, dict): return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list): return [clean(v) for v in value[:32]]
        if isinstance(value, str):
            if remote_key: value = value.replace(remote_key, '[REDACTED]')
            return redact(value, key, 2400)
        return value
    return clean(report)
