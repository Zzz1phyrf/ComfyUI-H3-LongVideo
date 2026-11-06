"""Talking project inventory and explicitly requested removal of settled projects."""
import shutil
from pathlib import Path

from ..core import LOCK, project_path, read_plan
from ..storage import final_file, project_plans, project_roots
from . import project,runner


def size(path):
    if path.is_file():return path.stat().st_size
    total=0
    for item in path.rglob('*'):
        if item.is_file():
            try:total+=item.stat().st_size
            except OSError:continue
    return total


def entry(root,pid):
    directory=project_path(root,pid);plan=read_plan(root,pid)
    if plan.get('kind')!='talking_rh':raise ValueError('不是口播项目。')
    try:project.require_settled(plan['segments'],allow_finished_provider=True);pending=False
    except ValueError:pending=True
    files=set()
    for owner in project_roots(root):
        for path in (owner.parent/'final_videos').glob(pid+'_*.mp4'):
            candidate=final_file(root,path)
            if candidate.is_file():files.add(candidate)
    if plan.get('final_video'):
        candidate=final_file(root,plan['final_video'])
        if candidate.is_file():files.add(candidate)
    return {'id':pid,'created':plan['created'],'duration':plan['duration'],'count':len(plan['segments']),
        'status':plan['run_status'],'mode':'speaking','active':runner.busy(root,pid) or pending,
        'project_bytes':size(directory),'final_bytes':sum(size(path) for path in files),'has_final':bool(files),
        'directory':directory,'final_files':files}


def inventory(root):
    result=[]
    for file in project_plans(root):
        try:row=entry(root,file.parent.parent.name)
        except (OSError,ValueError):continue
        result.append({key:value for key,value in row.items() if key not in ('directory','final_files')})
    return sorted(result,key=lambda row:row['created'],reverse=True)


def remove(root,payload):
    ids=payload.get('ids')
    if not isinstance(ids,list) or not ids or len(ids)>200 or any(not isinstance(pid,str) for pid in ids):
        raise ValueError('请选择1–200个有效项目。')
    ids=list(dict.fromkeys(ids));delete_final=payload.get('delete_final',False)
    if type(delete_final) is not bool:raise ValueError('删除成片选项须为布尔值。')
    with LOCK:
        rows=[entry(root,pid) for pid in ids]
        if any(row['active'] for row in rows):raise ValueError('项目仍在运行或有未解决任务，请先等待或继续查询。')
        reclaimed=0
        for row in rows:
            # Project and final paths were resolved before any removal.
            shutil.rmtree(row['directory']);reclaimed+=row['project_bytes']
            if delete_final:
                for file in row['final_files']:file.unlink(missing_ok=True)
                reclaimed+=row['final_bytes']
    return {'deleted':ids,'delete_final':delete_final,'reclaimed_bytes':reclaimed}
