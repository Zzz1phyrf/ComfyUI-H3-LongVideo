import json
from pathlib import Path

from .project import PREFIX_TOKENS

TEMPLATE = Path(__file__).resolve().parents[1]/'defaults/talking_rh_graph.json'


def build(plan,row,uploaded,previous=None):
    graph = json.loads(TEMPLATE.read_text(encoding='utf-8'))
    if plan['config'].get('backend')=='local':
        graph['9104']['class_type']='H3LVTalkingVideoMask'
        models=plan['config']['models']
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
    graph['115']['inputs']['megapixels'] = plan['config']['megapixels']
    graph['270']['inputs']['seed'] = plan['config']['seed']
    graph['9000']['inputs']['audio'] = uploaded['audio']
    graph['9001']['inputs']['image'] = uploaded['references'][0]
    graph['9102']['inputs']['image'] = uploaded['mask']
    graph['268']['inputs']['length'] = row['generation_frames']
    graph['9100']['inputs']['amount'] = row['generation_frames']
    if row['index'] or len(uploaded['references'])>1:
        old = graph['268']['inputs']
        graph['268'] = {'class_type':'MiniMaxH3ReferenceToVideo','inputs':{
            **{k:old[k] for k in ('clip','vae','prompt','width','height','length')},
            'ref_image_size':'match','ref_images.ref_image_0':['267',0]}}
        for i,filename in enumerate(uploaded['references'][1:],1):
            key = str(9300+i)
            graph[key] = {'class_type':'LoadImage','inputs':{'image':filename}}
            graph['268']['inputs'][f'ref_images.ref_image_{i}'] = [key,0]
    if row['index']:
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
            node['inputs']['filename_prefix'] = f'H3TalkingRH/{plan["id"]}/{row["index"]:04d}/{uploaded["attempt"]}/{label}'
    assert graph['249']['inputs']['value']==0
    assert graph['246']['inputs']['audio_latent']==['250',0]
    assert graph['270']['class_type']=='SelfLiftAvatarH3Sampler'
    return graph
