"""Apply visible local generation settings without reanalysing approved speech."""
import copy

from ..core import LOCK,project_path,read_plan,write_plan
from . import flat,project,runner


def compatible(previous,current):
    """Canvas choices may change; backend, audio clock and project inputs must match."""
    if previous.get('backend')!='local' or current.get('backend')!='local':return False
    if not previous.get('canvas') or not current.get('canvas'):return False
    return {k:v for k,v in previous.items() if k!='canvas'}=={k:v for k,v in current.items() if k!='canvas'}


def update(root,pid,config,input_fingerprint,expected_fingerprint):
    with LOCK,runner.claim(project_path(root,pid)):
        plan=read_plan(root,pid)
        if project.fingerprint(plan)!=expected_fingerprint:
            raise ValueError('项目已在其他窗口改变，请刷新后再同步画布配置。')
        if not flat.enabled(plan) or not compatible(plan['config'],config):
            raise ValueError('本次改动包含音频、尺寸或其他项目设置，请重新分析；旧项目和输出保留。')
        project.verify_assets(root,plan)
        project.require_settled(plan['segments'])
        if plan.get('regeneration'):
            raise ValueError('请先完成或取消当前单段重做，再同步新的模型或LoRA配置。')
        has_results=bool(plan.get('final_video') or any(r.get('job') or r.get('previous_jobs') for r in plan['segments']))
        updated,_=project.clone_reviewed(root,plan,copy_refs=True) if has_results else (copy.deepcopy(plan),None)
        updated.update(config=copy.deepcopy(config),input_fingerprint=input_fingerprint,error='',
                       run_status='ready' if plan.get('approved') else 'draft')
        if updated.get('approved'):updated['approved_fingerprint']=project.fingerprint(updated)
        project.verify_assets(root,updated)
        write_plan(root,updated)
        return updated
