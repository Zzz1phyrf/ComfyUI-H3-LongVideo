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
from .project import digest, dimensions, fingerprint, latent_frames, verify_assets
from .remote import Client, Rejected

THREADS = {}


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
    width,height = dimensions(config['megapixels'])
    if (info['width'],info['height'],info['r_frame_rate'],int(info['nb_read_frames'])) != (width,height,'24/1',row['generation_frames']):
        raise ValueError('原片的分辨率、帧率或长度与实际提交参数不一致，已停止后续生成。')
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
    return {'actual_seed':actual['270']['inputs']['seed'],'audio_zero_mask':actual['249']['inputs']['value']==0,
            'width':width,'height':height,'frames':row['generation_frames']}


def run_project(root,pid,client,*,validate=validate_outputs,assemble=controller.assemble,sleep=time.sleep):
    directory = project_path(root,pid)
    plan = read_plan(root,pid)
    verify_assets(root,plan)
    if not plan.get('approved') or plan.get('approved_fingerprint')!=fingerprint(plan):
        raise ValueError('请先保存并确认分段。')
    for index in range(len(plan['segments'])):
        plan = read_plan(root,pid)
        if plan.get('pause_requested'):
            change(root,pid,lambda p:p.update(run_status='paused'))
            return
        row = plan['segments'][index]
        job = row.get('job')
        if job and job.get('status')=='completed':
            for kind in ('video','latent'):
                path = inside(directory,job[kind])
                if digest(path)!=job[kind+'_sha256']:
                    raise ValueError('已完成片段的文件已变化，请检查项目文件；没有自动重生成。')
            continue
        local_reserved=plan['config'].get('backend')=='local' and job and job.get('task_id')
        if job and (job.get('status') in ('failed','rejected') or job.get('status')=='creating_unknown' and not local_reserved):
            raise ValueError('该段任务需人工处理：未知创建请绑定RH任务ID，明确失败请点击“重试失败段”。')
        if not job:
            attempt = uuid.uuid4().hex[:12]
            folder = directory/'takes'/f'{index:04d}'/attempt
            folder.mkdir(parents=True)
            wave,sr = sf.read(audio_file(directory,'source.wav'),dtype='float32',always_2d=True)
            condition = np.zeros((row['generation_frames']*1000,1),dtype='float32')
            part = wave[row['audio_start_sample']:row['audio_start_sample']+len(condition)].mean(axis=1,keepdims=True)
            condition[:len(part)] = part
            sf.write(folder/'condition.wav',condition,sr,subtype='FLOAT')
            uploaded = {'attempt':attempt,'audio':client.upload(folder/'condition.wav'),
                        'references':[client.upload(directory/'refs'/a['name']) for a in plan['assets'][:-1]],
                        'mask':client.upload(directory/'refs/person_mask.png')}
            previous = plan['segments'][index-1] if index else None
            if previous:
                uploaded['previous_latent'] = client.upload(previous['job']['latent'])
            graph = build(plan,row,uploaded,previous)
            (folder/'submitted_graph.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
            if read_plan(root,pid).get('pause_requested'):
                change(root,pid,lambda p:p.update(run_status='paused'))
                return
            job = {'status':'creating_unknown','directory':str(folder),'created':time.time()}
            if plan['config'].get('backend')=='local':job['task_id']=client.reserve()
            change(root,pid,lambda p:p['segments'][index].update(job=job))
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
        while True:
            result = client.query(job['task_id'])
            status = result.get('status')
            if status=='SUCCESS':
                break
            if status in ('FAILED','ERROR','CANCELED','CANCELLED','MISSING'):
                job.update(status='failed',provider_status=status)
                change(root,pid,lambda p:p['segments'][index].update(job=job))
                reason='本地任务已不在队列或历史中（可能执行时重启过）' if status=='MISSING' else '生成任务失败'
                raise ValueError(f'第{index+1}段{reason}，任务号{job["task_id"]}；已保留文件，请检查后手动重试该段。')
            if status not in ('PENDING','QUEUED','RUNNING','STARTING'):
                raise ValueError('任务查询没有返回有效状态，已保留任务ID供继续查询。')
            if time.monotonic()-began>5400:
                raise TimeoutError('本次等待超过90分钟，已保留任务ID；继续时查询同一任务。')
            sleep(15)
        selected = {}
        for item in result.get('results') or []:
            node = str(item.get('nodeId'))
            if node not in ('273','9200'):
                continue
            kind = 'video' if node=='273' else 'latent'
            if kind in selected:
                raise ValueError('任务返回多个同名输出，已停止以免误用。')
            suffix = str(item.get('outputType','')).lower()
            if suffix not in (('mp4',) if kind=='video' else ('latent',)):
                raise ValueError('输出类型与口播模板不一致。')
            selected[kind] = folder/('raw.mp4' if kind=='video' else 'video.latent')
            client.download(item['url'],selected[kind])
        if set(selected)!= {'video','latent'}:
            raise ValueError('缺少视频或续接latent。已保存任务ID，不会重建任务。')
        audit = validate(selected,graph,row,plan['config'])
        (folder/'execution_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
        job.update(status='completed',edit_start_frame=row['edit_start_frame'],**{k:str(v) for k,v in selected.items()},
                   **{k+'_sha256':digest(v) for k,v in selected.items()})
        change(root,pid,lambda p:p['segments'][index].update(job=job))
    if read_plan(root,pid).get('pause_requested'):
        change(root,pid,lambda p:p.update(run_status='paused'))
        return
    change(root,pid,lambda p:p.update(run_status='merging'))
    final = assemble(root,pid)
    change(root,pid,lambda p:p.update(run_status='completed',final_video=final,final_stale=False))


def start(root,pid,client_factory=make_client):
    with LOCK:
        directory = project_path(root,pid)
        handle = claim(directory)
        try:
            plan = read_plan(root,pid)
            if not plan.get('approved') or plan.get('approved_fingerprint')!=fingerprint(plan):
                raise ValueError('请先保存并确认分段。')
            client = client_factory(plan['config'])
            verify_assets(root,plan)
            if plan.get('run_status')=='completed' and not plan.get('final_stale') and Path(plan.get('final_video','')).is_file():
                handle.close()
                return
            plan.update(pause_requested=False,run_status='running',error='')
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
