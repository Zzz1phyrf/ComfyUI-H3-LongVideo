"""Talking-head node with local inference and optional RunningHub execution."""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from ..core import read_plan
from ..storage import TALKING_PREFIX, output_path
from . import project
from .auto_mask import automatic_mask
from .graph import TEMPLATE
from .remote import Client, validate_config
from .local import MODEL_FIELDS, NO_LORA, model_choices, validate_models
from . import canvas, overlap


def root():
    return output_path(TALKING_PREFIX)/'projects'


class TalkingRH:
    DEPRECATED = True
    DESCRIPTION = "口播数字人：独立Ref2VA窗口复用原图，原声零罩，按音频时钟动作选切。先准备审核，再逐窗生成及合并；可预览原片与采用区间。旧项目保留latent续接。"
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{
            'audio':('AUDIO',), 'reference_image':('IMAGE',),
            'workflow_id':('STRING',{'default':'','tooltip':'仅RunningHub模式使用。本地生成留空。'}),
            'region':(['海外版','国内版'],), 'instance_type':(['ultra','plus','default'],),
            'megapixels':([1.0,.8,.6],), 'seed':('INT',{'default':999,'min':0,'max':2**63-1,'control_after_generate':False}),
            'first_seconds':('FLOAT',{'default':13.0,'min':2.3334,'max':13.66,'step':.1}),
            'segment_seconds':('FLOAT',{'default':5.667,'min':2.2,'max':12.0,'step':.1}),
            'project_id':('STRING',{'default':''})},
            'optional':{'person_mask':('MASK',{'tooltip':'仅兼容旧工作流。新项目默认不使用人物空间遮罩；旧项目恢复沿用其原配置。'}),
                        **{f'reference_{i}':('IMAGE',) for i in range(2,7)},
                        'backend':(['本地','RunningHub'],),
                        **{field:(model_choices(field),) for field in [*MODEL_FIELDS,'local_lora']},
                        'local_clip_read':(['原生','内存'],{'tooltip':'仅本地使用。内存读取避开Windows大CLIP文件映射读取崩溃，可能占用更多系统内存；不改变权重。'}),
                        'execution_mode':(['内置','画布'],{'default':'画布','tooltip':'本地使用外部模型、Ref2VA、采样和解码节点；内置仅用于旧项目兼容及RH控制端。'}),
                        'continuation_mode':(['独立窗口（自然动作）','旧版latent续接'],{'tooltip':'独立分段优先按口播停顿切分，每段4–15秒；保留手动切点。first_seconds和segment_seconds仅用于旧版。'}),
                        'fixed_prompt':('STRING',{'default':overlap.DEFAULT_PROMPT,'multiline':True})},
            'hidden':{'prompt':'PROMPT','unique_id':'UNIQUE_ID','extra_pnginfo':'EXTRA_PNGINFO'}}

    RETURN_TYPES = canvas.RETURN_TYPES
    RETURN_NAMES = canvas.RETURN_NAMES
    FUNCTION = 'prepare'
    CATEGORY = 'MiniMax H3'
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls,**kwargs):
        return float('nan')

    def prepare(self,audio,reference_image,person_mask=None,workflow_id='',region='海外版',instance_type='ultra',
                megapixels=1.0,seed=999,first_seconds=13.0,segment_seconds=5.667,project_id='',
                backend='RunningHub',local_model='',local_clip='',local_video_vae='',local_audio_vae='',
                local_upscaler='',local_lora=NO_LORA,local_clip_read='原生',execution_mode='内置',
                continuation_mode='旧版latent续接',fixed_prompt=overlap.DEFAULT_PROMPT,
                prompt=None,unique_id=None,extra_pnginfo=None,width=None,height=None,seed_from_sampler=False,audio_processing=None,**references):
        from ..nodes import audio_array
        wave,sr = audio_array(audio)
        pictures=[]
        tensors=[reference_image]+[references.get(f'reference_{i}') for i in range(2,7)]
        signature = hashlib.sha256(wave.tobytes()+str(sr).encode())
        for tensor in tensors:
            if tensor is None: continue
            value=tensor.detach().cpu().numpy()
            if value.ndim!=4 or len(value)!=1 or value.shape[-1] not in (3,4) or not np.isfinite(value).all():
                raise ValueError('每个参考图输入须为一张有效图片。')
            signature.update(str(value.shape).encode()+value.tobytes())
            pictures.append(Image.fromarray(np.rint(np.clip(value[0,:,:,:3],0,1)*255).astype('uint8')))
        prior=read_plan(root(),project_id.strip()) if str(project_id).strip() else None
        prepared=audio_processing is not None and (prior is None or prior['config'].get('audio_preparation_version')==1)
        if audio_processing is not None:
            from ..audio_preparation import validate_mode
            validate_mode(audio_processing)
        if continuation_mode not in ('独立窗口（自然动作）','旧版latent续接'):raise ValueError('口播续接方式无效。')
        independent=continuation_mode=='独立窗口（自然动作）'
        if prior and overlap.enabled(prior)!=independent:
            raise ValueError('续接方式与旧项目不同，请新建项目；旧项目和结果保留。')
        if independent and (not 1<=len(pictures)<=2 or person_mask is not None):
            raise ValueError('独立窗口使用人物图和可选背景图，最多两张，不使用人物MASK。')
        if independent and not str(fixed_prompt).strip():raise ValueError('独立窗口提示词不能为空。')
        if independent and backend=='本地' and execution_mode!='画布':
            raise ValueError('独立窗口的本地执行请导入13号展开工作流，使用画布模式。')
        auto_info=None
        if person_mask is None:
            if prior and prior['config'].get('auto_mask'):
                # Resume the frozen legacy contract; never reinterpret an old
                # automatic-mask project as a new unmasked trajectory.
                value,auto_info=automatic_mask(pictures[0],root().parent/'mask_cache')
                mask=value[None]
            elif prior and not project.unmasked(prior):
                raise ValueError('旧项目需要原人物MASK接线。请恢复原接线，或新建不使用人物空间遮罩的项目。')
            else:
                mask=None
        else:
            mask=person_mask.detach().cpu().numpy()
        if mask is not None:
            if mask.ndim!=3 or len(mask)!=1:
                raise ValueError('请提供一张人物活动MASK。')
            signature.update(str(mask.shape).encode()+mask.tobytes())
        resolution={'megapixels':float(megapixels)}
        if width is not None or height is not None:
            size=project.output_size({'width':width,'height':height})
            resolution={'width':size[0],'height':size[1]}
            if prior and 'megapixels' in prior['config'] and 'width' not in prior['config']:
                if size!=project.output_size(prior['config']):
                    raise ValueError('外部宽高与旧项目不同，请新建项目；旧结果保留。')
                resolution={'megapixels':prior['config']['megapixels']}
        config={'base_url':'https://www.runninghub.ai' if region=='海外版' else 'https://www.runninghub.cn',
                'workflow_id':str(workflow_id).strip(),'instance_type':instance_type,
                **resolution,'seed':int(seed),'first_seconds':float(first_seconds),
                'segment_seconds':float(segment_seconds),'template_sha256':project.digest(TEMPLATE)}
        if execution_mode not in ('内置','画布'):raise ValueError('口播执行方式无效。')
        if execution_mode=='画布' and backend!='本地':raise ValueError('展开画布用于本地执行；RH请使用云端工作流入口。')
        if backend=='本地':
            config={k:config[k] for k in (*resolution,'seed','first_seconds','segment_seconds','template_sha256')}
            config['backend']='local'
            if execution_mode=='画布':
                config.update(execution_mode='canvas',canvas=canvas.capture(prompt,unique_id,independent=independent,prepared_audio=prepared))
                if seed_from_sampler:
                    config['seed']=canvas.sampler_seed(config['canvas'],int(seed))
                    previous=prior.get('config',{}).get('canvas') if prior else None
                    if (previous and config['seed']==prior['config']['seed']
                            and canvas.equivalent_seed_canvas(config['canvas'],previous,config['seed'])):
                        config['canvas']=previous
                if independent and len(pictures)==2:
                    refs=[n['inputs'] for n in config['canvas']['prompt'].values() if n['class_type']=='MiniMaxH3ReferenceToVideo']
                    if not any(n.get('ref_images.ref_image_1')==[str(unique_id),canvas.SLOT['reference_2']] for n in refs):
                        raise ValueError('已输入背景图，请将口播节点的背景图输出连接到Ref2VA第二张参考图。')
            else:
                config['models']={'local_model':local_model,'local_clip':local_clip,
                                 'local_video_vae':local_video_vae,'local_audio_vae':local_audio_vae,
                                 'local_upscaler':local_upscaler,'local_lora':local_lora}
                if local_clip_read not in ('原生','内存'):raise ValueError('本地CLIP读取方式无效。')
                if local_clip_read=='内存':config['models']['local_clip_read']='内存'
                validate_models(config)
        elif backend=='RunningHub':
            # Keep old RH project fingerprints readable. Missing backend in an
            # older API submission retains RH; newly created UI nodes default local.
            validate_config(config)
            if str(project_id).strip():
                if prior.get('config',{}).get('rh_sampling'):
                    config['rh_sampling']=prior['config']['rh_sampling']
            else:
                from .sampling import freeze
                config['rh_sampling']=freeze(Client(config).workflow(),prepared_audio=prepared)
            if seed_from_sampler:
                if prior:config['seed']=prior['config']['seed']
                else:
                    from .sampling import binding
                    sampler,_=binding(config['rh_sampling']['graph'])
                    settings=config['rh_sampling']['graph'][sampler]['inputs']
                    value=settings.get('noise_seed',settings.get('seed'))
                    if type(value) is not int or not 0<=value<=2**64-1:raise ValueError('RH采样器须设置固定整数种子。')
                    config['seed']=value
        else:
            raise ValueError('请选择本地或RunningHub生成后端。')
        # New speech projects use the accepted Ref2VA contract. A legacy resume
        # keeps its original fingerprint and generation semantics.
        if independent or prior is None or prior['config'].get('talking_profile')=='ref2va-v1':
            config['talking_profile']=overlap.PROFILE if independent else 'ref2va-v1'
            if independent:config['fixed_prompt']=str(fixed_prompt).strip()
            if config.get('canvas'):canvas.verify_ref2va(config['canvas'])
            if config.get('rh_sampling'):
                from .sampling import binding
                source=config['rh_sampling']['graph'];sampler,reference=binding(source)
                if independent and source[sampler]['class_type']!='SamplerCustom':
                    raise ValueError('独立窗口RH模板需要标准K单采样，请使用15号云端模板。')
                if not config['rh_sampling'].get('prepared_audio') and 'ref_audios.ref_audio_0' not in source[reference]['inputs']:
                    raise ValueError('口播RH工作流须使用Ref2VA并连接参考人声。')
        if prepared:
            config.update(audio_preparation_version=1,audio_processing=audio_processing)
        if mask is None:config['person_mask_mode']='none'
        if auto_info is not None:config['auto_mask']=auto_info
        if not 2.333<=first_seconds<=13.667 or not 2.2<=segment_seconds<=12:
            raise ValueError('分段目标时长超出支持范围。')
        signature.update(json.dumps(config,sort_keys=True).encode())
        input_id=signature.hexdigest()
        if str(project_id).strip():
            plan=read_plan(root(),project_id.strip())
            if plan.get('kind')!='talking_rh' or plan.get('input_fingerprint')!=input_id:
                raise ValueError('输入素材或配置已变化。请点击“新建项目”以保留旧项目和输出。')
        else:
            plan=project.prepare(root(),wave,sr,pictures,None if mask is None else mask[0],config,input_id)
        result=(canvas.blocked(plan) if execution_mode=='画布' else
                (plan['id'],plan.get('final_video',''),len(plan['segments']),*(None for _ in canvas.FIELDS)))
        return {'ui':{'talking_rh_project':[plan['id']],'talking_rh_open_review':[not plan.get('approved')],
                      'text':['口播项目已准备，请打开分段审核，试听后保存并确认。']},
                'result':result}


class TalkingAvatar(TalkingRH):
    """Public independent-window interface; legacy slots stay internal to jobs."""
    DEPRECATED = False
    DESCRIPTION = '口播：一张图片确定人物、背景与构图。先准备审核，再分段生成；种子在K采样器固定，本地执行外部画布，RH执行已保存的云端模板。'
    OUTPUT_FIELDS = ('project_id','condition_audio','reference_image',
                     'generation_frames','width','height','fixed_prompt','fps','raw_prefix','reference_audio')
    LEGACY_SLOTS = (0, *(canvas.SLOT[name] for name in OUTPUT_FIELDS[1:]))
    RETURN_TYPES = tuple(canvas.RETURN_TYPES[index] for index in LEGACY_SLOTS)
    RETURN_NAMES = ('project_id','audio','reference_image',
                    'frames','width','height','prompt','fps','filename_prefix','reference_audio')

    @classmethod
    def INPUT_TYPES(cls):
        old=TalkingRH.INPUT_TYPES()
        required={key:old['required'][key] for key in ('audio','reference_image')}
        required.update({name:('INT',{'forceInput':True,'tooltip':'连接外部分辨率预设节点；宽高须为32的整数倍。'})
                         for name in ('width','height')})
        required['backend']=(['本地','RunningHub'],{'tooltip':'本地使用右侧模型和采样链路；RH使用保存的云端工作流，右侧链路不执行。'})
        required.update({key:old['required'][key] for key in ('workflow_id','region','instance_type','project_id')})
        required['fixed_prompt']=old['optional']['fixed_prompt']
        required['audio_processing']=(['直接使用','提取人声'],{'default':'直接使用','tooltip':'干声直接使用；带背景音乐时提取人声。仅创建新项目时处理整条音频一次。'})
        return {'required':required,'optional':{},'hidden':old['hidden']}

    @classmethod
    def legacy_prompt(cls,prompt,unique_id):
        value=copy.deepcopy(prompt)
        if value is None:return None
        for node in value.values():
            for name,item in node.get('inputs',{}).items():
                if isinstance(item,list) and len(item)==2 and str(item[0])==str(unique_id) and isinstance(item[1],int):
                    if not 0<=item[1]<len(cls.LEGACY_SLOTS):raise ValueError('口播输出接线已过期，请重新导入当前工作流。')
                    node['inputs'][name]=[str(item[0]),cls.LEGACY_SLOTS[item[1]]]
        return value

    def prepare(self,audio,reference_image,backend='本地',workflow_id='',region='海外版',instance_type='ultra',
                megapixels=1.,seed=9260,project_id='',fixed_prompt=overlap.DEFAULT_PROMPT,
                prompt=None,unique_id=None,extra_pnginfo=None,width=None,height=None,audio_processing='直接使用'):
        text=str(fixed_prompt).strip()
        result=super().prepare(audio=audio,reference_image=reference_image,
            backend=backend,workflow_id=workflow_id,region=region,instance_type=instance_type,
            megapixels=megapixels,width=width,height=height,seed=seed,project_id=project_id,fixed_prompt=text,
            execution_mode='画布' if backend=='本地' else '内置',continuation_mode='独立窗口（自然动作）',
            prompt=self.legacy_prompt(prompt,unique_id),unique_id=unique_id,extra_pnginfo=extra_pnginfo,seed_from_sampler=True,audio_processing=audio_processing)
        result['result']=tuple(result['result'][index] for index in self.LEGACY_SLOTS)
        return result
