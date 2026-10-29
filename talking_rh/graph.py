import json
from pathlib import Path

from .project import PREFIX_TOKENS, unmasked, output_size
from . import overlap
from ..storage import TALKING_PREFIX

TEMPLATE = Path(__file__).resolve().parents[1]/'defaults/talking_rh_graph.json'


def build(plan,row,uploaded,previous=None):
    if plan['config'].get('execution_mode')=='canvas':
        from .canvas import build as build_canvas
        return build_canvas(plan,row,uploaded)
    graph = json.loads(TEMPLATE.read_text(encoding='utf-8'))
    independent=overlap.enabled(plan)
    ref2va=plan['config'].get('talking_profile')=='ref2va-v1' or independent
    if plan['config'].get('backend')=='local':
        graph['9104']['class_type']='H3LVTalkingVideoMask'
        models=plan['config']['models']
        graph.pop('9300',None)
        graph['242']['inputs']['model']=['127',0]
        for key,field,name in [('127','unet_name','local_model'),('128','clip_name','local_clip'),
                               ('119','vae_name','local_video_vae'),('120','vae_name','local_audio_vae'),
                               ('270','upscaler_model','local_upscaler')]:
            graph[key]['inputs'][field]=models[name]
        if models.get('local_clip_read')=='内存':
            graph['128']={'class_type':'H3LVTalkingClipLoader','inputs':{'clip_name':models['local_clip']}}
        if models.get('local_lora','不使用')!='不使用':
            graph['9300']={'class_type':'LoraLoaderModelOnly','inputs':{'model':['127',0],
                           'lora_name':models['local_lora'],'strength_model':1.0}}
            graph['242']['inputs']['model']=['9300',0]
    if 'width' in plan['config']:
        size=output_size(plan['config'])
        for node in graph.values():
            for name,value in node['inputs'].items():
                if isinstance(value,list) and len(value)==2 and value[0]=='115':
                    node['inputs'][name]=size[value[1]]
        graph.pop('115')
    else:graph['115']['inputs']['megapixels'] = plan['config']['megapixels']
    graph['270']['inputs']['seed'] = plan['config']['seed']
    graph['9000']['inputs']['audio'] = uploaded['audio']
    graph['9001']['inputs']['image'] = uploaded['references'][0]
    if unmasked(plan):
        graph.pop('9102')
        # KJ subtract otherwise defaults to all zero and freezes every frame.
        # This is a uniform generation mask, with no person/background contour.
        graph['9103']={'class_type':'SolidMask','inputs':{'value':1.,'width':64,'height':64}}
    else:
        graph['9102']['inputs']['image'] = uploaded['mask']
    graph['268']['inputs']['length'] = row['generation_frames']
    graph['9100']['inputs']['amount'] = row['generation_frames']
    if ref2va or row['index'] or len(uploaded['references'])>1:
        old = graph['268']['inputs']
        graph['268'] = {'class_type':'MiniMaxH3ReferenceToVideo','inputs':{
            **{k:old[k] for k in ('clip','vae','prompt','width','height','length')},
            'ref_image_size':'match','ref_images.ref_image_0':['267',0]}}
        for i,filename in enumerate(uploaded['references'][1:],1):
            key = str(9300+i)
            graph[key] = {'class_type':'LoadImage','inputs':{'image':filename}}
            graph['268']['inputs'][f'ref_images.ref_image_{i}'] = [key,0]
    if ref2va:
        # Reference blocks condition the model; the generated video starts from
        # the native empty latent, rather than repeated encoded portrait frames.
        condition=graph['268']['inputs']
        condition['ref_images.ref_image_0']=['9001',0]
        if len(uploaded['references'])==1 and not independent:condition['ref_images.ref_image_1']=['9001',0]
        graph['9250']={'class_type':'AudioSeparation','inputs':{'audio':['9000',0],
            'chunk_fade_shape':'linear','chunk_length':10.,'chunk_overlap':.1}}
        condition.update(audio_vae=['120',0],**{'ref_audios.ref_audio_0':['9250',3]})
        graph['9101']={'class_type':'LTXVSeparateAVLatent','inputs':{'av_latent':['268',1]}}
        graph.pop('269')
        graph['270']['inputs'].update(positive=['268',0],vae=['119',0],transition_step=4)
        graph['264']['inputs']['conditioning']=['268',0]
        graph['270']['inputs']['sigmas']=['263',0]
    if independent:
        graph['246']['inputs']['video_latent']=['9101',0]
        from .prompts import effective
        graph['268']['inputs']['prompt']=effective(plan,row)
    if row['index'] and not independent:
        if not previous or not uploaded.get('previous_latent'):
            raise ValueError('缺少上一段实际保留尾部的续接状态。')
        graph.update({
            '9110':{'class_type':'LoadLatent','inputs':{'latent':uploaded['previous_latent']}},
            '9111':{'class_type':'LatentCut','inputs':{'samples':['9110',0],'dim':'t',
                'index':previous['tail_token_start'],'amount':PREFIX_TOKENS}},
            '9112':{'class_type':'ReplaceVideoLatentFrames','inputs':{'destination':['9101',0],
                'source':['9111',0],'index':0}},
            '9113':{'class_type':'LTXVAudioVideoMask','inputs':{'video_latent':['9104',0],
                'video_fps':8.0,'video_start_time':0.0,'video_end_time':89/8,
                'audio_start_time':0.0,'audio_end_time':0.0,'max_length':'partial','existing_mask_mode':'subtract'}}})
        graph['9104']['inputs']['samples'] = ['9112',0]
        graph['246']['inputs']['video_latent'] = ['9113',0]
    for node in graph.values():
        if node['class_type'] in ('SaveVideo','SaveLatent'):
            label = 'raw' if node['class_type']=='SaveVideo' else 'video'
            node['inputs']['filename_prefix'] = f'{TALKING_PREFIX}/projects/{plan["id"]}/takes/{row["index"]:04d}/{uploaded["attempt"]}/generated/{label}'
    if plan['config'].get('rh_sampling'):
        from .sampling import apply
        graph = apply(graph,plan['config']['rh_sampling'],ref2va=ref2va)
    if independent:
        if graph['270']['class_type']!='SamplerCustom':raise ValueError('独立窗口口播须使用已配置的标准K采样模板。')
        from .sampling import ancestors
        used=ancestors(graph,['273'])
        graph={k:n for k,n in graph.items() if k in used}
    if ref2va:
        if graph['270']['class_type']=='SelfLiftAvatarH3Sampler':
            graph['270']['inputs']['lowres_scale']=1. if row['index'] else .5
            if graph['270']['inputs'].get('highres_tiling_mode','')=='':
                graph['270']['inputs']['highres_tiling_mode']='spatial'
        from .sampling import ancestors
        used=ancestors(graph,['273'] if independent else ['273','9200'])
        graph={k:n for k,n in graph.items() if k in used}
    assert graph['249']['inputs']['value']==0
    assert graph['246']['inputs']['audio_latent']==['250',0]
    assert graph['270']['class_type'] in ('SelfLiftAvatarH3Sampler','SamplerCustom')
    return graph
