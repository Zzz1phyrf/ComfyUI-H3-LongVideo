"""Shared durable segment orchestration for local ComfyUI and RunningHub."""
import json
import os
from pathlib import Path
import threading
import time
import uuid

import numpy as np
import soundfile as sf

from .. import controller
from ..core import LOCK, audio_file, inside, project_path, read_plan, write_plan
from .graph import build
from .project import digest, output_size, fingerprint, latent_frames, verify_assets, reference_assets, unmasked, verify_saved_job, condition_audio
from .remote import Client, Rejected
from .canvas import output_ids
from . import overlap

THREADS = {}


def report(root, pid, stage, index=None):
    def update(plan):
        prior=plan.get('progress',{})
        if prior.get('stage')!=stage or prior.get('index')!=index:
            plan['progress']={'stage':stage,'index':index,'since':time.time()}
    change(root,pid,update)


def make_client(config):
    if config.get('backend')=='local':
        from .local import LocalClient
        return LocalClient(config)
    return Client(config)


def claim(directory):
    handle = (directory/'state/run.lock').open('a+b')
    if not handle.tell():
        handle.write(b'0');handle.flush()
    handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise ValueError('此项目已有控制进程运行，请使用原窗口查看进度。') from None
    return handle


def busy(root,pid):
    try:
        handle = claim(project_path(root,pid))
    except ValueError:
        return True
    handle.close()
    return False


def change(root,pid,fn):
    with LOCK:
        plan = read_plan(root,pid)
        fn(plan)
        write_plan(root,plan)
        return plan


def validate_outputs(paths,graph,row,config):
    from safetensors import safe_open
    info = controller.probe_video(paths['video'])
    width,height = output_size(config)
    if (info['width'],info['height'],info['r_frame_rate'],int(info['nb_read_frames'])) != (width,height,'24/1',row['generation_frames']):
        raise ValueError('原片的分辨率、帧率或长度与实际提交参数不一致，已停止后续生成。')
    if not overlap.enabled({'config':config}):
        with safe_open(str(paths['latent']),framework='np') as source:
            if source.get_slice('latent_tensor').get_shape() != [1,24,latent_frames(row['generation_frames']),height//16,width//16]:
                raise ValueError('返回的续接latent形状无效，已保留任务供检查。')
    result = controller.command(['ffprobe','-v','error','-show_entries','format_tags=prompt','-of','json',str(paths['video'])])
    actual = json.loads(json.loads(result)['format']['tags']['prompt'])
    for key,node in graph.items():
        if actual[key]['class_type'] != node['class_type']:
            raise ValueError('实际执行节点与提交图不一致。')
        for name,value in node['inputs'].items():
            if name!='filename_prefix' and actual[key]['inputs'].get(name)!=value:
                raise ValueError(f'实际执行参数与提交图不一致：{key}/{name}。')
    Path(paths['video']).with_name('actual_graph.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2),encoding='utf-8')
    canvas=config.get('canvas',{})
    sampler=actual[canvas.get('sampler_id','270')]
    seed=sampler['inputs']['noise_seed' if sampler['class_type']=='SamplerCustom' else 'seed']
    if isinstance(seed,list):seed=config['seed']
    if canvas:
        source=actual[canvas['audio_mask_id']]['inputs']['mask']
        zero=actual[str(source[0])]['inputs']['value']==0
    else:zero=actual['249']['inputs']['value']==0
    audit={'actual_seed':seed,'audio_zero_mask':zero,
           'width':width,'height':height,'frames':row['generation_frames']}
    audit['person_spatial_mask']=not unmasked({'config':config})
    if config.get('rh_sampling'):
        from .sampling import summary
        audit['rh_sampling']=summary(config['rh_sampling'])
        audit['explicit_project_bindings']={'seed':seed,'width':width,'height':height,
             'frames':row['generation_frames'],'crop_start':row['edit_start_frame'],
             'kept_frames':row['edit_frames'],'fixed_prompt':True,'continuation_unchanged':True}
        if overlap.enabled({'config':config}):
            audit['explicit_project_bindings'].update(continuation_unchanged=False,independent_window=True,
                final_crop_pending=True,audio_start_sample=row['audio_start_sample'])
    return audit


def validate_prefix(previous,current,tail):
    """Compare saved outputs, not the intended mask or submitted graph."""
    from safetensors import safe_open
    from .project import PREFIX_TOKENS
    with safe_open(str(previous),framework='np') as source:
        expected=source.get_slice('latent_tensor')[:,:,tail:tail+PREFIX_TOKENS]
    with safe_open(str(current),framework='np') as source:
        actual=source.get_slice('latent_tensor')[:,:,:PREFIX_TOKENS]
    if expected.shape!=actual.shape or not np.isfinite(expected).all() or not np.array_equal(expected,actual):
        raise ValueError('实际输出的续接前缀未完整保留上一段尾部，已停止后续生成，请检查采样器与时间遮罩。')
    return {'bit_exact':True,'previous_tail_start':tail,'prefix_tokens':PREFIX_TOKENS}


def halt_requested(root,pid):
    plan=read_plan(root,pid)
    state='stopped' if plan.get('stop_requested') else 'paused' if plan.get('pause_requested') else None
    if state:change(root,pid,lambda p:p.update(run_status=state))
    return state


def run_project(root,pid,client,*,validate=validate_outputs,assemble=None,sleep=time.sleep):
    directory = project_path(root,pid)
    plan = read_plan(root,pid)
    verify_assets(root,plan)
    if not plan.get('approved') or plan.get('approved_fingerprint')!=fingerprint(plan):
        raise ValueError('请先保存并确认分段。')
    for index in range(len(plan['segments'])):
        plan = read_plan(root,pid)
        if halt_requested(root,pid):return
        row = plan['segments'][index]
        job = row.get('job')
        if job and job.get('status')=='completed':
            verify_saved_job(directory,job)
            continue
        local_reserved=plan['config'].get('backend')=='local' and job and job.get('task_id')
        if job and (job.get('status') in ('failed','rejected') or job.get('status')=='creating_unknown' and not local_reserved):
            raise ValueError('该段任务需人工处理：未知创建请绑定RH任务ID，明确失败请点击“重试失败段”。')
        if not job:
            report(root,pid,'prepare',index)
            attempt = uuid.uuid4().hex[:12]
            folder = directory/'takes'/f'{index:04d}'/attempt
            folder.mkdir(parents=True)
            wave,sr = sf.read(audio_file(directory,'source.wav'),dtype='float32',always_2d=True)
            condition = condition_audio(wave,row)
            sf.write(folder/'condition.wav',condition,sr,subtype='FLOAT')
            report(root,pid,'upload',index)
            audio=(client.upload(folder/'condition.wav',attempt=attempt) if plan['config'].get('backend')=='local'
                   else client.upload(folder/'condition.wav'))
            uploaded = {'attempt':attempt,'audio':audio,
                        'references':[client.upload(directory/'refs'/a['name']) for a in reference_assets(plan)]}
            if not unmasked(plan):uploaded['mask']=client.upload(directory/'refs/person_mask.png')
            previous = plan['segments'][index-1] if index and not overlap.enabled(plan) else None
            if previous:
                uploaded['previous_latent'] = client.upload(previous['job']['latent'])
            graph = build(plan,row,uploaded,previous)
            (folder/'submitted_graph.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
            if halt_requested(root,pid):return
            job = {'status':'creating_unknown','directory':str(folder),'created':time.time()}
            if plan['config'].get('backend')=='local':job['task_id']=client.reserve()
            change(root,pid,lambda p:p['segments'][index].update(job=job))
            report(root,pid,'submitting',index)
            try:
                task_id = client.create(graph,job['task_id']) if plan['config'].get('backend')=='local' else client.create(graph)
            except Rejected:
                job['status']='rejected'
                change(root,pid,lambda p:p['segments'][index].update(job=job))
                raise
            job.update(task_id=task_id,status='submitted')
            change(root,pid,lambda p:p['segments'][index].update(job=job))
        folder = inside(directory,job['directory'])
        graph = json.loads((folder/'submitted_graph.json').read_text(encoding='utf-8'))
        began = time.monotonic()
        report(root,pid,'querying',index)
        while True:
            result = client.query(job['task_id'])
            status = result.get('status')
            if status=='SUCCESS':
                job['provider_status']='SUCCESS'
                change(root,pid,lambda p:p['segments'][index].update(job=job))
                break
            if status in ('FAILED','ERROR','CANCELED','CANCELLED','MISSING'):
                job.update(status='failed',provider_status=status)
                change(root,pid,lambda p:p['segments'][index].update(job=job))
                reason='本地任务已不在队列或历史中（可能执行时重启过）' if status=='MISSING' else '生成任务失败'
                raise ValueError(f'第{index+1}段{reason}，任务号{job["task_id"]}；已保留文件，请检查后手动重试该段。')
            if status not in ('PENDING','QUEUED','RUNNING','STARTING'):
                raise ValueError('任务查询没有返回有效状态，已保留任务ID供继续查询。')
            if job.get('provider_status')!=status:
                job['provider_status']=status
                change(root,pid,lambda p:p['segments'][index].update(job=job))
            report(root,pid,'running' if status=='RUNNING' else 'queued',index)
            if time.monotonic()-began>5400:
                raise TimeoutError('本次等待超过90分钟，已保留任务ID；继续时查询同一任务。')
            sleep(15)
        report(root,pid,'download',index)
        selected = {};outputs=output_ids(plan['config'])
        for item in result.get('results') or []:
            node = str(item.get('nodeId'))
            if node not in outputs.values():
                continue
            kind = next(k for k,v in outputs.items() if v==node)
            if kind in selected:
                raise ValueError('任务返回多个同名输出，已停止以免误用。')
            suffix = str(item.get('outputType','')).lower()
            if suffix not in (('latent',) if kind=='latent' else ('mp4',)):
                raise ValueError('输出类型与口播模板不一致。')
            selected[kind] = folder/({'video':'raw.mp4','latent':'video.latent','preview':'preview.mp4'}[kind])
            client.download(item['url'],selected[kind])
        report(root,pid,'validate',index)
        required={'video'} if overlap.enabled(plan) else {'video','latent'}
        if not required.issubset(selected) or ('preview' in outputs and 'preview' not in selected):
            if overlap.enabled(plan):raise ValueError('任务未返回本窗口原片视频，请检查SaveVideo输出；已保存任务ID，继续只查询同一任务。')
            raise ValueError('缺少视频或续接latent。已保存任务ID，不会重建任务。')
        audit = validate(selected,graph,row,plan['config'])
        if index and plan['config'].get('talking_profile')=='ref2va-v1':
            previous=read_plan(root,pid)['segments'][index-1]
            if plan['config'].get('backend')!='local' and graph['270']['class_type']=='SamplerCustom':
                from .prefix import canonicalize
                raw=selected['latent']
                selected['latent'],proof=canonicalize(previous['job']['latent'],raw,folder/'carry.latent',previous['tail_token_start'])
                selected['raw_latent']=raw
                audit['raw_continuation_prefix']=proof
            audit['continuation_prefix']=validate_prefix(previous['job']['latent'],selected['latent'],previous['tail_token_start'])
        if 'preview' in selected:
            preview=controller.probe_video(selected['preview'])
            if int(preview['nb_read_frames'])!=row['edit_frames'] or preview['r_frame_rate']!='24/1':
                raise ValueError('本段预览的裁切或帧率与实际保留内容不一致。')
        (folder/'execution_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
        job.update(status='completed',edit_start_frame=row['edit_start_frame'],**{k:str(v) for k,v in selected.items()},
                   **{k+'_sha256':digest(v) for k,v in selected.items()})
        if overlap.enabled(plan):job['talking_profile']=overlap.PROFILE
        change(root,pid,lambda p:p['segments'][index].update(job=job))
        if overlap.enabled(plan):
            report(root,pid,'seams',index)
            with LOCK:overlap.resolve(root,read_plan(root,pid))
    if halt_requested(root,pid):return
    change(root,pid,lambda p:p.update(run_status='merging'))
    report(root,pid,'merge',len(plan['segments'])-1)
    # start() already owns the run claim; the manual entry claims it separately.
    from .canvas import assemble_ready_project
    assemble_ready_project(root,pid,assemble=assemble)


def start(root,pid,client_factory=make_client,client_id=None):
    with LOCK:
        directory = project_path(root,pid)
        handle = claim(directory)
        try:
            plan = read_plan(root,pid)
            if not plan.get('approved') or plan.get('approved_fingerprint')!=fingerprint(plan):
                raise ValueError('请先保存并确认分段。')
            client = client_factory(plan['config'])
            if plan['config'].get('backend')=='local':client.client_id=client_id
            verify_assets(root,plan)
            if plan.get('run_status')=='completed' and not plan.get('final_stale') and Path(plan.get('final_video','')).is_file():
                handle.close()
                return
            plan.update(pause_requested=False,stop_requested=False,run_status='running',error='')
            plan['progress']={'stage':'accepted','since':time.time()}
            write_plan(root,plan)
        except Exception:
            handle.close();raise
        def work():
            try:
                run_project(root,pid,client)
            except Exception as exc:
                message = str(exc)
                secret = os.environ.get('RUNNINGHUB_API_KEY','')
                if secret: message = message.replace(secret,'[REDACTED]')
                change(root,pid,lambda p:p.update(run_status='failed',error=message[:1200]))
            finally:
                handle.close()
                with LOCK:THREADS.pop(pid,None)
        thread = threading.Thread(target=work,name=f'h3talk-{pid[:8]}',daemon=True)
        THREADS[pid]=thread
        thread.start()
