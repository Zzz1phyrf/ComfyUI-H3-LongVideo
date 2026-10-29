"""Window prompt drafts and evidence from immutable submitted/executed graphs."""
import hashlib
import json
import time

from ..core import LOCK, inside, project_path, read_plan, write_plan
from . import overlap


def snapshot(text):
    return {'text':text,'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest()}


def graph_text(graph):
    values=[node.get('inputs',{}).get('prompt') for node in graph.values()
            if isinstance(node,dict) and node.get('class_type') in ('MiniMaxH3ReferenceToVideo','MiniMaxH3FirstLastFrameToVideo')]
    return values[0] if len(values)==1 and isinstance(values[0],str) else None


def evidence(directory,job):
    result={'task_id':job.get('task_id',''),'status':job.get('status',''),'state':'unavailable'}
    texts={}
    for name in ('submitted_graph.json','actual_graph.json'):
        try:
            folder=inside(directory,job['directory'])
            value=json.loads((folder/name).read_text(encoding='utf-8'))
            if isinstance(value,dict):texts[name]=graph_text(value)
        except (OSError,ValueError,KeyError,TypeError):
            pass
    actual=texts.get('actual_graph.json');submitted=texts.get('submitted_graph.json')
    if actual is not None:
        result.update(snapshot(actual),source='actual_graph.json',state='actual')
        if submitted is not None:
            result['submitted_sha256']=snapshot(submitted)['sha256']
            result['state']='verified' if actual==submitted else 'mismatch'
    elif submitted is not None:
        result.update(snapshot(submitted),source='submitted_graph.json',state='submitted')
    else:
        result['reason']='缺少可解析的文本快照；动态引用不按当前项目提示词推断历史。'
    return result


def effective(plan,row):
    frozen=row.get('generation_prompt') if plan.get('regeneration') else None
    return (frozen or row.get('prompt_draft') or row.get('generation_prompt') or
            {'text':plan['config'].get('fixed_prompt','')})['text']


def freeze_redo(directory,plan,row):
    # A redo without a draft keeps this take's own prompt, even in a mixed-version project.
    if row.get('prompt_draft'):return snapshot(row['prompt_draft']['text'])
    prior=evidence(directory,row.get('job',{}))
    if prior.get('text') is not None:return snapshot(prior['text'])
    return snapshot(effective(plan,row))


def versions(root,plan):
    directory=project_path(root,plan['id'])
    windows=[]
    for row in plan['segments']:
        job=row.get('job');history=[];seen=set()
        for old in reversed(row.get('previous_jobs',[])):
            identity=(old.get('directory'),old.get('task_id'))
            if identity in seen or old==job:continue
            seen.add(identity);history.append(evidence(directory,old))
        current=evidence(directory,job) if job else None
        next_text=(row.get('prompt_draft') or current or {}).get('text',effective(plan,row))
        if plan.get('regeneration') and row.get('generation_prompt'):next_text=effective(plan,row)
        windows.append({'index':row['index'],'current':current,
                        'draft':row.get('prompt_draft'), 'history':history,
                        'next_prompt':snapshot(next_text)})
    return {'editable':overlap.enabled(plan),'project_prompt':plan['config'].get('fixed_prompt',''),
            'windows':windows}


def save_draft(root,pid,index,text,expected_sha256):
    from .project import require_settled
    from .runner import claim
    if type(index) is not int:raise ValueError('分段编号无效。')
    if not isinstance(text,str) or not text.strip() or len(text)>32000:
        raise ValueError('提示词须为1至32000字的非空文本。')
    directory=project_path(root,pid)
    with LOCK,claim(directory):
        plan=read_plan(root,pid)
        if not overlap.enabled(plan):raise ValueError('旧版续接项目仅展示历史提示词；编辑请使用独立分段项目。')
        if not 0<=index<len(plan['segments']):raise ValueError('分段编号超出范围。')
        if plan.get('regeneration'):raise ValueError('请先完成当前重做或恢复旧结果，再编辑提示词。')
        require_settled(plan['segments'])
        row=plan['segments'][index]
        if (row.get('prompt_draft') or {}).get('sha256')!=expected_sha256:
            raise ValueError('本段提示词已在其他页面修改，请重新读取后再保存。')
        row['prompt_draft']={**snapshot(text),'saved_at':time.time()}
        # Drafts do not replace completed takes, revoke cut approval, or stale the final.
        write_plan(root,plan)
        return plan
