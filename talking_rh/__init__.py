"""Local talking-head controller node; H3 inference runs only on RH."""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from ..core import read_plan
from . import project
from .graph import TEMPLATE
from .remote import validate_config


def root():
    import folder_paths
    return Path(folder_paths.get_output_directory())/'H3TalkingRH/projects'


class TalkingRH:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{
            'audio':('AUDIO',), 'reference_image':('IMAGE',),
            'person_mask':('MASK',{'tooltip':'白色允许人物活动，黑色保留背景；使用羽化后的运动范围遮罩。尺寸与主图或输出画面相同。'}),
            'workflow_id':('STRING',{'default':'','tooltip':'你有调用权限的RH工作流ID；海外版或国内版须与服务地址对应。'}),
            'region':(['海外版','国内版'],), 'instance_type':(['ultra','plus','default'],),
            'megapixels':([1.0,.6],), 'seed':('INT',{'default':999,'min':0,'max':2**63-1,'control_after_generate':False}),
            'first_seconds':('FLOAT',{'default':13.0,'min':2.3334,'max':13.66,'step':.1}),
            'segment_seconds':('FLOAT',{'default':5.667,'min':2.2,'max':12.0,'step':.1}),
            'project_id':('STRING',{'default':''})},
            'optional':{f'reference_{i}':('IMAGE',) for i in range(2,7)}}

    RETURN_TYPES = ('STRING','STRING','INT')
    RETURN_NAMES = ('project_id','final_video','segment_count')
    FUNCTION = 'prepare'
    CATEGORY = '像素幻想/H3 口播 · RunningHub'
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls,**kwargs):
        return float('nan')

    def prepare(self,audio,reference_image,person_mask,workflow_id,region='海外版',instance_type='ultra',
                megapixels=1.0,seed=999,first_seconds=13.0,segment_seconds=5.667,project_id='',**references):
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
        mask=person_mask.detach().cpu().numpy()
        if mask.ndim!=3 or len(mask)!=1:
            raise ValueError('请提供一张人物活动MASK。')
        signature.update(str(mask.shape).encode()+mask.tobytes())
        config={'base_url':'https://www.runninghub.ai' if region=='海外版' else 'https://www.runninghub.cn',
                'workflow_id':str(workflow_id).strip(),'instance_type':instance_type,
                'megapixels':float(megapixels),'seed':int(seed),'first_seconds':float(first_seconds),
                'segment_seconds':float(segment_seconds),'template_sha256':project.digest(TEMPLATE)}
        validate_config(config)
        if not 2.333<=first_seconds<=13.667 or not 2.2<=segment_seconds<=12:
            raise ValueError('分段目标时长超出支持范围。')
        signature.update(json.dumps(config,sort_keys=True).encode())
        input_id=signature.hexdigest()
        if str(project_id).strip():
            plan=read_plan(root(),project_id.strip())
            if plan.get('kind')!='talking_rh' or plan.get('input_fingerprint')!=input_id:
                raise ValueError('输入素材或配置已变化。请点击“新建项目”以保留旧项目和输出。')
        else:
            plan=project.prepare(root(),wave,sr,pictures,mask[0],config,input_id)
        return {'ui':{'talking_rh_project':[plan['id']],'text':['口播项目已准备，请打开分段审核，试听后保存并确认。']},
                'result':(plan['id'],plan.get('final_video',''),len(plan['segments']))}
