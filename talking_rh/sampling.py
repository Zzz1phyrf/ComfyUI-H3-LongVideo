"""Freeze the user's RH sampler/model graph; bind only talking project inputs."""
import copy
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def link(value, graph):
    return isinstance(value,list) and len(value)==2 and str(value[0]) in graph and type(value[1]) is int


def ancestors(graph, roots, stop=()):
    found=set()
    def visit(key):
        key=str(key)
        if key in found or key in stop:return
        if key not in graph:raise ValueError('RH采样配置有缺失连接：'+key)
        found.add(key)
        for value in graph[key]['inputs'].values():
            if link(value,graph):visit(value[0])
    for key in roots:visit(key)
    return found


def binding(graph):
    samplers=[key for key,n in graph.items() if n['class_type'] in ('SelfLiftAvatarH3Sampler','SamplerCustom')]
    if len(samplers)!=1:raise ValueError('RH口播配置需要且仅需要一个SelfLift-Avatar或标准SamplerCustom采样器。')
    sampler=samplers[0];inputs=graph[sampler]['inputs']
    source=inputs.get('positive')
    if not link(source,graph):raise ValueError('RH Avatar正面条件连接无效。')
    refs=[key for key in ancestors(graph,[source[0]]) if graph[key]['class_type'] in
          ('MiniMaxH3ReferenceToVideo','MiniMaxH3ImageToVideo')]
    if len(refs)!=1:raise ValueError('RH采样条件需连接一个原生H3参考图或首尾帧节点。')
    reference=refs[0];condition=graph[reference]['inputs']
    fields=('model','sampler','sigmas') if graph[sampler]['class_type']=='SamplerCustom' else ('low_res_model','sampler','sigmas','vae')
    for field in fields:
        if not link(inputs.get(field),graph):raise ValueError('RH Avatar缺少连接：'+field)
    for field in ('clip','vae'):
        if not link(condition.get(field),graph):raise ValueError('RH参考条件缺少连接：'+field)
    return sampler,reference


def freeze(source,prepared_audio=False):
    if not isinstance(source,dict) or not source:
        raise ValueError('RH没有返回有效工作流；没有回退到内置采样配置。')
    graph={str(k):{'class_type':n['class_type'],'inputs':copy.deepcopy(n['inputs'])} for k,n in source.items()}
    sampler,reference=binding(graph)
    inputs=graph[sampler]['inputs'];condition=graph[reference]['inputs']
    combined=inputs.get('latent_image')
    if not link(combined,graph) or graph[str(combined[0])]['class_type']!='LTXVConcatAVLatent':
        raise ValueError('RH Avatar输入需要视频与零遮罩音频latent合并链路。')
    audio=graph[str(combined[0])]['inputs'].get('audio_latent')
    if not link(audio,graph) or graph[str(audio[0])]['class_type']!='SetLatentNoiseMask':
        raise ValueError('RH采样配置缺少音频零噪声遮罩。')
    mask=graph[str(audio[0])]['inputs'].get('mask')
    if (not link(mask,graph) or graph[str(mask[0])]['class_type']!='SolidMask'
            or graph[str(mask[0])]['inputs'].get('value')!=0):
        raise ValueError('RH音频噪声遮罩必须全零；没有删除或替换该遮罩。')
    encoded=graph[str(audio[0])]['inputs'].get('samples')
    if not link(encoded,graph) or graph[str(encoded[0])]['class_type']!='LTXVAudioVAEEncode':
        raise ValueError('RH音频遮罩需要连接原生音频VAE编码。')
    audio_vae=graph[str(encoded[0])]['inputs'].get('audio_vae')
    if not link(audio_vae,graph):raise ValueError('RH没有有效音频VAE。')
    if (graph[reference]['class_type']=='MiniMaxH3ReferenceToVideo'
            and condition.get('ref_audios.ref_audio_0') is not None
            and condition.get('audio_vae')!=audio_vae):
        raise ValueError('RH参考音频与零罩原声须使用同一音频VAE；没有静默改写云端配置。')
    # Image/audio assets and the old positive guides are project inputs, not
    # sampler settings. Do not persist unused workflow nodes or their secrets.
    graph[reference]['inputs']={k:copy.deepcopy(condition[k]) for k in ('clip','vae','ref_image_size') if k in condition}
    graph[reference]['inputs']['audio_vae']=audio_vae
    reference_audio=condition.get('ref_audios.ref_audio_0')
    if prepared_audio:
        if (graph[reference]['class_type']!='MiniMaxH3ReferenceToVideo' or not link(reference_audio,graph)
                or (graph[str(reference_audio[0])]['class_type'],reference_audio[1]) not in
                   (('AudioSeparation',3),('LoadAudio',0))):
            raise ValueError('RH口播Ref2VA须连接有效的参考人声音频。')
        if any(k.startswith('ref_audios.') and k!='ref_audios.ref_audio_0' for k in condition):
            raise ValueError('口播只提供一条参考人声音频。')
    elif reference_audio is not None:
        if (not link(reference_audio,graph) or reference_audio[1]!=3
                or graph[str(reference_audio[0])]['class_type']!='AudioSeparation'):
            raise ValueError('RH口播参考音频须连接AudioSeparation的人声输出。')
        separation=graph[str(reference_audio[0])]
        separation['inputs']={k:copy.deepcopy(v) for k,v in separation['inputs'].items()
                              if k in ('chunk_fade_shape','chunk_length','chunk_overlap')}
        graph[reference]['inputs']['ref_audios.ref_audio_0']=reference_audio
    inputs['positive']=[reference,0]
    inputs.pop('latent_image',None)
    keep=ancestors(graph,[sampler,reference])
    graph={k:graph[k] for k in sorted(keep)}
    if any(n['class_type'] in {'KSampler','KSamplerAdvanced','SamplerCustomAdvanced',
            'LoadImage','LoadAudio','SaveVideo','VHS_VideoCombine'} for n in graph.values()):
        raise ValueError('RH采样主干含额外素材或非Avatar生成节点，不能静默改写为内置参数。')
    value={'graph':graph}
    if prepared_audio:value['prepared_audio']=True
    value['sha256']=digest(value if prepared_audio else graph)
    return value


def verify(snapshot):
    expected=({'graph':snapshot['graph'],'prepared_audio':True} if snapshot.get('prepared_audio') else snapshot['graph'])
    if digest(expected)!=snapshot.get('sha256'):
        raise ValueError('项目冻结的RH采样配置已变化，请保留旧项目并新建项目。')
    binding(snapshot['graph'])


def summary(snapshot):
    verify(snapshot);graph=snapshot['graph'];sampler,_=binding(graph)
    inputs=graph[sampler]['inputs']
    return {'source_sampler_id':sampler,'sampler_class':graph[sampler]['class_type'],'sha256':snapshot['sha256'],
            'models':[n['inputs']['unet_name'] for n in graph.values() if n['class_type']=='UNETLoader'],
            'loras':[{'name':n['inputs']['lora_name'],'strength':n['inputs']['strength_model']}
                     for n in graph.values() if n['class_type']=='LoraLoaderModelOnly'],
            'schedulers':[{'class_type':n['class_type'],'inputs':n['inputs']} for n in graph.values()
                          if n['class_type'] in ('BasicScheduler','H3SigmaRefiner')],
            'avatar':{k:v for k,v in inputs.items() if not link(v,graph)}}


def apply(value, snapshot, *, ref2va=False):
    verify(snapshot);source=snapshot['graph'];sampler,reference=binding(source)
    condition=source[reference]['inputs']
    audio_vae=condition.get('audio_vae')
    if not link(audio_vae,source):
        # Read raw graphs in regression probes; production freeze resolves this.
        raise ValueError('冻结RH配置缺少音频VAE。')
    selected=[v[0] for k,v in source[sampler]['inputs'].items()
              if k not in ('positive','latent_image') and link(v,source)]
    selected += [condition[k][0] for k in ('clip','vae','audio_vae')]
    if ref2va and not snapshot.get('prepared_audio'):
        reference_audio=condition.get('ref_audios.ref_audio_0')
        if not link(reference_audio,source):raise ValueError('冻结RH配置缺少Ref2VA参考人声。')
        selected.append(reference_audio[0])
    keep=ancestors(source,selected,stop={reference})
    mapping={key:str(10000+i) for i,key in enumerate(sorted(keep))}
    mapping[reference]='268'
    def remap(item):
        return [mapping[str(item[0])],item[1]] if link(item,source) else copy.deepcopy(item)
    for key in keep:
        node=source[key]
        value[mapping[key]]={'class_type':node['class_type'],
                             'inputs':{k:remap(v) for k,v in node['inputs'].items()}}
    old=value['270']['inputs']
    inputs={k:remap(v) for k,v in source[sampler]['inputs'].items() if k not in ('positive','latent_image','seed','noise_seed')}
    seed_field='noise_seed' if source[sampler]['class_type']=='SamplerCustom' else 'seed'
    inputs.update(positive=old['positive'],latent_image=old['latent_image'],**{seed_field:old['seed']})
    value['270']={'class_type':source[sampler]['class_type'],'inputs':inputs}
    video=remap(condition['vae']);audio=remap(audio_vae)
    value['268']['inputs'].update(clip=remap(condition['clip']),vae=video)
    if value['268']['class_type']=='MiniMaxH3ReferenceToVideo':
        value['268']['inputs']['ref_image_size']=condition.get('ref_image_size','match')
    if ref2va:
        if snapshot.get('prepared_audio'):
            target=['9002',0]
            if value.get('9002',{}).get('class_type')!='LoadAudio':raise ValueError('缺少项目已准备的参考人声。')
        else:
            target=remap(reference_audio)
            value[target[0]]['inputs']['audio']=['9000',0]
        value['268']['inputs'].update(audio_vae=audio,**{'ref_audios.ref_audio_0':target})
    value['248']['inputs']['audio_vae']=audio
    if '269' in value:value['269']['inputs']['audio_vae']=audio
    if value['9101']['class_type']=='VAEEncode':value['9101']['inputs']['vae']=video
    value['122']['inputs']['vae']=video
    # Only executable ancestors survive: stale Turbo/refiner defaults must not
    # remain in the submission, even as disconnected nodes.
    used=ancestors(value,['273','9200'])
    return {k:n for k,n in value.items() if k in used}
