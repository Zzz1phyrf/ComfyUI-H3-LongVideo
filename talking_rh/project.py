"""CPU project preparation and the shared H3 audio/video/latent clock."""
import hashlib
import json
import math
from pathlib import Path
import time
import uuid

import numpy as np
from PIL import Image
import soundfile as sf

from ..core import audio_file, envelope, project_path, read_plan, write_plan

CONTEXT = 39
PREFIX_TOKENS = 12


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def dimensions(megapixels):
    if float(megapixels) not in (.6, .8, 1.0):
        raise ValueError('支持0.6、0.8或1.0 MP。')
    scale = math.sqrt(float(megapixels)*1024*1024/(9*16))
    return round(9*scale/32)*32, round(16*scale/32)*32


def latent_frames(frames):
    if frames < 5 or (frames-5) % 17:
        raise ValueError('续接尾部没有对齐H3帧网格。')
    return 2+(frames-5)//17*5


def clock(samples, cuts):
    """Cuts are absolute output frame indices; the final sample never rounds away."""
    total = round(int(samples)/1000)
    cuts = [int(x) for x in cuts]
    if total < 1 or cuts != sorted(set(cuts)) or any(not 0 < x < total for x in cuts):
        raise ValueError('切点必须递增且位于音频范围内。')
    if cuts and (cuts[0] < 56 or any((x-5) % 17 for x in cuts)):
        raise ValueError('首段至少2.333秒，切点需要对齐续接网格。')
    boundaries = [0, *cuts, total]
    result = []
    for i, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        crop = CONTEXT if i else 0
        kept = end-start
        maximum = 306 if i else 328
        if kept > maximum:
            raise ValueError(f'第{i+1}段过长，请增加切点；首段最多13.667秒，后段最多12.75秒。')
        generated = max(175, 5+17*math.ceil((crop+kept+(17 if i==0 else 0)-5)/17))
        row = {'index':i, 'start_sample':start*1000,
               'end_sample':int(samples) if i==len(boundaries)-2 else end*1000,
               'edit_frames':kept, 'generation_frames':generated, 'edit_start_frame':crop,
               'audio_start_sample':(start-crop)*1000,
               'actual_last_retained_frame':crop+kept-1,
               'tail_token_start':None if i==len(boundaries)-2 else latent_frames(crop+kept)-PREFIX_TOKENS}
        if row['audio_start_sample'] < 0 or generated > 345:
            raise ValueError('分段上下文或生成长度无效，请重新调整切点。')
        result.append(row)
    assert result[-1]['end_sample'] == samples
    assert sum(r['edit_frames'] for r in result) == total
    return result


def snap_cuts(seconds, samples):
    cuts = [5+17*round((float(t)*24-5)/17) for t in seconds]
    clock(samples, cuts)
    return cuts


def auto_cuts(wave, first_seconds, segment_seconds):
    total = round(len(wave)/1000)
    starts, db = envelope(wave, 24000)
    cursor, cuts = 0, []
    while total-cursor > (328 if not cuts else 306) or total-cursor > round((first_seconds if not cuts else segment_seconds)*24)+17:
        target = min(328, round(first_seconds*24)) if not cuts else cursor+min(272, round(segment_seconds*24))
        options = [f for f in range(56 if not cuts else cursor+17, min(total-16, cursor+(328 if not cuts else 306))+1)
                   if (f-5) % 17 == 0 and abs(f-target) <= 25]
        if not options:
            options = [f for f in range(cursor+1, min(total-1,cursor+(328 if not cuts else 306))+1)
                       if (f-5) % 17 == 0 and (cuts or f>=56)]
        if not options:
            break
        cut = min(options, key=lambda f: float(db[min(len(db)-1,np.searchsorted(starts,f*1000))])+abs(f-target)*.8)
        cuts.append(cut)
        cursor = cut
    clock(len(wave), cuts)
    return cuts


def crop_box(size, target):
    w,h = size
    cw,ch = (round(h*target[0]/target[1]),h) if w/h > target[0]/target[1] else (w,round(w*target[1]/target[0]))
    x,y = (w-cw)//2,(h-ch)//2
    return x,y,x+cw,y+ch


def fingerprint(plan):
    value = {k:plan[k] for k in ('config','assets','segments_clock','samples')}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def prepare(root, wave, sr, pictures, mask, config, input_fingerprint):
    from scipy.signal import resample_poly
    if sr != 24000:
        divisor = math.gcd(sr,24000)
        wave = resample_poly(wave,24000//divisor,sr//divisor,axis=0).astype('float32')
    if len(wave)<1000 or not np.isfinite(wave).all():
        raise ValueError('请提供至少一帧长度的有效口播音频。')
    if not 1<=len(pictures)<=6:
        raise ValueError('需要1至6张参考图。')
    target = dimensions(config['megapixels'])
    mask = np.asarray(mask,dtype='float32')
    if mask.ndim!=2 or not np.isfinite(mask).all() or mask.min()<0 or mask.max()>1:
        raise ValueError('人物活动遮罩必须是0至1的单张MASK。')
    # Native media loaders can decode 8-bit white as 65280/65535. Keep
    # intended white/black interiors exact while retaining the feather ramp.
    mask = np.where(mask>=.99,1,np.where(mask<=.001,0,mask))
    size = (mask.shape[1],mask.shape[0])
    image = Image.fromarray(np.rint(mask*255).astype('uint8'))
    if size == target:
        pass
    elif size == pictures[0].size:
        image = image.crop(crop_box(size,target)).resize(target,Image.Resampling.BILINEAR)
    else:
        raise ValueError('MASK尺寸须与主参考图或最终生成画面相同。')
    if np.asarray(image).min()!=0 or np.asarray(image).max()!=255:
        raise ValueError('请提供同时包含背景黑区和人物活动白区的遮罩。')
    mono = wave.mean(axis=1,keepdims=True)
    cuts = auto_cuts(mono,float(config['first_seconds']),float(config['segment_seconds']))
    rows = clock(len(wave),cuts)
    pid = uuid.uuid4().hex
    directory = project_path(root,pid)
    for name in ('audio','refs','state','takes','cache','work'):
        (directory/name).mkdir(parents=True,exist_ok=True)
    sf.write(audio_file(directory,'source.wav'),wave,24000,subtype='FLOAT')
    assets = []
    for index,picture in enumerate(pictures):
        path = directory/'refs'/f'reference_{index}.png'
        picture.save(path)
        assets.append({'name':path.name,'sha256':digest(path)})
    mask_path = directory/'refs/person_mask.png'
    image.save(mask_path)
    assets.append({'name':mask_path.name,'sha256':digest(mask_path)})
    plan = {'id':pid,'kind':'talking_rh','schema':1,'created':time.time(),'mode':'speaking',
            'sample_rate':24000,'samples':len(wave),'duration':len(wave)/24000,
            'max_seconds':13.667,'config':config,'assets':assets,'input_fingerprint':input_fingerprint,
            'source_sha256':digest(audio_file(directory,'source.wav')),'segments_clock':rows,
            'segments':[dict(r) for r in rows],'approved':False,'run_status':'draft','pause_requested':False,
            'first_condition':'endpoint' if len(pictures)==1 else 'ref2va',
            'multi_reference_visual_accepted':False}
    write_plan(root,plan)
    return plan


def verify_assets(root,plan):
    from .graph import TEMPLATE
    directory = project_path(root,plan['id'])
    if digest(TEMPLATE)!=plan['config']['template_sha256']:
        raise ValueError('云端生成模板已变化，请保留旧项目并新建项目。')
    if digest(audio_file(directory,'source.wav')) != plan['source_sha256']:
        raise ValueError('项目原音频已变化，请新建项目。')
    for item in plan['assets']:
        path = directory/'refs'/item['name']
        if path.parent != directory/'refs' or digest(path)!=item['sha256']:
            raise ValueError('项目参考图或遮罩已变化，请新建项目。')


def update_cuts(root,pid,seconds):
    plan = read_plan(root,pid)
    cuts = snap_cuts(seconds,plan['samples'])
    rows = clock(plan['samples'],cuts)
    first = next((i for i,(a,b) in enumerate(zip(plan['segments_clock'],rows)) if a!=b),min(len(rows),len(plan['segments'])))
    if any(r.get('job',{}).get('status') not in (None,'completed','failed','rejected') for r in plan['segments'][first:]):
        raise ValueError('有尚未收集的生成任务，请先继续查询完成后再改切点。')
    if first<len(plan['segments']):
        history = project_path(root,pid)/'state'/('before-cuts-'+uuid.uuid4().hex+'.json')
        history.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    old = plan['segments']
    segments=[]
    for i,row in enumerate(rows):
        if i<first:
            segments.append(old[i])
        else:
            history = list(old[i].get('previous_jobs',[])) if i<len(old) else []
            if i<len(old) and old[i].get('job'):
                history.append(old[i]['job'])
            segments.append(dict(row,previous_jobs=history))
    plan.update(segments_clock=rows,segments=segments,approved=False,run_status='draft')
    if first<len(old):
        plan['final_stale']=True
    write_plan(root,plan)
    return plan
