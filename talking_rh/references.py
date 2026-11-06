"""Replace a talking reference independently of the approved audio timeline."""
import copy
import hashlib
import math
import shutil
import time
import uuid

import numpy as np
from PIL import Image
import soundfile as sf

from ..core import LOCK, audio_file, inside, project_path, read_plan, write_plan
from . import flat, project, runner


def same_picture(a,b):
    return a.size==b.size and a.convert('RGB').tobytes()==b.convert('RGB').tobytes()


def original(directory,asset):
    record=asset.get('original') or asset
    path=inside(directory/'refs',directory/'refs'/record['name'])
    if project.digest(path)!=record['sha256']:raise ValueError('项目原图已变化，请检查原项目文件。')
    with Image.open(path) as image:return image.convert('RGB')


def read_capture(descriptor):
    import folder_paths
    if not isinstance(descriptor,dict) or descriptor.get('type')!='temp':
        raise ValueError('参考图同步需要当前画布生成的临时图片。')
    root=folder_paths.get_temp_directory()
    from pathlib import Path
    path=inside(root,Path(root)/descriptor.get('subfolder','')/descriptor.get('filename',''))
    if not path.name.startswith('h3talk-reference-') or path.suffix!='.png':
        raise ValueError('参考图同步文件无效，请重新读取画布图片。')
    with Image.open(path) as image:
        if getattr(image,'n_frames',1)!=1:raise ValueError('口播参考图必须是单张图片。')
        return image.convert('RGB')


def update(root,pid,picture,expected_fingerprint,*,confirmed_sha256=None):
    directory=project_path(root,pid)
    picture=picture.convert('RGB')
    candidate=hashlib.sha256(str(picture.size).encode()+picture.tobytes()).hexdigest()
    with LOCK,runner.claim(directory):
        plan=read_plan(root,pid)
        if project.fingerprint(plan)!=expected_fingerprint:
            raise ValueError('项目已在其他窗口改变，请刷新后重新同步参考图。')
        if not flat.enabled(plan) or len(plan['assets'])!=1:
            raise ValueError('参考图独立更新适用于当前首尾锚定口播；旧版项目请保持原图。')
        project.verify_assets(root,plan)
        if same_picture(original(directory,plan['assets'][0]),picture):
            return {'status':'unchanged','plan':plan}
        project.require_settled(plan['segments'])
        if plan.get('regeneration'):
            raise ValueError('请先完成或取消当前单段重做，再更换整条口播的参考图。')
        has_results=bool(plan.get('final_video') or any(r.get('job') or r.get('previous_jobs') for r in plan['segments']))
        if has_results and confirmed_sha256!=candidate:
            if confirmed_sha256:raise ValueError('待更换图片已变化，请重新确认。')
            return {'status':'confirmation_required','candidate_sha256':candidate,'plan':plan}
        updated=copy.deepcopy(plan)
        if has_results:
            # Copy only the inputs and reviewed timeline. Never copy take paths,
            # progress, failures or final output into a different-image project.
            keep=('kind','schema','mode','sample_rate','samples','duration','max_seconds','config',
                  'input_fingerprint','source_sha256','segments_clock','speech_segmentation',
                  'audio_preparation','manual_cuts','overlap_cuts','approved','first_condition',
                  'multi_reference_visual_accepted')
            updated={k:copy.deepcopy(plan[k]) for k in keep if k in plan}
            updated.update(id=uuid.uuid4().hex,created=time.time(),parent_project=pid,
                           run_status='ready' if plan.get('approved') else 'draft',pause_requested=False,
                           stop_requested=False,segments=[])
            for timing,row in zip(plan['segments_clock'],plan['segments']):
                new=copy.deepcopy(timing)
                for key in ('prompt_draft','generation_prompt'):
                    if key in row:new[key]=copy.deepcopy(row[key])
                updated['segments'].append(new)
            destination=project_path(root,updated['id'])
            for name in ('audio','refs','state','takes','cache','work'):(destination/name).mkdir(parents=True,exist_ok=True)
            for path in (directory/'audio').iterdir():
                if path.is_file():shutil.copy2(path,destination/'audio'/path.name)
        else:destination=directory
        # New filenames leave the old plan's inputs intact even if saving fails.
        asset={}
        if plan['config'].get('reference_geometry'):
            source=destination/'refs'/f'original_{candidate}.png';picture.save(source)
            asset['original']={'name':source.name,'sha256':project.digest(source),'size':list(picture.size)}
            picture=flat.reference_image(picture,project.output_size(plan['config']))
        path=destination/'refs'/f'reference_{candidate}.png';picture.save(path)
        asset.update(name=path.name,sha256=project.digest(path))
        updated.update(assets=[asset],reference_revision={'sha256':candidate,'updated_at':time.time()})
        if updated.get('approved'):updated['approved_fingerprint']=project.fingerprint(updated)
        project.verify_assets(root,updated)
        write_plan(root,updated)
        return {'status':'forked' if has_results else 'updated','plan':updated}


def inputs_match(root,plan,wave,sr,pictures,config,*,check_picture=True):
    """Resume after an explicit picture update without weakening audio/config checks."""
    if not flat.enabled(plan) or config!=plan['config'] or len(pictures)!=1:return False
    directory=project_path(root,plan['id'])
    if check_picture and not same_picture(original(directory,plan['assets'][0]),pictures[0]):return False
    if sr!=24000:
        from scipy.signal import resample_poly
        divisor=math.gcd(sr,24000)
        wave=resample_poly(wave,24000//divisor,sr//divisor,axis=0).astype('float32')
    saved,saved_sr=sf.read(audio_file(directory,'source.wav'),dtype='float32',always_2d=True)
    return saved_sr==24000 and np.array_equal(saved,wave)


class ReferenceCapture:
    """Internal CPU output; uses the same rounding as project preparation."""
    CATEGORY='H3LongVideo/internal'
    FUNCTION='capture'
    RETURN_TYPES=()
    OUTPUT_NODE=True
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'image':('IMAGE',)}}
    def capture(self,image):
        import folder_paths
        from pathlib import Path
        value=image.detach().cpu().numpy()
        if value.ndim!=4 or len(value)!=1 or value.shape[-1] not in (3,4) or not np.isfinite(value).all():
            raise ValueError('每个参考图输入须为一张有效图片。')
        picture=Image.fromarray(np.rint(np.clip(value[0,:,:,:3],0,1)*255).astype('uint8'))
        name=f'h3talk-reference-{uuid.uuid4().hex}.png'
        picture.save(Path(folder_paths.get_temp_directory())/name)
        return {'ui':{'images':[{'filename':name,'subfolder':'','type':'temp'}]},'result':()}
