import asyncio
import json
import math
from pathlib import Path
import uuid

import numpy as np
import soundfile as sf

from ..core import LOCK, audio_file, inside, project_path, read_plan, write_plan, output_preview
from . import root
from . import project, runner, overlap, prompts
from .. import rh_credentials
from ..storage import comfy_output, final_file, project_plans

WEB = Path(__file__).resolve().parents[1]/'web'


def check_rh(data):
    from .remote import Client, validate_config
    from .sampling import freeze, binding, summary
    if data.get('region') not in ('海外版', '国内版'):
        raise ValueError('请选择RH国内站或海外站。')
    config = {'workflow_id': str(data.get('workflow_id', '')).strip(),
              'base_url': 'https://www.runninghub.ai' if data['region']=='海外版' else 'https://www.runninghub.cn',
              'instance_type': data.get('instance_type')}
    validate_config(config)
    snapshot = freeze(Client(config).workflow(),prepared_audio=True)
    sampler, reference = binding(snapshot['graph'])
    if data.get('independent', True) and snapshot['graph'][sampler]['class_type'] != 'SamplerCustom':
        raise ValueError('独立窗口口播需要标准K单采样，请选择口播RH工作流。')
    condition = snapshot['graph'][reference]
    if condition['class_type'] != 'MiniMaxH3ReferenceToVideo' or not snapshot.get('prepared_audio'):
        raise ValueError('口播工作流须使用Ref2VA，并连接参考人声。')
    return {'workflow_id':config['workflow_id'], 'sampling':summary(snapshot), 'submitted':False}


def project_list():
    items = []
    for path in project_plans(root()):
        pid = path.parent.parent.name
        try:
            plan = read_plan(root(), pid)
        except (ValueError, OSError):
            continue
        if plan.get('kind') != 'talking_rh': continue
        items.append({'id':pid, 'created':plan.get('created',0), 'duration':plan['duration'],
                      'count':len(plan['segments']), 'run_status':plan['run_status']})
    return sorted(items, key=lambda item:item['created'], reverse=True)


def public(plan):
    value=json.loads(json.dumps(plan))
    value['editing_fingerprint']=project.fingerprint(plan)
    value['controller_active']=runner.busy(root(),plan['id'])
    width,height=project.output_size(plan['config'])
    value['resolution_summary']=f'{width}×{height}'
    final=plan.get('final_video') if not plan.get('final_stale') else None
    completed=[row for row in plan['segments'] if row.get('job',{}).get('status')=='completed']
    video=final or (completed[-1]['job'].get('video') if completed else None)
    value['canvas_preview']=output_preview(comfy_output(root()),video) if video else None
    value['canvas_preview_kind']='final' if final else 'segment'
    value['canvas_preview_index']=completed[-1]['index'] if completed and not final else None
    value['canvas_video_id']=plan['config'].get('canvas',{}).get('video_id')
    value['credential_required']=plan['config'].get('backend')!='local'
    value['credential_configured']=not value['credential_required'] or rh_credentials.public_settings(plan['config']['base_url'])['configured']
    if plan.get('regeneration'):
        try:
            project.require_settled(plan['segments'],allow_finished_provider=True)
            value['regeneration_can_restore']=True
        except ValueError:value['regeneration_can_restore']=False
    if plan['config'].get('rh_sampling'):
        from .sampling import summary
        value['sampling_summary']=summary(plan['config']['rh_sampling'])
    if overlap.enabled(plan):
        value['max_seconds']=15.
        speech=plan.get('speech_segmentation')
        if 'manual_cuts' in plan:value['segmentation_summary']='当前为手动分段，按已保存切点生成；每段4–15秒。'
        elif speech:
            count=speech['fallback_count']
            value['segmentation_summary']='优先按原声停顿切分，每段4–15秒。'+(f'{count}处缺少可用停顿，已按时长切分，请试听复核。' if count else '切点均位于检测到的停顿内，请试听确认。')
        value['preview_intervals']={}
        for i in range(len(plan['segments'])):
            try:value['preview_intervals'][str(i)]=list(overlap.interval(plan,i))
            except ValueError:pass
        value['seam_warnings']=[f'接缝{i}：{w}' for i,s in plan.get('overlap_seams',{}).items() for w in s.get('warnings',[])]
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
            message=rh_credentials.redact_keys(message)
            return web.json_response({'error':message[:1200]},status=400)

    @routes.get('/h3talk/assets/{name}')
    async def asset(request):
        name=request.match_info['name']
        if name not in ('talking_panel.js','talking_status.js','talking_progress.js','timeline.js'):
            raise web.HTTPNotFound()
        return web.FileResponse(WEB/name)

    @routes.post('/h3talk/rh-check')
    async def rh_check(request):
        async def action():
            data = await request.json()
            return web.json_response(await asyncio.to_thread(check_rh, data))
        return await endpoint(request, action)

    @routes.get('/h3talk/projects')
    async def projects(request):
        async def action(): return web.json_response(await asyncio.to_thread(project_list))
        return await endpoint(request, action)

    @routes.get('/h3talk/projects/manage')
    async def manage_projects(request):
        from .management import inventory
        async def action():return web.json_response(await asyncio.to_thread(inventory,root()))
        return await endpoint(request,action)

    @routes.post('/h3talk/projects/delete')
    async def delete_projects(request):
        from .management import remove
        async def action():
            payload=await request.json()
            if not isinstance(payload,dict):raise ValueError('项目删除请求须为JSON对象。')
            return web.json_response(await asyncio.to_thread(remove,root(),payload))
        return await endpoint(request,action)

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
            plan=read_plan(root(),request.match_info['pid'])
            start=float(request.query.get('start',0));end=float(request.query.get('end',plan['duration']))
            if not math.isfinite(start) or not math.isfinite(end) or not 0<=start<end<=plan['duration']:
                raise ValueError('试听起止时间无效。')
            wave,_=sf.read(audio_file(directory,'source.wav'),start=round(start*24000),stop=round(end*24000),dtype='float32',always_2d=True)
            if not len(wave):raise ValueError('试听区间为空。')
            signal=np.abs(wave).max(axis=1)
            values=np.array([float(v.max()) for v in np.array_split(signal,min(2500,len(signal)))])
            values=np.clip(values/max(float(np.percentile(values,99)),1e-8),0,1).tolist()
            return web.json_response({'waveform':{'original':values,'vocals':values}})
        return await endpoint(request,action)

    @routes.get('/h3talk/project/{pid}/audio-preview')
    async def audio_preview(request):
        async def action():
            plan=read_plan(root(),request.match_info['pid']);directory=project_path(root(),plan['id'])
            start=float(request.query['start']);end=float(request.query['end'])
            if not math.isfinite(start) or not math.isfinite(end) or not 0<=start<end<=plan['duration']:
                raise ValueError('试听起止时间无效。')
            a,b=round(start*24000),min(plan['samples'],round(end*24000))
            if b<=a:raise ValueError('试听区间为空。')
            source=audio_file(directory,'source.wav')
            if project.digest(source)!=plan['source_sha256']:raise ValueError('项目原声已变化。')
            target=directory/'cache'/f'speech_{plan["source_sha256"][:16]}_{a}_{b}.wav'
            if not target.exists():
                wave,sr=sf.read(source,start=a,stop=b,always_2d=True)
                temporary=target.with_name(target.stem+'_'+uuid.uuid4().hex+'.wav')
                try:sf.write(temporary,wave,sr,subtype='PCM_16');temporary.replace(target)
                finally:temporary.unlink(missing_ok=True)
            return web.FileResponse(target)
        return await endpoint(request,action)

    @routes.get('/h3talk/project/{pid}/prompts')
    async def prompt_versions(request):
        async def action():
            pid=request.match_info['pid']
            with LOCK:
                plan=read_plan(root(),pid)
            value=await asyncio.to_thread(prompts.versions,root(),plan)
            return web.json_response(value)
        return await endpoint(request,action)

    @routes.get('/h3talk/project/{pid}/segment/{index}/preview')
    async def segment_video(request):
        async def action():
            from .canvas import segment_preview
            index=request.match_info['index']
            if not index.isdecimal():raise ValueError('段号无效。')
            path=await asyncio.to_thread(segment_preview,root(),request.match_info['pid'],int(index),raw=request.query.get('kind')=='raw')
            return web.FileResponse(path)
        return await endpoint(request,action)

    @routes.get('/h3talk/project/{pid}/file/{kind}')
    async def file(request):
        async def action():
            plan=read_plan(root(),request.match_info['pid']);directory=project_path(root(),plan['id'])
            kind=request.match_info['kind']
            paths={'audio':audio_file(directory,'source.wav'),'reference':directory/'refs/reference_0.png',
                   'mask':directory/'refs/person_mask.png'}
            if kind=='mask' and project.unmasked(plan):raise ValueError('此项目未使用人物空间遮罩。')
            if kind=='final':
                if not plan.get('final_video'):raise ValueError('完整片尚未生成。')
                path=final_file(root(),plan['final_video'])
            elif kind in paths:path=paths[kind]
            else:raise ValueError('文件类型无效。')
            return web.FileResponse(path)
        return await endpoint(request,action)

    @routes.post('/h3talk/project/{pid}/reveal-final')
    async def reveal_final(request):
        async def action():
            from ..controller import reveal_file
            plan=read_plan(root(),request.match_info['pid'])
            if not plan.get('final_video'):raise ValueError('完整片尚未生成。')
            path=final_file(root(),plan['final_video'])
            revealed=await asyncio.to_thread(reveal_file,path)
            return web.json_response({'path':revealed})
        return await endpoint(request,action)

    @routes.post('/h3talk/project/{pid}/{action}')
    async def post(request):
        async def action():
            pid=request.match_info['pid'];name=request.match_info['action'];data=await request.json()
            with LOCK:
                plan=read_plan(root(),pid)
                if name in ('pause','stop'):
                    plan[name+'_requested']=True
                    if plan['run_status'] in ('running','pausing','stopping'):
                        plan['run_status']='stopping' if plan.get('stop_requested') else 'pausing'
                    write_plan(root(),plan)
                elif name=='run':
                    client_id=str(data.get('client_id') or '')[:128]
                    runner.start(root(),pid,client_id=client_id)
                else:
                    if runner.busy(root(),pid):raise ValueError('请暂停并等待当前段完成后再修改项目。')
                    if name in ('cuts','prompt') and data.get('expected_fingerprint') not in (None,project.fingerprint(plan)):
                        raise ValueError('分段已在其他页面改变，请重新读取后再保存；当前输入仍保留。')
                    if name=='cuts':
                        plan=project.update_cuts(root(),pid,data['cuts'])
                    elif name=='approve':
                        project.verify_assets(root(),plan)
                        plan.update(approved=True,approved_fingerprint=project.fingerprint(plan))
                        write_plan(root(),plan)
                    elif name=='regenerate':
                        plan=project.request_regeneration(root(),pid,int(data['index']))
                    elif name=='prompt':
                        plan=prompts.save_draft(root(),pid,data['index'],data['text'],data['expected_sha256'])
                    elif name=='restore':
                        plan=project.restore_regeneration(root(),pid)
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
                        from .canvas import assemble_project
                        assemble_project(root(),pid)
                    else:raise ValueError('操作无效。')
            return web.json_response(public(read_plan(root(),pid)))
        return await endpoint(request,action)
