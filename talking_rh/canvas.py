"""Visible local canvas capture, persisted segment inputs and explicit assembly."""
import copy
import hashlib
import json
import math
from pathlib import Path
import uuid

import numpy as np
from PIL import Image
import soundfile as sf

from ..core import audio_file, inside, project_path, read_plan, write_plan
from ..storage import TALKING_PREFIX
from . import project, overlap

SAMPLERS = ('SelfLiftAvatarH3Sampler','SamplerCustom')

FIELDS = [
    ('condition_audio','AUDIO'), ('preview_audio','AUDIO'), ('reference_image','IMAGE'),
    ('person_mask','MASK'), ('generation_frames','INT'), ('width','INT'), ('height','INT'),
    ('seed','INT'), ('use_endpoint','BOOLEAN'), ('has_previous','BOOLEAN'),
    ('previous_latent','LATENT'), ('tail_token_start','INT'), ('fixed_prompt','STRING'),
    ('fps','FLOAT'), ('raw_prefix','STRING'), ('latent_prefix','STRING'),
    ('preview_prefix','STRING'), ('crop_start','INT'), ('keep_frames','INT'),
    *[(f'reference_{i}','IMAGE') for i in range(2,7)],
    ('lowres_scale','FLOAT'),
    ('reference_audio','AUDIO'),
]
SLOT = {name:i+3 for i,(name,_) in enumerate(FIELDS)}
RETURN_TYPES = ('STRING','STRING','INT', *(kind for _,kind in FIELDS))
# Keep serialized output indices and internal keys compatible with old canvases.
RETURN_NAMES = ('project_id','final_video','segment_count',
                *('video_generation_mask' if name=='person_mask' else name for name,_ in FIELDS))


def blocked(plan):
    from comfy_execution.graph import ExecutionBlocker
    return (plan['id'],plan.get('final_video',''),len(plan['segments']),
            *(ExecutionBlocker(None) for _ in FIELDS))


def ancestors(prompt, key, stop):
    seen=set()
    def visit(node):
        node=str(node)
        if node in seen:return
        if node not in prompt:raise ValueError('画布含缺失的节点连线。')
        seen.add(node)
        if node==stop:return
        for value in prompt[node].get('inputs',{}).values():
            if isinstance(value,list) and len(value)==2 and isinstance(value[1],int):visit(value[0])
    visit(key)
    return seen


def capture(prompt, loader, independent=False, prepared_audio=False):
    loader=str(loader)
    if not prompt or loader not in prompt:raise ValueError('画布模式需要完整工作流，请重新导入口播展开示例。')
    video=[];latents=[];previews=[]
    for key,node in prompt.items():
        if node['class_type'] not in ('SaveVideo','SaveLatent'):continue
        ids=ancestors(prompt,key,loader)
        if loader not in ids or not any(prompt[n]['class_type'] in SAMPLERS for n in ids):continue
        if node['class_type']=='SaveLatent':latents.append(key);continue
        source=node['inputs'].get('video')
        create=prompt.get(str(source[0]),{}) if isinstance(source,list) else {}
        if create.get('class_type')!='CreateVideo':raise ValueError('展开示例的视频保存须连接CreateVideo。')
        pixels=create['inputs'].get('images')
        decoder=prompt.get(str(pixels[0]),{}) if isinstance(pixels,list) else {}
        if decoder.get('class_type')=='VAEDecode':video.append(key)
        elif decoder.get('class_type')=='ImageFromBatch':previews.append(key)
    if independent:
        if len(video)!=1 or latents or previews:
            raise ValueError('独立窗口画布需要唯一的原片SaveVideo；采用区间由本地审核面板裁切，不连接续接latent保存。')
        ids=ancestors(prompt,video[0],loader)
        saved={key:copy.deepcopy(prompt[key]) for key in sorted(ids)}
        saved[loader]={'class_type':'H3LVTalkingSegmentState','inputs':{}}
        samplers=[k for k in ids if prompt[k]['class_type'] in SAMPLERS]
        masks=[k for k in ids if prompt[k]['class_type']=='SetLatentNoiseMask']
        if len(samplers)!=1 or len(masks)!=1:
            raise ValueError('独立窗口需要唯一的标准K采样器和音频零罩。')
        result={'prompt':saved,'loader_id':loader,'video_id':video[0],
                'sampler_id':samplers[0],'audio_mask_id':masks[0],'profile':overlap.PROFILE}
        if prepared_audio:prepare_reference(result)
        verify_ref2va(result)
        return result
    if len(video)!=1 or len(latents)!=1 or len(previews)!=1:
        raise ValueError('画布需各有一条原片SaveVideo、续接SaveLatent和裁切预览SaveVideo链路。')
    ids=set().union(*(ancestors(prompt,k,loader) for k in [*video,*latents,*previews]))
    samplers=[k for k in ids if prompt[k]['class_type'] in SAMPLERS]
    if len(samplers)!=1 or any(prompt[k]['class_type'] in ('KSampler','KSamplerAdvanced','SamplerCustomAdvanced') for k in ids):
        raise ValueError('口播展开工作流需要唯一的SelfLift-Avatar或标准SamplerCustom采样链路。')
    masks=[k for k in ids if prompt[k]['class_type']=='SetLatentNoiseMask']
    audio_masks=[]
    for key in masks:
        node=prompt[key]['inputs'];source=node.get('samples')
        if isinstance(source,list) and prompt.get(str(source[0]),{}).get('class_type')=='LTXVAudioVAEEncode':
            audio_masks.append(key)
            mask=node.get('mask');solid=prompt.get(str(mask[0]),{}) if isinstance(mask,list) else {}
            if solid.get('class_type')!='SolidMask' or solid['inputs'].get('value')!=0:
                raise ValueError('口播音频必须保留SolidMask=0的零噪声遮罩。')
    if len(audio_masks)!=1:raise ValueError('未找到唯一的音频零噪声遮罩链路。')
    raw_create=prompt[str(prompt[video[0]]['inputs']['video'][0])]['inputs']
    preview_create=prompt[str(prompt[previews[0]]['inputs']['video'][0])]['inputs']
    crop=prompt[str(preview_create['images'][0])]['inputs']
    if (raw_create.get('audio')!=[loader,SLOT['condition_audio']]
        or preview_create.get('audio')!=[loader,SLOT['preview_audio']]
        or crop.get('batch_index')!=[loader,SLOT['crop_start']]
        or crop.get('length')!=[loader,SLOT['keep_frames']]
        or crop.get('image')!=raw_create.get('images')):
        raise ValueError('逐段预览须使用实际保留帧和对应的原音频，请恢复示例中的裁切接线。')
    saved={key:copy.deepcopy(prompt[key]) for key in sorted(ids)}
    saved[loader]={'class_type':'H3LVTalkingSegmentState','inputs':{}}
    return {'prompt':saved,'loader_id':loader,'video_id':video[0],
            'latent_id':latents[0],'preview_id':previews[0],
            'sampler_id':samplers[0],'audio_mask_id':audio_masks[0]}


def output_ids(config):
    canvas=config.get('canvas')
    if config.get('talking_profile')==overlap.PROFILE:
        return {'video':canvas['video_id'] if canvas else '273'}
    return ({'video':canvas['video_id'],'latent':canvas['latent_id'],'preview':canvas['preview_id']}
            if canvas else {'video':'273','latent':'9200'})


def prepare_reference(saved):
    """Bind a new project to its prepared audio, preserving the sampler graph."""
    prompt=saved['prompt'];sampler=prompt[saved['sampler_id']]['inputs']
    positive=sampler.get('positive')
    node=prompt.get(str(positive[0]),{}) if isinstance(positive,list) and len(positive)==2 else {}
    if node.get('class_type')!='MiniMaxH3ReferenceToVideo':raise ValueError('口播参考人声需要连接Ref2VA。')
    condition=node['inputs']
    expected=[saved['loader_id'],SLOT['reference_audio']]
    if condition.get('ref_audios.ref_audio_0')!=expected:verify_ref2va(saved)
    condition['ref_audios.ref_audio_0']=expected
    saved['prepared_audio']=True
    used=ancestors(prompt,saved['video_id'],saved['loader_id'])
    for key in list(prompt):
        if key not in used:prompt.pop(key)


def verify_ref2va(saved):
    prompt=saved['prompt'];sampler=prompt[saved['sampler_id']]['inputs']
    positive=sampler.get('positive')
    condition=prompt.get(str(positive[0]),{}) if isinstance(positive,list) else {}
    if condition.get('class_type')!='MiniMaxH3ReferenceToVideo':
        raise ValueError('新口播项目须使用Ref2VA展开示例09；旧项目请填原项目ID恢复。')
    audio=condition['inputs'].get('ref_audios.ref_audio_0')
    separation=prompt.get(str(audio[0]),{}) if isinstance(audio,list) else {}
    if saved.get('prepared_audio'):
        if audio!=[saved['loader_id'],SLOT['reference_audio']]:
            raise ValueError('Ref2VA须连接当前项目的参考人声音频。')
    elif (not audio or audio[1]!=3 or separation.get('class_type')!='AudioSeparation'
            or separation['inputs'].get('audio')!=[saved['loader_id'],SLOT['condition_audio']]):
        raise ValueError('Ref2VA参考音频须连接分段音频的AudioSeparation人声输出，请使用展开示例09。')
    encoded=prompt[str(prompt[saved['audio_mask_id']]['inputs']['samples'][0])]['inputs']
    if (encoded.get('audio')!=[saved['loader_id'],SLOT['condition_audio']]
            or not condition['inputs'].get('audio_vae')
            or condition['inputs']['audio_vae']!=encoded.get('audio_vae')):
        raise ValueError('Ref2VA参考音频与零罩音频latent须使用同一分段时钟和音频VAE。')
    if saved.get('profile')==overlap.PROFILE:
        verify_independent(saved,condition['inputs'])
        return
    verify_clock_paths(saved,condition['inputs'],encoded['audio_vae'])
    if prompt[saved['sampler_id']]['class_type']=='SamplerCustom':
        raw=prompt[str(prompt[saved['video_id']]['inputs']['video'][0])]['inputs']
        decoder=prompt[str(raw['images'][0])]['inputs']
        source=decoder.get('samples')
        restore=prompt.get(str(source[0]),{}) if isinstance(source,list) else {}
        expected={'has_previous':[saved['loader_id'],SLOT['has_previous']],
                  'previous':[saved['loader_id'],SLOT['previous_latent']],
                  'tail_token_start':[saved['loader_id'],SLOT['tail_token_start']]}
        if (restore.get('class_type')!='H3LVTalkingFrozenPrefix'
                or prompt[saved['latent_id']]['inputs'].get('samples')!=source
                or any(restore['inputs'].get(k)!=v for k,v in expected.items())):
            raise ValueError('标准K口播需在实际解码和保存前接入前缀校验恢复节点。')
        separated=restore['inputs'].get('samples')
        split=prompt.get(str(separated[0]),{}) if isinstance(separated,list) else {}
        if (not separated or separated[1]!=0 or split.get('class_type')!='LTXVSeparateAVLatent'
                or split['inputs'].get('av_latent')!=[saved['sampler_id'],0]):
            raise ValueError('前缀恢复必须连接当前标准K采样器的实际视频输出。')


def verify_independent(saved,condition):
    prompt=saved['prompt'];loader=saved['loader_id'];sampler=prompt[saved['sampler_id']]
    def connected(value,kind,output=0):
        node=prompt.get(str(value[0]),{}) if isinstance(value,list) and len(value)==2 else {}
        if node.get('class_type')!=kind or value[1]!=output:
            raise ValueError('独立窗口接线无效：'+kind)
        return node['inputs']
    if sampler['class_type']!='SamplerCustom':raise ValueError('独立窗口使用标准K单采样。')
    for field,slot in [('width','width'),('height','height'),('length','generation_frames'),
                       ('prompt','fixed_prompt'),('ref_images.ref_image_0','reference_image')]:
        if condition.get(field)!=[loader,SLOT[slot]]:
            raise ValueError('独立窗口Ref2VA须使用项目的原图、提示词、尺寸及帧数。')
    if any(k.startswith('ref_images.') and k not in ('ref_images.ref_image_0','ref_images.ref_image_1') or k.startswith('ref_videos.') for k in condition):
        raise ValueError('独立窗口只使用人物图和可选背景图，不使用视频参考。')
    if 'ref_images.ref_image_1' in condition and condition['ref_images.ref_image_1']!=[loader,SLOT['reference_2']]:
        raise ValueError('第二张背景图须连接口播项目的背景图输出。')
    sampler_seed(saved)
    mask=prompt[saved['audio_mask_id']]['inputs']
    if connected(mask.get('mask'),'SolidMask').get('value')!=0:
        raise ValueError('音频必须使用零噪声遮罩。')
    combined=connected(sampler['inputs'].get('latent_image'),'LTXVConcatAVLatent')
    native=connected(combined.get('video_latent'),'LTXVSeparateAVLatent')
    if (native.get('av_latent')!=[sampler['inputs']['positive'][0],1]
            or combined.get('audio_latent')!=[saved['audio_mask_id'],0]):
        raise ValueError('独立窗口须使用Ref2VA原生空视频latent和本窗零罩音频。')
    raw=connected(prompt[saved['video_id']]['inputs'].get('video'),'CreateVideo')
    if raw.get('audio')!=[loader,SLOT['condition_audio']] or raw.get('fps') not in (24,24.,[loader,SLOT['fps']]):
        raise ValueError('独立窗口原片须使用本窗原音频和24fps。')
    decoder=connected(raw.get('images'),'VAEDecode')
    split=connected(decoder.get('samples'),'LTXVSeparateAVLatent')
    if split.get('av_latent')!=[saved['sampler_id'],0] or decoder.get('vae')!=condition.get('vae') or condition.get('vae')==condition.get('audio_vae'):
        raise ValueError('独立窗口需直接解码当前K采样结果，并使用正确的视频VAE。')


def sampler_seed(saved,legacy_seed=None):
    value=saved['prompt'][saved['sampler_id']]['inputs'].get('noise_seed')
    if value==[saved['loader_id'],SLOT['seed']]:
        return legacy_seed  # Existing frozen projects retain their original binding.
    if type(value) is not int or not 0<=value<=2**64-1:
        raise ValueError('请在标准K采样器中设置固定整数种子。')
    return value


def equivalent_seed_canvas(current,previous,seed):
    """A literal seed and the old project-seed wire have identical semantics."""
    def normalized(value):
        value=copy.deepcopy(value)
        value['prompt'][value['sampler_id']]['inputs']['noise_seed']=sampler_seed(value,seed)
        for node in value['prompt'].values():node.pop('_meta',None)
        return value
    return normalized(current)==normalized(previous)


def verify_clock_paths(saved,condition,audio_vae):
    prompt=saved['prompt'];loader=saved['loader_id']
    sampler=prompt[saved['sampler_id']]
    def connected(value,kind,output=0):
        node=prompt.get(str(value[0]),{}) if isinstance(value,list) and len(value)==2 else {}
        if node.get('class_type')!=kind or value[1]!=output:
            raise ValueError('Ref2VA时钟链路连接无效：'+kind)
        return node['inputs']
    for field,slot in [('width','width'),('height','height'),('length','generation_frames')]:
        if condition.get(field)!=[loader,SLOT[slot]]:
            raise ValueError('Ref2VA尺寸与生成帧数须使用已审核项目的分段时钟。')
    raw=connected(prompt[saved['video_id']]['inputs']['video'],'CreateVideo')
    decoder=connected(raw['images'],'VAEDecode')
    preview=connected(prompt[saved['preview_id']]['inputs']['video'],'CreateVideo')
    if any(value.get('fps') not in (24,24.,[loader,SLOT['fps']]) for value in (raw,preview)):
        raise ValueError('Ref2VA原片和裁切预览须使用项目的24fps时钟。')
    if (condition.get('vae')==audio_vae or decoder.get('vae')==audio_vae
            or sampler['class_type']=='SelfLiftAvatarH3Sampler' and sampler['inputs'].get('vae')==audio_vae):
        raise ValueError('视频参考、采样和解码不能接到音频VAE。')
    combined=connected(sampler['inputs'].get('latent_image'),'LTXVConcatAVLatent')
    if combined.get('audio_latent')!=[saved['audio_mask_id'],0]:
        raise ValueError('采样器必须使用实际的零噪声罩原声latent。')
    switch=connected(combined.get('video_latent'),'LazySwitchKJ')
    if switch.get('switch')!=[loader,SLOT['has_previous']]:
        raise ValueError('续接分支必须使用项目的上一段状态。')
    first=connected(switch.get('on_false'),'H3LVTalkingVideoMask')
    empty=connected(first.get('samples'),'LTXVSeparateAVLatent')
    reference=sampler['inputs']['positive']
    if empty.get('av_latent')!=[reference[0],1]:
        raise ValueError('首段视频须使用当前Ref2VA的原生空白latent。')
    continuation=connected(switch.get('on_true'),'LTXVAudioVideoMask')
    times=[continuation.get(k) for k in ('video_fps','video_start_time','video_end_time')]
    # KJ maps this mask through its own 8-frame grid, not H3 pixel seconds.
    if (any(not isinstance(t,(int,float)) or not math.isfinite(t) for t in times)
            or round(times[0]*times[1])!=0 or (round(times[0]*times[2])+7)//8!=project.PREFIX_TOKENS
            or continuation.get('max_length')!='partial' or continuation.get('existing_mask_mode')!='subtract'):
        raise ValueError('续接时间遮罩须只冻结前12个latent token，保留后续生成区。')
    masked=connected(continuation.get('video_latent'),'H3LVTalkingVideoMask')
    if first.get('masks')!=[loader,SLOT['person_mask']] or masked.get('masks')!=first.get('masks'):
        raise ValueError('视频生成范围须使用项目已有遮罩，不能替换音频零罩或时间前缀。')
    replace=connected(masked.get('samples'),'ReplaceVideoLatentFrames')
    cut=connected(replace.get('source'),'LatentCut')
    if (replace.get('destination')!=first.get('samples') or replace.get('index')!=0
            or cut.get('samples')!=[loader,SLOT['previous_latent']] or cut.get('dim')!='t'
            or cut.get('index')!=[loader,SLOT['tail_token_start']] or cut.get('amount')!=project.PREFIX_TOKENS):
        raise ValueError('续接前缀须取上一段实际保留尾部的12token并放在起点。')


def build(plan,row,uploaded):
    canvas=plan['config']['canvas'];value=copy.deepcopy(canvas['prompt'])
    value[canvas['loader_id']]={'class_type':'H3LVTalkingSegmentState','inputs':{
        'project_id':plan['id'],'segment_index':row['index'],'attempt':uploaded['attempt']}}
    for kind,key in output_ids(plan['config']).items():
        value[key]['inputs']['filename_prefix']=f'{TALKING_PREFIX}/projects/{plan["id"]}/takes/{row["index"]:04d}/{uploaded["attempt"]}/generated/{kind}'
    for node in value.values():
        if node['class_type']=='MiniMaxH3ReferenceToVideo':
            if overlap.enabled(plan) and ('fixed_prompt' in plan['config'] or row.get('prompt_draft') or row.get('generation_prompt')):
                from .prompts import effective
                node['inputs']['prompt']=effective(plan,row)
            count=len(project.reference_assets(plan))
            if plan['config'].get('talking_profile')=='ref2va-v1':count=max(2,count)
            for i in range(count,6):node['inputs'].pop(f'ref_images.ref_image_{i}',None)
    return value


class SegmentState:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'project_id':('STRING',),'segment_index':('INT',{'default':0,'min':0}),
                            'attempt':('STRING',)}}
    RETURN_TYPES=RETURN_TYPES
    RETURN_NAMES=RETURN_NAMES
    FUNCTION='load'
    CATEGORY='MiniMax H3/Internal'
    DEPRECATED=True

    @classmethod
    def IS_CHANGED(cls,**kwargs):return float('nan')

    def load(self,project_id,segment_index,attempt):
        import torch
        import nodes
        import folder_paths
        from . import root
        plan=read_plan(root(),project_id);directory=project_path(root(),project_id)
        if not plan.get('approved') or plan.get('approved_fingerprint')!=project.fingerprint(plan):
            raise ValueError('请先保存并确认当前口播分段。')
        project.verify_assets(root(),plan)
        if not 0<=segment_index<len(plan['segments']):raise ValueError('段号超出范围。')
        if len(attempt)!=12 or any(c not in '0123456789abcdef' for c in attempt):raise ValueError('片段执行标识无效。')
        row=plan['segments'][segment_index]
        wave,sr=sf.read(audio_file(directory,'source.wav'),dtype='float32',always_2d=True)
        condition=project.condition_audio(wave,row)
        reference=condition
        if plan.get('audio_preparation'):
            vocals,_=sf.read(audio_file(directory,'vocals.wav'),dtype='float32',always_2d=True)
            reference=project.condition_audio(vocals,row)
        def audio(samples):return {'waveform':torch.from_numpy(samples.T.copy())[None], 'sample_rate':sr}
        def picture(name):
            with Image.open(directory/'refs'/name) as image:
                return torch.from_numpy(np.asarray(image.convert('RGB'),dtype='float32')/255)[None]
        references=[picture(item['name']) for item in project.reference_assets(plan)]
        width,height=project.output_size(plan['config'])
        if project.unmasked(plan):
            mask=torch.ones(1,height,width)
        else:
            with Image.open(directory/'refs/person_mask.png') as image:
                mask=torch.from_numpy(np.asarray(image.convert('L'),dtype='float32')/255)[None]
        previous=None;tail=0
        if segment_index and not overlap.enabled(plan):
            prior=plan['segments'][segment_index-1];job=prior.get('job',{})
            if job.get('status')!='completed':raise ValueError('上一段尚未完成，不能跳过动作续接。')
            path=inside(directory,job['latent'])
            if project.digest(path)!=job['latent_sha256']:raise ValueError('上一段续接latent已变化，已停止。')
            output=Path(folder_paths.get_output_directory()).resolve()
            annotated=path.resolve().relative_to(output).as_posix()+' [output]'
            previous=nodes.LoadLatent().load(annotated)[0];tail=prior['tail_token_start']
        template=json.loads((Path(__file__).resolve().parents[1]/'defaults/talking_rh_graph.json').read_text(encoding='utf-8'))
        prefix=f'{TALKING_PREFIX}/projects/{plan["id"]}/takes/{segment_index:04d}/{attempt}/generated'
        values={'condition_audio':audio(condition),'preview_audio':audio(wave[row['start_sample']:row['end_sample']]),
                'reference_image':references[0],'person_mask':mask,'generation_frames':row['generation_frames'],
                'width':width,'height':height,'seed':plan['config']['seed'],
                'use_endpoint':plan['config'].get('talking_profile') not in ('ref2va-v1',overlap.PROFILE) and segment_index==0 and len(references)==1,'has_previous':segment_index>0 and not overlap.enabled(plan),
                'previous_latent':previous,'tail_token_start':tail,'fixed_prompt':plan['config'].get('fixed_prompt',template['268']['inputs']['prompt']),
                'fps':24.0,'raw_prefix':prefix+'/video','latent_prefix':prefix+'/latent',
                'preview_prefix':prefix+'/preview','crop_start':row['edit_start_frame'],'keep_frames':row['edit_frames'],
                'lowres_scale':1. if segment_index else .5,'reference_audio':audio(reference)}
        if overlap.enabled(plan) and ('fixed_prompt' in plan['config'] or row.get('prompt_draft') or row.get('generation_prompt')):
            from .prompts import effective
            values['fixed_prompt']=effective(plan,row)
        for i in range(2,7):values[f'reference_{i}']=references[i-1] if i<=len(references) else None
        if len(references)==1 and plan['config'].get('talking_profile')=='ref2va-v1':values['reference_2']=references[0]
        return (plan['id'],plan.get('final_video',''),len(plan['segments']),*(values[name] for name,_ in FIELDS))


def assemble_project(root,pid):
    from .runner import claim
    directory=project_path(root,pid)
    handle=claim(directory)
    try:
        return assemble_ready_project(root,pid)
    finally:handle.close()


def assemble_ready_project(root,pid,*,assemble=None):
    """Assemble validated takes while the caller owns the project's run claim."""
    from ..controller import assemble as legacy_assemble
    directory=project_path(root,pid);plan=read_plan(root,pid)
    if not plan.get('approved') or plan.get('approved_fingerprint')!=project.fingerprint(plan):
        raise ValueError('请先保存并确认当前口播分段。')
    project.verify_clock(plan)
    if overlap.enabled(plan):project.verify_assets(root,plan)
    if any(r.get('job',{}).get('status')!='completed' for r in plan['segments']):
        raise ValueError('仍有片段未完成，暂不能合并。')
    for row in plan['segments']:
        job=row['job']
        if project.digest(inside(directory,job['video']))!=job['video_sha256']:
            raise ValueError('已完成原片已变化，已停止合并。')
    final=plan.get('final_video','')
    if final and not plan.get('final_stale') and Path(final).is_file():
        project.verify_final(root,pid,final,plan.get('final_sha256'))
    else:
        merge=assemble or (overlap.assemble if overlap.enabled(plan) else legacy_assemble)
        final=merge(root,pid)
        plan=read_plan(root,pid)
    plan.update(final_video=final,final_sha256=project.digest(final),final_stale=False,run_status='completed')
    project.finish_regeneration(plan)
    write_plan(root,plan)
    return final


def segment_preview(root,pid,index,raw=False):
    from ..controller import command
    plan=read_plan(root,pid);directory=project_path(root,pid)
    if overlap.enabled(plan):return overlap.preview(root,pid,index,raw=raw)
    project.verify_clock(plan)
    if not 0<=index<len(plan['segments']):raise ValueError('段号超出范围。')
    row=plan['segments'][index];job=row.get('job',{})
    if job.get('status')!='completed':raise ValueError('本段尚未生成完成。')
    if job.get('preview'):
        path=inside(directory,job['preview'])
        if project.digest(path)!=job['preview_sha256']:raise ValueError('本段预览文件已变化。')
        return path
    raw=inside(directory,job['video'])
    if project.digest(raw)!=job['video_sha256']:raise ValueError('本段原片已变化。')
    identity=json.dumps([job['video_sha256'],row['edit_start_frame'],row['edit_frames'],
                         row['start_sample'],row['end_sample'],plan['source_sha256']]).encode()
    target=directory/'cache'/f'preview_{index:04d}_{hashlib.sha256(identity).hexdigest()[:16]}.mp4'
    if not target.is_file():
        temporary=target.with_suffix('.'+uuid.uuid4().hex[:8]+'.tmp.mp4')
        start=row['edit_start_frame'];end=start+row['edit_frames']
        command(['ffmpeg','-v','error','-y','-i',str(raw),'-i',str(audio_file(directory,'source.wav')),
                 '-filter_complex',f'[0:v]trim=start_frame={start}:end_frame={end},setpts=N/(24*TB)[v];'
                 f'[1:a]atrim=start_sample={row["start_sample"]}:end_sample={row["end_sample"]},asetpts=PTS-STARTPTS[a]',
                 '-map','[v]','-map','[a]','-r','24','-frames:v',str(row['edit_frames']),
                 '-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','320k',
                 '-movflags','+faststart',str(temporary)])
        temporary.replace(target)
    return target


class FrozenPrefix:
    """Validate native K's float round trip before restoring immutable context."""
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'samples':('LATENT',),'has_previous':('BOOLEAN',),
                            'tail_token_start':('INT',{'default':0,'min':0})},
                'optional':{'previous':('LATENT',)}}
    RETURN_TYPES=('LATENT',)
    FUNCTION='restore'
    CATEGORY='MiniMax H3/Internal'
    DESCRIPTION='仅容许浮点格式往返误差，验证后恢复原续接前缀；明显变化会停止。新生成区域不修改。'

    def restore(self,samples,has_previous,tail_token_start,previous=None):
        import torch
        from .prefix import RTOL,ATOL
        if not has_previous:return (samples,)
        current=samples['samples'];source=previous.get('samples') if previous else None
        count=project.PREFIX_TOKENS
        if (source is None or current.ndim!=5 or source.ndim!=5
                or current.shape[:2]+current.shape[-2:]!=source.shape[:2]+source.shape[-2:]
                or current.shape[2]<count or tail_token_start<0 or source.shape[2]<tail_token_start+count):
            raise ValueError('标准K续接前缀的来源或形状无效。')
        expected=source[:,:,tail_token_start:tail_token_start+count].to(current)
        actual=current[:,:,:count]
        if not torch.isfinite(expected).all() or not torch.allclose(actual,expected,rtol=RTOL,atol=ATOL):
            raise ValueError('标准K采样改变了冻结前缀，超过浮点往返误差；已停止，未用复制隐藏变化。')
        restored=current.clone();restored[:,:,:count]=expected
        return ({**samples,'samples':restored},)


class TalkingMerge:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'project_id':('STRING',)}}
    RETURN_TYPES=('VIDEO','STRING')
    RETURN_NAMES=('video','final_path')
    FUNCTION='merge'
    CATEGORY='MiniMax H3'
    OUTPUT_NODE=True

    @classmethod
    def IS_CHANGED(cls,**kwargs):return float('nan')

    def merge(self,project_id):
        from comfy_execution.graph import ExecutionBlocker
        from comfy_api.latest import InputImpl
        from . import root
        plan=read_plan(root(),project_id)
        if not plan.get('approved') or any(r.get('job',{}).get('status')!='completed' for r in plan['segments']):
            return {'ui':{'text':['等待各段生成完成；完成后运行此合并分支。']},
                    'result':(ExecutionBlocker(None),ExecutionBlocker(None))}
        final=assemble_project(root(),project_id)
        return {'ui':{'text':[final]},'result':(InputImpl.VideoFromFile(final),final)}
