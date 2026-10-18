import json
import os
from pathlib import Path

import numpy as np
import soundfile as sf

from ..core import LOCK, audio_file, inside, project_path, read_plan, write_plan
from . import root
from . import project, runner

WEB = Path(__file__).resolve().parents[1]/'web'


def public(plan):
    value=json.loads(json.dumps(plan))
    value['controller_active']=runner.busy(root(),plan['id'])
    value['credential_required']=plan['config'].get('backend')!='local'
    value['credential_configured']=not value['credential_required'] or bool(os.environ.get('RUNNINGHUB_API_KEY','').strip())
    return value


def register():
    from aiohttp import web
    from server import PromptServer
    routes=PromptServer.instance.routes

    async def endpoint(request,fn):
        try:
            return await fn()
        except (ValueError,RuntimeError,FileNotFoundError,KeyError) as exc:
            message=str(exc)
            key=os.environ.get('RUNNINGHUB_API_KEY','')
            if key:message=message.replace(key,'[REDACTED]')
            return web.json_response({'error':message[:1200]},status=400)

    @routes.get('/h3talk/assets/{name}')
    async def asset(request):
        name=request.match_info['name']
        if name not in ('talking_panel.js','timeline.js'):
            raise web.HTTPNotFound()
        return web.FileResponse(WEB/name)

    @routes.get('/h3talk/project/{pid}/panel')
    async def panel(request):
        project_path(root(),request.match_info['pid'])
        return web.FileResponse(WEB/'talking_panel.html')

    @routes.get('/h3talk/project/{pid}')
    async def get(request):
        async def action():return web.json_response(public(read_plan(root(),request.match_info['pid'])))
        return await endpoint(request,action)

    @routes.get('/h3talk/project/{pid}/waveform')
    async def waveform(request):
        async def action():
            directory=project_path(root(),request.match_info['pid'])
            wave,_=sf.read(audio_file(directory,'source.wav'),dtype='float32',always_2d=True)
            signal=np.abs(wave).max(axis=1)
            values=np.array([float(v.max()) for v in np.array_split(signal,min(2500,len(signal)))])
            values=np.clip(values/max(float(np.percentile(values,99)),1e-8),0,1).tolist()
            return web.json_response({'waveform':{'original':values,'vocals':values}})
        return await endpoint(request,action)

    @routes.get('/h3talk/project/{pid}/file/{kind}')
    async def file(request):
        async def action():
            plan=read_plan(root(),request.match_info['pid']);directory=project_path(root(),plan['id'])
            kind=request.match_info['kind']
            paths={'audio':audio_file(directory,'source.wav'),'reference':directory/'refs/reference_0.png',
                   'mask':directory/'refs/person_mask.png'}
            if kind=='final':
                if not plan.get('final_video'):raise ValueError('完整片尚未生成。')
                path=inside(root().parent/'final_videos',plan['final_video'])
            elif kind in paths:path=paths[kind]
            else:raise ValueError('文件类型无效。')
            return web.FileResponse(path)
        return await endpoint(request,action)

    @routes.post('/h3talk/project/{pid}/{action}')
    async def post(request):
        async def action():
            pid=request.match_info['pid'];name=request.match_info['action'];data=await request.json()
            with LOCK:
                plan=read_plan(root(),pid)
                if name=='pause':
                    plan['pause_requested']=True
                    if plan['run_status']=='running':plan['run_status']='pausing'
                    write_plan(root(),plan)
                elif name=='run':
                    runner.start(root(),pid)
                else:
                    if runner.busy(root(),pid):raise ValueError('请暂停并等待当前段完成后再修改项目。')
                    if name=='cuts':
                        plan=project.update_cuts(root(),pid,data['cuts'])
                    elif name=='approve':
                        project.verify_assets(root(),plan)
                        plan.update(approved=True,approved_fingerprint=project.fingerprint(plan))
                        write_plan(root(),plan)
                    elif name=='retry':
                        index=int(data['index'])
                        if not 0<=index<len(plan['segments']):raise ValueError('段号超出范围。')
                        job=plan['segments'][index].get('job',{})
                        if job.get('status') not in ('failed','rejected'):
                            raise ValueError('仅明确失败或被拒绝的任务可重建；已知任务请直接继续，未知任务请绑定ID。')
                        plan['segments'][index].setdefault('previous_jobs',[]).append(job)
                        plan['segments'][index].pop('job',None)
                        write_plan(root(),plan)
                    elif name=='recover':
                        index=int(data['index']);task_id=str(data['task_id']).strip()
                        if not 0<=index<len(plan['segments']):raise ValueError('段号超出范围。')
                        job=plan['segments'][index].get('job',{})
                        if job.get('status')!='creating_unknown' or not task_id.isdigit():
                            raise ValueError('请为创建结果未知的片段填写有效RH任务ID。')
                        job.update(task_id=task_id,status='submitted')
                        write_plan(root(),plan)
                    elif name=='assemble':
                        if not plan.get('approved') or plan.get('approved_fingerprint')!=project.fingerprint(plan):
                            raise ValueError('请先确认当前分段。')
                        from ..controller import assemble
                        final=assemble(root(),pid)
                        plan.update(final_video=final,final_stale=False,run_status='completed')
                        write_plan(root(),plan)
                    else:raise ValueError('操作无效。')
            return web.json_response(public(read_plan(root(),pid)))
        return await endpoint(request,action)
