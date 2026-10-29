"""CPU project preparation and the shared H3 audio/video/latent clock."""
import copy
import hashlib
import json
import math
from pathlib import Path
import time
import uuid

import numpy as np
from PIL import Image
import soundfile as sf
from . import overlap

from ..core import LOCK, audio_file, envelope, inside, project_path, read_plan, write_plan

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


def output_size(config):
    if 'width' not in config and 'height' not in config:return dimensions(config['megapixels'])
    width,height=config.get('width'),config.get('height')
    if any(type(value) is not int or not 64<=value<=8192 or value%32 for value in (width,height)):
        raise ValueError('外部分辨率宽高须为64–8192之间、32的整数倍，请使用分辨率预设节点。')
    return width,height


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


def condition_audio(wave,row):
    # A rounded video end may precede the final source sample by up to half a frame.
    length=max(row['generation_frames']*1000,row['end_sample']-row['audio_start_sample'])
    result=np.zeros((length,1),dtype='float32')
    part=wave[row['audio_start_sample']:row['audio_start_sample']+length].mean(axis=1,keepdims=True)
    result[:len(part)]=part
    return result


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


def unmasked(plan):
    return plan['config'].get('person_mask_mode')=='none'


def reference_assets(plan):
    return plan['assets'] if unmasked(plan) else plan['assets'][:-1]


def prepare(root, wave, sr, pictures, mask, config, input_fingerprint):
    from scipy.signal import resample_poly
    if sr != 24000:
        divisor = math.gcd(sr,24000)
        wave = resample_poly(wave,24000//divisor,sr//divisor,axis=0).astype('float32')
    if len(wave)<1000 or not np.isfinite(wave).all():
        raise ValueError('请提供至少一帧长度的有效口播音频。')
    if not 1<=len(pictures)<=6:
        raise ValueError('需要1至6张参考图。')
    target = output_size(config)
    image=None
    if config.get('person_mask_mode')=='none':
        if mask is not None:raise ValueError('无人物空间遮罩项目不能同时传入人物MASK。')
    else:
        mask = np.asarray(mask,dtype='float32')
        if mask.ndim!=2 or not np.isfinite(mask).all() or mask.min()<0 or mask.max()>1:
            raise ValueError('人物活动遮罩必须是0至1的单张MASK。')
        # Native media loaders can decode 8-bit white as 65280/65535.
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
    pid = uuid.uuid4().hex
    directory = project_path(root,pid)
    for name in ('audio','refs','state','takes','cache','work'):
        (directory/name).mkdir(parents=True,exist_ok=True)
    sf.write(audio_file(directory,'source.wav'),wave,24000,subtype='FLOAT')
    prepared=None;voice=wave
    if config.get('audio_preparation_version')==1:
        from ..audio_preparation import cache_reference
        prepared=cache_reference(directory,config['audio_processing'])
        voice=sf.read(audio_file(directory,'vocals.wav'),dtype='float32',always_2d=True)[0]
    mono = voice.mean(axis=1,keepdims=True)
    speech_segmentation=None
    if overlap.enabled({'config':config}):
        if not 1<=len(pictures)<=2 or mask is not None:raise ValueError('独立窗口口播使用人物图和可选背景图，最多两张，不使用人物空间遮罩。')
        from . import speech
        speech_segmentation=speech.plan(voice)
        rows=overlap.manual_clock(len(wave),speech_segmentation['cuts'])
    else:
        cuts = auto_cuts(mono,float(config['first_seconds']),float(config['segment_seconds']))
        rows = clock(len(wave),cuts)
    assets = []
    for index,picture in enumerate(pictures):
        path = directory/'refs'/f'reference_{index}.png'
        picture.save(path)
        assets.append({'name':path.name,'sha256':digest(path)})
    if image is not None:
        mask_path = directory/'refs/person_mask.png'
        image.save(mask_path)
        assets.append({'name':mask_path.name,'sha256':digest(mask_path)})
    plan = {'id':pid,'kind':'talking_rh','schema':1,'created':time.time(),'mode':'speaking',
            'sample_rate':24000,'samples':len(wave),'duration':len(wave)/24000,
            'max_seconds':13.667,'config':config,'assets':assets,'input_fingerprint':input_fingerprint,
            'source_sha256':digest(audio_file(directory,'source.wav')),'segments_clock':rows,
            'segments':[dict(r) for r in rows],'approved':False,'run_status':'draft','pause_requested':False,
            'first_condition':'ref2va' if config.get('talking_profile') in ('ref2va-v1',overlap.PROFILE) else ('endpoint' if len(pictures)==1 else 'ref2va'),
            'multi_reference_visual_accepted':False}
    if speech_segmentation is not None:plan['speech_segmentation']=speech_segmentation
    if prepared is not None:plan['audio_preparation']=prepared
    write_plan(root,plan)
    return plan


def verify_clock(plan):
    if overlap.enabled(plan):return overlap.verify_clock(plan)
    if plan.get('config',{}).get('talking_profile')!='ref2va-v1':return
    expected=clock(plan['samples'],[r['end_sample']//1000 for r in plan['segments_clock'][:-1]])
    if (plan['segments_clock']!=expected or len(plan['segments'])!=len(expected)
            or any(any(row.get(k)!=v for k,v in timing.items()) for row,timing in zip(plan['segments'],expected))):
        raise ValueError('执行片段与已审核的音频、裁切或续接时钟不一致，已停止提交。')
    if any(row.get('job',{}).get('status')=='completed'
           and row['job'].get('edit_start_frame',0)!=row['edit_start_frame'] for row in plan['segments']):
        raise ValueError('已完成片段的裁切记录与项目时钟不一致，已停止合并。')


def verify_assets(root,plan):
    from .graph import TEMPLATE
    directory = project_path(root,plan['id'])
    verify_clock(plan)
    if plan['config'].get('talking_profile') in ('ref2va-v1',overlap.PROFILE):
        if plan['config'].get('canvas'):
            from .canvas import verify_ref2va
            verify_ref2va(plan['config']['canvas'])
    if plan['config'].get('rh_sampling'):
        from .sampling import verify
        verify(plan['config']['rh_sampling'])
    if digest(TEMPLATE)!=plan['config']['template_sha256']:
        raise ValueError('云端生成模板已变化，请保留旧项目并新建项目。')
    if digest(audio_file(directory,'source.wav')) != plan['source_sha256']:
        raise ValueError('项目原音频已变化，请新建项目。')
    if plan['config'].get('audio_preparation_version')==1:
        from ..audio_preparation import verify_reference
        record=plan.get('audio_preparation') or {}
        verify_reference(directory,record)
        if record['mode']!=plan['config']['audio_processing']:raise ValueError('项目音频处理方式已变化，请新建项目。')
    for item in plan['assets']:
        path = directory/'refs'/item['name']
        if path.parent != directory/'refs' or digest(path)!=item['sha256']:
            raise ValueError('项目参考图或遮罩已变化，请新建项目。')


def update_cuts(root,pid,seconds):
    plan = read_plan(root,pid)
    if overlap.enabled(plan):return overlap.update_cuts(root,pid,seconds)
    if plan.get('regeneration'):
        raise ValueError('请先完成当前重做或恢复重做前结果，再修改切点。')
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


def verify_saved_job(directory,job):
    for kind in ('video','latent','raw_latent','preview'):
        if kind in ('raw_latent','preview') and not job.get(kind):continue
        if kind=='latent' and job.get('talking_profile')==overlap.PROFILE:continue
        if not job.get(kind):raise ValueError('已保存片段缺少必要的原片或续接latent。')
        if digest(inside(directory,job[kind]))!=job[kind+'_sha256']:
            raise ValueError('已保存片段文件已变化，请检查项目文件；没有自动重生成或恢复。')


def require_settled(rows,*,allow_finished_provider=False):
    for row in rows:
        job=row.get('job',{})
        if job.get('status') in (None,'completed','failed','rejected'):continue
        if (allow_finished_provider and job.get('task_id')
                and job.get('provider_status') in ('SUCCESS','FAILED','ERROR','CANCELED','CANCELLED','MISSING')):continue
        raise ValueError('有尚未收集或创建结果未知的任务，请先继续查询或绑定任务ID；没有丢弃任务或重复提交。')


def verify_final(root,pid,path,expected=None):
    from ..storage import project_roots
    if not path:return ''
    resolved=Path(path).resolve()
    owners=(project_path(root,pid).resolve(),*(base.parent/'final_videos' for base in project_roots(root)))
    if not any(resolved.is_relative_to(owner) for owner in owners):
        raise ValueError('旧成片不在此插件的项目或成片目录内。')
    actual=digest(resolved)
    if expected is not None and actual!=expected:raise ValueError('重做前成片已变化，已停止恢复。')
    return actual


def request_regeneration(root,pid,index):
    from .runner import claim
    directory=project_path(root,pid)
    with LOCK,claim(directory):
        plan=read_plan(root,pid)
        if plan.get('regeneration'):raise ValueError('已有待完成的重做，请先继续或恢复重做前结果。')
        if not plan.get('approved') or plan.get('approved_fingerprint')!=fingerprint(plan):
            raise ValueError('请先保存并确认当前口播分段。')
        if not 0<=index<len(plan['segments']):raise ValueError('段号超出范围。')
        if plan['segments'][index].get('job',{}).get('status')!='completed':
            raise ValueError('仅已完成的片段可重做；失败任务请使用重试失败段。')
        require_settled(plan['segments'])
        verify_assets(root,plan)
        for row in plan['segments']:
            if row.get('job',{}).get('status')=='completed':verify_saved_job(directory,row['job'])
        stop=index+1 if overlap.enabled(plan) else len(plan['segments'])
        plan['regeneration']={'id':uuid.uuid4().hex,'index':index,'stop':stop,
            'fingerprint':fingerprint(plan),
            'segments':copy.deepcopy(plan['segments'][index:stop]),
            'overlap_seams':copy.deepcopy(plan.get('overlap_seams',{})),
            'overlap_cuts':copy.deepcopy(plan.get('overlap_cuts')),
            'final_video':plan.get('final_video',''),'final_stale':plan.get('final_stale',False),
            'final_sha256':verify_final(root,pid,plan.get('final_video','')),
            'run_status':plan.get('run_status','paused')}
        for row in plan['segments'][index:stop]:
            if overlap.enabled(plan):
                from .prompts import freeze_redo
                row['generation_prompt']=freeze_redo(directory,plan,row)
            if row.get('job'):row.setdefault('previous_jobs',[]).append(row.pop('job'))
        if overlap.enabled(plan):overlap.invalidate(plan,index)
        plan.update(final_stale=True,run_status='ready',pause_requested=False,error='')
        write_plan(root,plan)
        return plan


def finish_regeneration(plan,outcome='completed'):
    previous=plan.pop('regeneration',None)
    if previous:
        previous['outcome']=outcome
        plan.setdefault('regeneration_history',[]).append(previous)


def restore_regeneration(root,pid):
    from .runner import claim
    directory=project_path(root,pid)
    with LOCK,claim(directory):
        plan=read_plan(root,pid);previous=plan.get('regeneration')
        if not previous:raise ValueError('没有待恢复的重做记录。')
        if previous['fingerprint']!=fingerprint(plan):raise ValueError('项目配置已变化，不能恢复不匹配的旧结果。')
        require_settled(plan['segments'],allow_finished_provider=True)
        verify_assets(root,plan)
        restored=copy.deepcopy(previous['segments'])
        stop=previous.get('stop',len(plan['segments']))
        for row in [*plan['segments'][:previous['index']],*restored,*plan['segments'][stop:]]:
            if row.get('job',{}).get('status')=='completed':verify_saved_job(directory,row['job'])
        verify_final(root,pid,previous['final_video'],previous['final_sha256'])
        for old,current in zip(restored,plan['segments'][previous['index']:stop]):
            history=old.setdefault('previous_jobs',[])
            for job in [*current.get('previous_jobs',[]),current.get('job')]:
                if job and job!=old.get('job') and job not in history:history.append(copy.deepcopy(job))
        plan['segments'][previous['index']:stop]=restored
        if overlap.enabled(plan):
            plan['overlap_seams']=copy.deepcopy(previous['overlap_seams'])
            if previous.get('overlap_cuts') is not None:plan['overlap_cuts']=copy.deepcopy(previous['overlap_cuts'])
            else:plan.pop('overlap_cuts',None)
        plan.update(final_video=previous['final_video'],final_stale=previous['final_stale'],
                    run_status=previous['run_status'],pause_requested=False,error='')
        if overlap.enabled(plan):plan['final_sha256']=previous['final_sha256']
        finish_regeneration(plan,'restored')
        write_plan(root,plan)
        return plan
