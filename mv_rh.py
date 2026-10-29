"""RunningHub segment adapter for the existing MV project controller."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import time
import uuid

from .core import (LOCK, fingerprint, inside, project_path, read_plan,
                   request_regeneration, write_plan)
from .talking_rh.remote import Client, Rejected, validate_config
from .storage import MV_PREFIX

TERMINAL = {'completed', 'failed', 'rejected'}


def pending(job):
    return bool(job and job.get('backend') == 'runninghub' and job.get('status') not in TERMINAL)


def require_settled(plan):
    if any(pending(row.get('job')) for row in plan['segments']):
        raise ValueError('仍有未解决的RH任务，请先继续查询；创建结果未知时请绑定任务ID。未重复提交。')


def save(path, value):
    path = Path(path)
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def config(inputs):
    region = inputs.get('rh_region', '海外版')
    if region not in ('海外版', '国内版'):
        raise ValueError('RH区域无效。')
    value = {'workflow_id':str(inputs.get('rh_workflow_id', '')).strip(),
             'base_url':'https://www.runninghub.ai' if region == '海外版' else 'https://www.runninghub.cn',
             'instance_type':inputs.get('rh_instance_type', 'ultra')}
    validate_config(value)
    return value


def capture(payload):
    prompt = payload.get('prompt') or {}
    loaders = [key for key, node in prompt.items() if node.get('class_type') == 'H3LVUnified']
    if len(loaders) != 1:
        raise ValueError('RH生成需要一个MV Generator节点。')
    loader = loaders[0]
    remote = config(prompt[loader]['inputs'])
    from .controller import reference_node
    _, reference = reference_node(prompt)
    if reference is None:
        raise ValueError('请保留MV画布的Ref2VA提示词连线，以确定本轮提示词来源。')
    source = reference.get('inputs', {}).get('prompt')
    seen = set()
    while isinstance(source, list) and len(source) == 2:
        key, slot = str(source[0]), source[1]
        if key in seen:
            raise ValueError('提示词连线存在循环。')
        seen.add(key)
        node = prompt.get(key, {})
        if key == loader and slot == 11:
            source = {'kind':'segment_prompt'}
            break
        if node.get('class_type') == 'H3LVPromptExpand':
            values = node.get('inputs', {})
            if values.get('material') != [loader, 4]:
                raise ValueError('MV Prompt Expander需要连接当前MV节点的segment_material。')
            settings = {name:values.get(name, default) for name, default in (
                ('mode','vision'), ('model',''), ('rule',''), ('revision',0))}
            if any(isinstance(value, (list, dict)) for value in settings.values()):
                raise ValueError('RH模式扩写配置须直接设置在MV Prompt Expander节点上。')
            source = {'kind':'expansion', 'settings':settings}
            break
        if node.get('class_type') == 'PreviewAny':
            source = node.get('inputs', {}).get('source')
            continue
        raise ValueError('RH模式的提示词请使用MV Prompt Expander或segment_prompt直连。')
    if isinstance(source, str) and source.strip():
        source = {'kind':'literal', 'text':source}
    if not isinstance(source, dict) or 'kind' not in source:
        raise ValueError('没有有效的MV提示词来源。')
    return {'backend':'runninghub', 'remote':remote, 'prompt_source':source,
            'loader_id':loader, 'video_id':str(payload.get('video_id') or ''),
            'client_id':str(payload.get('client_id') or '')[:128]}


def node(graph, source, expected):
    if not isinstance(source, list) or len(source) != 2 or source[1] != 0:
        raise ValueError('RH模板连接不符合MV协议：'+expected)
    key = str(source[0])
    if graph.get(key, {}).get('class_type') != expected:
        raise ValueError('RH模板缺少MV链路节点：'+expected)
    return key


def binding(graph):
    def single(kind):
        keys = [key for key, value in graph.items() if value.get('class_type') == kind]
        if len(keys) != 1:
            raise ValueError('RH模板需要且仅需要一个'+kind+'节点。')
        return keys[0]
    if not isinstance(graph, dict) or any(not isinstance(v,dict) for v in graph.values()):
        raise ValueError('RH模板API图无效。')
    if any(n.get('class_type') in {'KSampler','KSamplerAdvanced','SamplerCustom','SamplerCustomAdvanced'} for n in graph.values()):
        raise ValueError('此MV后端要求原版SelfLift-Avatar，不能使用K采样生成。')
    reference, sampler, output = single('MiniMaxH3ReferenceToVideo'), single('SelfLiftAvatarH3Sampler'), single('SaveVideo')
    creator = node(graph, graph[output]['inputs'].get('video'), 'CreateVideo')
    original = node(graph, graph[creator]['inputs'].get('audio'), 'LoadAudio')
    vocals = node(graph, graph[reference]['inputs'].get('ref_audios.ref_audio_0'), 'LoadAudio')
    if original == vocals:
        raise ValueError('RH模板的原曲与分离人声必须使用两个LoadAudio输入。')
    combined = node(graph, graph[sampler]['inputs'].get('latent_image'), 'LTXVConcatAVLatent')
    mask = node(graph, graph[combined]['inputs'].get('audio_latent'), 'SetLatentNoiseMask')
    zero = node(graph, graph[mask]['inputs'].get('mask'), 'SolidMask')
    encode = node(graph, graph[mask]['inputs'].get('samples'), 'LTXVAudioVAEEncode')
    if graph[zero]['inputs'].get('value') != 0 or graph[encode]['inputs'].get('audio') != [vocals,0]:
        raise ValueError('RH模板必须保留分离人声的音频latent零噪声遮罩。')
    decode = node(graph, graph[creator]['inputs'].get('images'), 'VAEDecode')
    video = node(graph, graph[combined]['inputs'].get('video_latent'), 'LTXVSeparateAVLatent')
    negative = node(graph, graph[sampler]['inputs'].get('negative'), 'ConditioningZeroOut')
    if (graph[sampler]['inputs'].get('positive') != [reference,0]
            or graph[negative]['inputs'].get('conditioning') != [reference,0]
            or graph[video]['inputs'].get('av_latent') != [reference,1]):
        raise ValueError('RH模板的采样条件和视频latent必须来自同一个Ref2VA节点。')
    video_vae = node(graph, graph[reference]['inputs'].get('vae'), 'VAELoader')
    audio_vae = node(graph, graph[reference]['inputs'].get('audio_vae'), 'VAELoader')
    if (video_vae == audio_vae or graph[sampler]['inputs'].get('vae') != [video_vae,0]
            or graph[decode]['inputs'].get('vae') != [video_vae,0]
            or graph[encode]['inputs'].get('audio_vae') != [audio_vae,0]):
        raise ValueError('RH模板VAE连线错误：Avatar和视频解码使用Ref2VA的视频VAE，人声编码使用音频VAE。')
    separate = node(graph, graph[decode]['inputs'].get('samples'), 'LTXVSeparateAVLatent')
    if graph[separate]['inputs'].get('av_latent') != [sampler,0]:
        raise ValueError('RH视频输出必须解码当前Avatar采样结果。')
    seed = graph[sampler]['inputs'].get('seed')
    if type(seed) is not int or seed < 0:
        raise ValueError('请在RH Avatar节点上设置固定整数seed。')
    return dict(reference=reference, sampler=sampler, output=output, creator=creator,
                original=original, vocals=vocals, zero=zero, seed=seed)


def graph_fingerprint(snapshot):
    graph = copy.deepcopy(snapshot.get('remote_graph') or {})
    for value in graph.values():
        value.pop('_meta', None)
        inputs = value.get('inputs', {})
        kind = value.get('class_type')
        if kind in ('LoadImage','LoadAudio'):
            inputs.clear()
        elif kind == 'MiniMaxH3ReferenceToVideo':
            for name in list(inputs):
                if name in ('prompt','length') or name.startswith('ref_images.'):
                    inputs.pop(name)
        elif kind == 'SaveVideo':
            inputs.pop('filename_prefix',None)
    # Reference loader count is bound per segment and must not invalidate old takes.
    graph = {k:v for k,v in graph.items() if v.get('class_type') != 'LoadImage'}
    value = [snapshot['remote'], snapshot['prompt_source'], graph]
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def check_configuration(payload):
    """Read the cloud template only; never upload assets or create a task."""
    snapshot = capture(payload)
    if payload['prompt'][snapshot['loader_id']]['inputs'].get('backend') != 'RunningHub':
        raise ValueError('请先把MV Generator的生成后端设为RunningHub。')
    mapping = binding(Client(snapshot['remote']).workflow())
    return {'workflow_id':snapshot['remote']['workflow_id'], 'seed':mapping['seed'],
            'prompt_source':snapshot['prompt_source']['kind'], 'checked':True}


def start(root, pid, payload, server):
    from . import controller
    with LOCK:
        if pid in controller.TASKS:
            raise ValueError('该项目已经在生成中。')
        plan = read_plan(root,pid)
        if plan.get('run_status') == 'merging':
            raise ValueError('正在合并最终视频，请等待合并完成。')
        from .nodes import require_mv_mode
        require_mv_mode(plan.get('mode'))
        if not plan.get('approved') or plan.get('approved_fingerprint') != fingerprint(plan):
            raise ValueError('请先保存并确认分段方案。')
        snapshot = capture(payload)
        directory = project_path(root,pid)
        path = directory/'state/queue_snapshot.json'
        previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        active = any(pending(row.get('job')) for row in plan['segments'])
        if active:
            if previous.get('backend') != 'runninghub' or any(previous.get(k) != snapshot[k] for k in ('remote','prompt_source')):
                require_settled(plan)
            # Resume the exact frozen graph; canvas or RH edits cannot replace an in-flight task.
            snapshot = previous
        else:
            for row in plan['segments']:
                job = row.get('job') or {}
                if job and job.get('status') not in TERMINAL:
                    raise ValueError('请先解决原本地任务，再切换RH后端。')
            if previous.get('backend') == 'runninghub':
                for key in ('remote_graph','binding','generation_graph_fingerprint'):
                    if key in previous:
                        snapshot[key] = previous[key]
        only = payload.get('only_segment_index')
        if active:
            if only is None:
                only = plan.get('run_only_segment')
            if only is not None and any(pending(row.get('job')) for i,row in enumerate(plan['segments']) if i != int(only)):
                require_settled(plan)
        if only is not None and not 0 <= int(only) < len(plan['segments']):
            raise ValueError('分段编号无效。')
        controller.validate_generation_materials(plan,directory, [int(only)] if only is not None else None)
        if previous and previous.get('backend') != 'runninghub':
            for row in plan['segments']:
                request_regeneration(row,'生成后端已切换为RunningHub')
            if plan.get('final_video'):
                plan['final_stale'] = True
        save(path,snapshot)
        plan.update(run_status='running',pause_requested=False,stop_requested=False,error='',
                    generation_backend='runninghub',video_output_id=snapshot['video_id'])
        if only is None:
            plan.pop('run_only_segment',None)
        else:
            plan['run_only_segment'] = int(only)
        write_plan(root,plan)
        controller.TASKS[pid] = asyncio.create_task(controller.execute_project(root,pid,server))


async def prepare(root,pid,snapshot,client):
    plan = read_plan(root,pid)
    if not any(pending(row.get('job')) for row in plan['segments']):
        graph = await asyncio.to_thread(client.workflow)
        mapping = binding(graph)
        previous = snapshot.get('generation_graph_fingerprint')
        snapshot.update(remote_graph=graph,binding=mapping)
        current = graph_fingerprint(snapshot)
        if previous and previous != current:
            for row in plan['segments']:
                request_regeneration(row,'RH工作流或提示词配置已修改')
            if plan.get('final_video'):
                plan['final_stale'] = True
            write_plan(root,plan)
        snapshot['generation_graph_fingerprint'] = current
        save(project_path(root,pid)/'state/queue_snapshot.json',snapshot)
    binding(snapshot['remote_graph'])
    client.config.update(seed=snapshot['binding']['seed'],seed_node=snapshot['binding']['sampler'])


def build_segment(root,pid,index,snapshot,client,folder):
    import soundfile as sf
    from .nodes import LoadSegment
    from .expansion import PromptExpand
    from .progress import report
    loaded = LoadSegment().load(pid,index)
    material = loaded[4]
    source = snapshot['prompt_source']
    if source['kind'] == 'segment_prompt':
        text = loaded[11].strip()
    elif source['kind'] == 'literal':
        text = source['text']
    else:
        report(root,pid,'expansion',index)
        text = PromptExpand().run(material,**source['settings'])['result'][0]
    if not text.strip():
        raise ValueError('本段没有手写提示词，请填写segment_prompt或使用MV Prompt Expander。')
    graph, mapping = copy.deepcopy(snapshot['remote_graph']), snapshot['binding']
    report(root,pid,'upload',index)
    for value, key, name in zip(loaded[:2],('original','vocals'),('original.wav','vocals.wav')):
        path = folder/name
        sf.write(path,value['waveform'][0].numpy().T,value['sample_rate'],subtype='PCM_16')
        graph[mapping[key]]['inputs']['audio'] = client.upload(path)
    ref = graph[mapping['reference']]['inputs']
    for name in list(ref):
        if name.startswith('ref_images.'):
            ref.pop(name)
    for key,value in list(graph.items()):
        if value['class_type'] == 'LoadImage':
            graph.pop(key)
    for i,path in enumerate(material['paths']):
        key = str(9010+i)
        if key in graph:
            raise ValueError('RH模板9010–9015节点ID须保留给分段参考图。')
        graph[key] = {'class_type':'LoadImage','inputs':{'image':client.upload(path)}}
        ref['ref_images.ref_image_'+str(i)] = [key,0]
    ref.update(prompt=text,length=loaded[2])
    graph[mapping['creator']]['inputs']['fps'] = 24
    graph[mapping['output']]['inputs']['filename_prefix'] = f'{MV_PREFIX}/projects/{pid}/takes/{index:04d}/{folder.name}/generated/video'
    return graph,text


def validate_output(path,graph,row,mapping):
    from .controller import command, probe_video
    info = probe_video(path)
    if info['r_frame_rate'] != '24/1' or int(info['nb_read_frames']) != row['generation_frames']:
        raise ValueError('RH原片的帧数或帧率与本段提交不一致。')
    metadata = json.loads(command(['ffprobe','-v','error','-show_entries','format_tags=prompt','-of','json',str(path)]))
    try:
        actual = json.loads(metadata['format']['tags']['prompt'])
        if set(actual) != set(graph) or any(actual[k]['class_type'] != n['class_type'] or
                any(actual[k]['inputs'].get(field) != value for field,value in n['inputs'].items())
                for k,n in graph.items()):
            raise ValueError
        binding(actual)
    except (KeyError,TypeError,ValueError):
        raise ValueError('RH原片的实际执行图与本段提交不一致；已保留任务和文件，停止后续生成。') from None
    save(path.with_name('actual_graph.json'),actual)
    return {'width':info['width'],'height':info['height'],'frames':row['generation_frames'],
            'actual_seed':actual[mapping['sampler']]['inputs']['seed'],'audio_zero_mask':True,
            'graph_verified':True,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


async def segment(root,pid,index,snapshot,client):
    directory = project_path(root,pid)
    plan = read_plan(root,pid)
    row = plan['segments'][index]
    job = row.get('job')
    if not job:
        folder = directory/'takes'/f'seg_{index:04d}'/uuid.uuid4().hex[:12]
        folder.mkdir(parents=True)
        graph,text = await asyncio.to_thread(build_segment,root,pid,index,snapshot,client,folder)
        save(folder/'submitted_graph.json',graph)
        plan = read_plan(root,pid)
        from .controller import halt_status
        if halt_status(plan):
            return None,None
        job = {'backend':'runninghub','status':'creating_unknown','directory':str(folder),
               'created':time.time(),'final_prompt':text,'remote':snapshot['remote']}
        plan['segments'][index]['job'] = job
        write_plan(root,plan)
        from .progress import report
        report(root,pid,'rh_submitting',index)
        try:
            task_id = await asyncio.to_thread(client.create,graph)
        except Rejected:
            job.update(status='failed',provider_status='REJECTED',error='RH明确拒绝创建；请检查配置后手动重试。')
            plan = read_plan(root,pid);plan['segments'][index]['job'] = job;write_plan(root,plan)
            raise
        job.update(task_id=task_id,status='submitted')
        plan = read_plan(root,pid);plan['segments'][index]['job'] = job;write_plan(root,plan)
    if job.get('backend') != 'runninghub' or not job.get('task_id'):
        raise ValueError('RH创建结果未知，请核对RH任务列表，在该段绑定任务ID后继续。不会自动重建。')
    folder = inside(directory,job['directory'])
    graph = json.loads((folder/'submitted_graph.json').read_text(encoding='utf-8'))
    began = time.monotonic()
    from .progress import report
    report(root,pid,'rh_received',index)
    while True:
        result = await asyncio.to_thread(client.query,job['task_id'])
        status = result.get('status')
        if status in {'PENDING','QUEUED','RUNNING','STARTING'}:
            report(root,pid,'rh_running' if status == 'RUNNING' else 'rh_queued',index)
        if status != job.get('provider_status'):
            job['provider_status'] = status
            plan = read_plan(root,pid)
            plan['segments'][index]['job']['provider_status'] = status
            write_plan(root,plan)
        if status == 'SUCCESS':
            break
        if status in {'FAILED','ERROR','CANCELED','CANCELLED'}:
            plan = read_plan(root,pid)
            plan['segments'][index]['job'].update(status='failed',provider_status=status,error='RH任务失败：'+status)
            write_plan(root,plan)
            raise ValueError(f'第{index+1}段RH任务失败（{status}），请检查云端任务后手动重试。')
        if status not in {'PENDING','QUEUED','RUNNING','STARTING'} or time.monotonic()-began > 5400:
            raise ValueError('RH查询未完成。已保留任务ID，继续时查询同一任务。')
        await asyncio.sleep(10)
    outputs = [x for x in result.get('results',[]) if str(x.get('nodeId')) == snapshot['binding']['output']
               and str(x.get('outputType','')).lower() == 'mp4']
    if len(outputs) != 1:
        raise ValueError('RH所选保存节点没有返回唯一MP4；已保留任务ID。')
    path = folder/'raw.mp4'
    report(root,pid,'download',index)
    await asyncio.to_thread(client.download,outputs[0]['url'],path)
    report(root,pid,'validate',index)
    try:
        audit = await asyncio.to_thread(validate_output,path,graph,row,snapshot['binding'])
    except ValueError as exc:
        plan = read_plan(root,pid)
        plan['segments'][index]['job'].update(status='failed',provider_status='INVALID_OUTPUT',error=str(exc))
        write_plan(root,plan)
        raise
    save(folder/'execution_audit.json',audit)
    from .controller import command
    preview = folder/'preview.mp4'
    if not preview.exists():
        temporary = folder/'preview.tmp.mp4'
        await asyncio.to_thread(command,['ffmpeg','-v','error','-y','-i',str(path),'-frames:v',str(row['edit_frames']),
            '-t',str(row['edit_frames']/24),'-c:v','libx264','-crf','18','-pix_fmt','yuv420p',
            '-c:a','aac','-movflags','+faststart',str(temporary)])
        temporary.replace(preview)
    plan = read_plan(root,pid)
    plan['segments'][index]['job'].update(audit=audit,preview_video=str(preview))
    write_plan(root,plan)
    return str(path),job['final_prompt']
