"""Run the shared talking-head graph in this ComfyUI, without network or RH."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from ..core import inside
from ..storage import TALKING_PREFIX
from .project import digest
from .remote import Rejected

MODEL_FIELDS = {'local_model':'diffusion_models','local_clip':'text_encoders',
                'local_video_vae':'vae','local_audio_vae':'vae','local_upscaler':'latent_upscale_models'}
UNSELECTED = '请选择本地权重'
NO_LORA = '不使用'


def model_choices(field):
    try:
        import folder_paths
        names = folder_paths.get_filename_list(MODEL_FIELDS.get(field,'loras'))
    except ImportError:
        names = []
    words = ('minimax','h3_upscaler')
    names=[n for n in names if any(w in n.lower() for w in words)]
    if field=='local_video_vae':names=[n for n in names if 'video_vae' in n.lower()]
    if field=='local_audio_vae':names=[n for n in names if 'audio_vae' in n.lower()]
    return [NO_LORA if field=='local_lora' else UNSELECTED]+names


def validate_models(config):
    import folder_paths
    for field,category in MODEL_FIELDS.items():
        name=config.get('models',{}).get(field,'')
        if not name or name==UNSELECTED or not folder_paths.get_full_path(category,name):
            raise ValueError(f'请明确选择已安装的本地权重：{field}。本地模式不使用RH资源或Key。')
    lora=config['models'].get('local_lora',NO_LORA)
    if lora!=NO_LORA and not folder_paths.get_full_path('loras',lora):
        raise ValueError('所选本地LoRA不存在，请重新选择。')


class LocalClient:
    def __init__(self,config,*,server=None,input_root=None,output_root=None,validate=None):
        from .canvas import output_ids
        self.output_ids=output_ids(config)
        self.client_id=None
        if server is None:
            import execution
            import folder_paths
            from server import PromptServer
            if config.get('execution_mode')!='canvas':validate_models(config)
            server=PromptServer.instance
            input_root=folder_paths.get_input_directory()
            output_root=folder_paths.get_output_directory()
            validate=execution.validate_prompt
        self.server=server;self.validate=validate
        self.input=Path(input_root).resolve();self.output=Path(output_root).resolve()
        self.records=self.output/TALKING_PREFIX/'local_tasks'
        self.records.mkdir(parents=True,exist_ok=True)

    def upload(self,path,*,attempt=''):
        source=Path(path)
        # ComfyUI's annotated input resolver accepts subdirectories for all loaders.
        relative=Path('H3TalkingInputs')/(digest(source)+source.suffix)
        if attempt:
            if len(attempt)!=12 or any(c not in '0123456789abcdef' for c in attempt):
                raise ValueError('本地音频执行标识无效。')
            # Identical source audio must still invalidate the sampler cache on redo.
            relative=relative.parent/attempt/relative.name
        target=self.input/relative
        if not target.is_file():
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name('.'+target.name+'.'+uuid.uuid4().hex)
            shutil.copyfile(source,temporary);os.replace(temporary,target)
        return relative.as_posix()

    def reserve(self):
        return str(uuid.uuid4())

    def record(self,task_id):
        try:
            if str(uuid.UUID(task_id))!=task_id:raise ValueError()
        except (ValueError,AttributeError):
            raise ValueError('本地任务ID无效。') from None
        path=self.records/(task_id+'.json')
        legacy=self.output/'H3TalkingRH/local_tasks'/path.name
        return legacy if not path.is_file() and legacy.is_file() else path

    def save(self,task_id,value):
        path=self.record(task_id);temporary=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,path)

    def create(self,graph,task_id):
        async def enqueue():
            valid=await self.validate(task_id,graph,list(self.output_ids.values()))
            if not valid[0]:
                raise Rejected('本地采样图校验失败，请检查现有H3/Avatar/KJ节点和模型：'+str(valid[1])[:1200])
            self.save(task_id,{'status':'PENDING','graph':graph})
            number=self.server.number;self.server.number+=1
            extra={'create_time':int(time.time()*1000)}
            if self.client_id:extra['client_id']=self.client_id
            self.server.prompt_queue.put((number,task_id,graph,extra,valid[2],{}))
            return task_id
        return asyncio.run_coroutine_threadsafe(enqueue(),self.server.loop).result(timeout=120)

    def query(self,task_id):
        path=self.record(task_id)
        saved=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if saved.get('status')=='SUCCESS':return saved['result']
        history=self.server.prompt_queue.get_history(prompt_id=task_id).get(task_id)
        if not history:
            running,waiting=self.server.prompt_queue.get_current_queue()
            if task_id in {item[1] for item in running+waiting}:return {'status':'RUNNING'}
            # Completion can move the task from queue to history between reads.
            history=self.server.prompt_queue.get_history(prompt_id=task_id).get(task_id)
        if history:
            if history.get('status',{}).get('status_str')!='success':
                self.save(task_id,{**saved,'status':'FAILED','history':history})
                return {'status':'FAILED'}
            results=[]
            for kind,node in self.output_ids.items():
                extension='.latent' if kind=='latent' else '.mp4'
                output=history.get('outputs',{}).get(node,{})
                entries=sum((output.get(k,[]) for k in ('gifs','images','latents')),[])
                matches=[e for e in entries if e.get('type')=='output' and str(e.get('filename','')).endswith(extension)]
                if len(matches)!=1:raise ValueError('本地任务没有返回唯一的视频与latent输出。')
                entry=matches[0]
                source=inside(self.output,self.output/entry.get('subfolder','')/entry['filename'])
                prefix=saved['graph'][node]['inputs']['filename_prefix']
                expected=inside(self.output,self.output/Path(prefix).parent)
                if source.parent!=expected or not source.is_file():raise ValueError('本地输出不属于当前口播任务。')
                results.append({'nodeId':node,'outputType':extension[1:],'url':str(source)})
            result={'status':'SUCCESS','results':results}
            self.save(task_id,{**saved,'status':'SUCCESS','result':result})
            return result
        # ComfyUI does not resume an interrupted GPU execution after a restart.
        # Never enqueue another task automatically; preserve the intent and files.
        return {'status':'MISSING'}

    def download(self,url,target):
        source=inside(self.output,url);target=Path(target)
        if target.is_file():return
        temporary=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.part')
        shutil.copyfile(source,temporary);os.replace(temporary,target)


class StaticVideoMask:
    """Internal adapter for the single static mask used by this local graph.

    Matches the single-mask B1THW/bilinear contract of Lightricks'
    LTXVSetVideoLatentNoiseMasks; RH continues to use its installed stock node.
    https://github.com/Lightricks/ComfyUI-LTXVideo/blob/master/latents.py
    """
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'samples':('LATENT',),'masks':('MASK',)}}
    RETURN_TYPES=('LATENT',)
    FUNCTION='apply'
    CATEGORY='像素幻想/H3 口播/内部'
    DEPRECATED=True

    def apply(self,samples,masks):
        import torch
        video=samples['samples']
        if video.ndim!=5 or masks.ndim!=3 or len(masks)!=1:
            raise ValueError('口播内部遮罩需要BCTHW视频latent和单张静态MASK。')
        # Reloading the prepared PNG can turn white back into 65280/65535 on
        # native media readers. Reapply the project's endpoint convention before
        # dtype conversion/interpolation; retain the intentional feather ramp.
        masks=masks.float()
        masks=torch.where(masks>=.99,1.,torch.where(masks<=.001,0.,masks))
        spatial=torch.nn.functional.interpolate(masks[:,None],size=video.shape[-2:],mode='bilinear',align_corners=False)
        mask=spatial[:,:,None].to(video).expand(video.shape[0],1,video.shape[2],*video.shape[-2:]).clone()
        return ({**samples,'noise_mask':mask},)
