"""Independent Ref2VA windows, audio-clock seam decisions and CPU rendering."""
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import uuid

import numpy as np

from ..core import audio_file, inside, project_path, read_plan, write_plan
from .. import controller

PROFILE = 'ref2va-overlap-v1'
VERSION = 'motion-area-v1'
DEFAULT_PROMPT = '''subject_definitions:
<Subject 1> is the speaking person in <Picture 1>, retaining their appearance and clothing.
<Picture 1> is the composition reference for the person and surroundings.
<Audio 1> is the supplied speech performed by <Subject 1> (S1).

summary:
[reference generation + audio reuse] A single stationary recording of <Subject 1> speaking <Audio 1>, retaining the framing of <Picture 1> throughout.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - identity, clothing and position in the setting.
<Picture 1> ([Shot 1] composition): fully_preserved - image boundaries, person size and background layout.
<Audio 1>: fully_copy - complete speech timing and delivery.

detailed_description:
[Shot 1] The camera remains on a stationary tripod. The framing and visible surroundings match <Picture 1>. The head and torso retain their proportions relative to the image. <Subject 1> (S1) speaks <Audio 1> with synchronized lips, natural expressions, occasional blinks and gentle conversational nods. Between phrases, the hands rest comfortably in their reference position. At a spoken emphasis, one relaxed hand makes a small explanatory gesture within the existing frame, then smoothly returns to rest. The elbows stay near the body, with relaxed pauses between gestures. Subtle breathing and small shoulder movements accompany the delivery. Lighting, skin texture and clothing remain consistent throughout the continuous recording.

overall_soundscape:
The original room sound accompanying the supplied speech is retained.

non_diegetic_music:
N/A
'''


def enabled(plan):
    return plan.get('config',{}).get('talking_profile') == PROFILE


def clock(samples):
    """Tested 175-frame windows, 119 first stride, 136 thereafter; tail anchored."""
    total=math.ceil(int(samples)/1000)
    if total<1:raise ValueError('口播音频不能为空。')
    starts=[0]
    while starts[-1]+175<total:
        start=min(starts[-1]+(119 if len(starts)==1 else 136),total-175)
        if start<=starts[-1]:raise ValueError('独立窗口布局无效。')
        starts.append(start)
    # Provisional timeline only; actual takes and seam decisions remain separate.
    cuts=[0]+[min(starts[i-1]+158,starts[i]+39) for i in range(1,len(starts))]+[total]
    rows=[]
    for i,start in enumerate(starts):
        rows.append(dict(index=i,start_sample=cuts[i]*1000,end_sample=min(samples,cuts[i+1]*1000),
                         edit_frames=cuts[i+1]-cuts[i],generation_frames=175,
                         edit_start_frame=cuts[i]-start,audio_start_sample=start*1000,
                         window_start_frame=start,window_end_frame=start+175,
                         actual_last_retained_frame=cuts[i+1]-start-1,tail_token_start=None))
    return rows


def verify_clock(plan):
    if 'manual_cuts' in plan:
        windows=[(r['window_start_frame'],r['generation_frames']) for r in plan['segments_clock']]
        expected=manual_clock(plan['samples'],plan['manual_cuts'],windows)
    elif plan.get('speech_segmentation',{}).get('algorithm')=='speech-pauses-v1':
        expected=manual_clock(plan['samples'],plan['speech_segmentation']['cuts'])
    else:expected=clock(plan['samples'])
    if plan['segments_clock']!=expected or len(plan['segments'])!=len(expected):
        raise ValueError('独立窗口时钟已变化，请新建项目，未提交生成。')
    for row,timing in zip(plan['segments'],expected):
        if any(row.get(k)!=v for k,v in timing.items()):
            raise ValueError('执行窗口与审核音频时钟不一致。')


def manual_clock(samples,cuts,windows=None):
    """User cuts are output-frame positions; reference conditioning stays independent."""
    total=math.ceil(samples/1000);bounds=[0,*cuts,total];rows=[]
    if cuts!=sorted(set(cuts)) or any(type(f) is not int or not 0<f<total for f in cuts):
        raise ValueError('切点必须递增且位于音频范围内。')
    if windows is not None and len(windows)!=len(bounds)-1:raise ValueError('分段生成时钟数量不符。')
    for i,(start,end) in enumerate(zip(bounds,bounds[1:])):
        if end<=start or end-start>360:raise ValueError('每段须为4–15秒。')
        begin=max(0,start-17)
        generated=max(175,5+17*math.ceil((end-begin+17-5)/17))
        if windows is not None:begin,generated=windows[i]
        if (type(begin) is not int or type(generated) is not int or begin<0 or begin>start
                or begin+generated<end or generated<175 or generated>396 or (generated-5)%17):
            raise ValueError('分段音频与生成时钟不一致。')
        rows.append(dict(index=i,start_sample=start*1000,end_sample=min(samples,end*1000),
                         edit_frames=end-start,generation_frames=generated,edit_start_frame=start-begin,
                         audio_start_sample=begin*1000,window_start_frame=begin,window_end_frame=begin+generated,
                         actual_last_retained_frame=end-begin-1,tail_token_start=None))
    return rows


def update_cuts(root,pid,seconds):
    from . import project,prompts
    from .runner import claim
    directory=project_path(root,pid)
    with project.LOCK,claim(directory):
        plan=read_plan(root,pid)
        if plan.get('regeneration'):raise ValueError('请先完成当前重做或取消重做，再修改切点。')
        project.require_settled(plan['segments']);project.verify_assets(root,plan)
        if not isinstance(seconds,list) or any(type(t) not in (int,float) or not math.isfinite(t) for t in seconds):
            raise ValueError('切点时间无效。')
        cuts=[round(t*24) for t in seconds];total=math.ceil(plan['samples']/1000)
        oldcuts=plan.get('manual_cuts',plan.get('overlap_cuts',[0]+[r['end_sample']//1000 for r in plan['segments']]))
        if 'manual_cuts' not in plan:oldcuts=oldcuts[1:-1]
        if cuts==oldcuts:return plan
        rows=manual_clock(plan['samples'],cuts)
        oldbounds=[0,*oldcuts,total];bounds=[0,*cuts,total];old=plan['segments'];segments=[];windows=[]
        for i,(start,end) in enumerate(zip(bounds,bounds[1:])):
            exact=next((j for j,(a,b) in enumerate(zip(oldbounds,oldbounds[1:])) if (a,b)==(start,end)),None)
            if exact is not None:
                origin=old[exact];windows.append((origin['window_start_frame'],origin['generation_frames']))
                segments.append(copy.deepcopy(origin));continue
            if end-start<96 and not (len(rows)==1 and total<96):raise ValueError('新增或调整后的每段须为4–15秒。')
            source=min(next((j for j,b in enumerate(oldbounds[1:]) if start<b),len(old)-1),len(old)-1)
            origin=old[source];row=dict(rows[i]);history=[]
            for j,(a,b) in enumerate(zip(oldbounds,oldbounds[1:])):
                if a<end and b>start:
                    for job in [*old[j].get('previous_jobs',[]),old[j].get('job')]:
                        if job and job not in history:history.append(copy.deepcopy(job))
            row['previous_jobs']=history
            if origin.get('prompt_draft'):row['prompt_draft']=copy.deepcopy(origin['prompt_draft'])
            else:row['generation_prompt']=prompts.freeze_redo(directory,plan,origin)
            segments.append(row);windows.append((row['window_start_frame'],row['generation_frames']))
        rows=manual_clock(plan['samples'],cuts,windows)
        for row,timing in zip(segments,rows):row.update(timing)
        archive=directory/'state'/('before-cuts-'+uuid.uuid4().hex+'.json')
        archive.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
        plan.update(manual_cuts=cuts,segments_clock=rows,segments=segments,overlap_seams={},
                    overlap_cuts=bounds,max_seconds=15.,approved=False,run_status='draft',final_stale=True,error='')
        write_plan(root,plan)
        return plan


def invalidate(plan,index):
    seams=plan.get('overlap_seams',{})
    for boundary in (index,index+1):seams.pop(str(boundary),None)
    plan.pop('overlap_cuts',None)


def choose(previous,current,previous_start,current_start,minimum=0,maximum=None):
    """Motion-weighted full-frame matching; no portrait-specific hand rectangle."""
    a=previous.astype(np.float32);b=current.astype(np.float32)
    low=max(current_start+8,minimum+1);high=min(previous_start+len(a),current_start+len(b)-3)
    if maximum is not None:high=min(high,maximum)
    if low>high:raise ValueError('相邻窗口没有可用的同时间重叠区。')
    # Locate activity from the overlap itself, adapting to subject placement.
    aa=a[max(0,low-3-previous_start):high-previous_start]
    bb=b[max(0,low-current_start):high+3-current_start]
    activity=np.maximum(np.abs(np.diff(aa,axis=0)).mean(axis=(0,3)),
                        np.abs(np.diff(bb,axis=0)).mean(axis=(0,3)))
    active=activity>max(1.,float(np.quantile(activity,.85)))
    coverage=float(active.mean())
    warnings=[]
    reliable=.001<=coverage<=.5
    if not reliable:warnings.append('动作区域不明确，已按全画面相似度选切，请检查接缝。')
    def area(value):return float(value[active].mean()) if reliable else float(value.mean())
    candidates=[]
    for frame in range(low,high+1):
        i=frame-previous_start;j=frame-current_start
        delta=np.abs(a[i-1]-b[j]).mean(axis=2)
        velocity=np.abs((a[i-1]-a[max(0,i-3)])/2-(b[j+2]-b[j])/2).mean(axis=2)
        cost=float(delta.mean())+.7*area(delta)+.4*area(velocity)
        candidates.append((cost,frame,float(delta.mean()),area(delta)))
    cost,frame,full,moving=min(candidates)
    typical=max(1.,float(np.median(np.abs(np.diff(aa,axis=0)).mean(axis=(1,2,3)))))
    if full>max(3.,typical*4) or moving>12:
        warnings.append('相邻片段动作或姿态差异较大，当前切点需人工预览，不能视为无缝通过。')
    return dict(frame=frame,score=cost,frame_difference=full,motion_difference=moving,
                activity_coverage=coverage,warnings=warnings,algorithm=VERSION)


def decode(path,width,height):
    h=max(16,round(192*height/width/2)*2)
    result=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vf',f'scale=192:{h}',
                           '-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,timeout=120)
    if result.returncode:raise ValueError('读取原片接缝画面失败，请检查FFmpeg及片段文件。')
    frames=np.frombuffer(result.stdout,np.uint8).reshape(-1,h,192,3)
    return frames


def resolve(root,plan,decode_frames=decode):
    from .project import digest,output_size
    verify_clock(plan);directory=project_path(root,plan['id'])
    rows=plan['segments'];seams=copy.deepcopy(plan.get('overlap_seams',{}));decoded={}
    width,height=output_size(plan['config'])
    for row in rows:
        job=row.get('job',{})
        if job.get('status')=='completed' and digest(inside(directory,job['video']))!=job['video_sha256']:
            raise ValueError('独立窗口原片已变化，已停止预览/合并。')
    provisional=[0]+[r['start_sample']//1000 for r in rows[1:]]+[math.ceil(plan['samples']/1000)]
    for i in range(1,len(rows)):
        left,right=rows[i-1:i+1]
        if any(r.get('job',{}).get('status')!='completed' for r in (left,right)):
            seams.pop(str(i),None);continue
        manual=plan.get('manual_cuts')
        boundary=plan.get('speech_segmentation',{}).get('boundaries',[])
        if manual is not None:minimum,maximum=manual[i-1]-1,manual[i-1]
        elif boundary:
            previous=seams.get(str(i-1),{}).get('frame',provisional[i-1])
            minimum=max(boundary[i-1]['low']-1,previous+95,provisional[i+1]-361)
            maximum=min(boundary[i-1]['high'],previous+360,provisional[i+1]-96)
        else:minimum,maximum=(provisional[i-1]+provisional[i])//2,(provisional[i]+provisional[i+1])//2
        identity=hashlib.sha256(json.dumps([VERSION,left['job']['video_sha256'],right['job']['video_sha256'],
                  left['window_start_frame'],right['window_start_frame'],minimum,maximum]).encode()).hexdigest()
        if seams.get(str(i),{}).get('identity')==identity:continue
        for row in (left,right):
            if row['index'] not in decoded:
                decoded[row['index']]=decode_frames(inside(directory,row['job']['video']),width,height)
                if len(decoded[row['index']])!=row['generation_frames']:raise ValueError('原片帧数与分段生成记录不符，已停止合并。')
        decision=choose(decoded[i-1],decoded[i],left['window_start_frame'],right['window_start_frame'],minimum,maximum)
        seams[str(i)]={**decision,'identity':identity}
        decoded={i:decoded[i]}  # Keep at most adjacent windows, even for long audio.
    plan['overlap_seams']=seams
    cuts=[0]+[seams[str(i)]['frame'] for i in range(1,len(rows)) if str(i) in seams]+[math.ceil(plan['samples']/1000)]
    if len(cuts)==len(rows)+1:
        if any(a>=b for a,b in zip(cuts,cuts[1:])):raise ValueError('接缝次序无效，未合并。')
        plan['overlap_cuts']=cuts
    else:plan.pop('overlap_cuts',None)
    write_plan(root,plan)
    return plan


def interval(plan,index):
    rows=plan['segments'];row=rows[index];seams=plan.get('overlap_seams',{})
    if 'manual_cuts' in plan:
        bounds=[0,*plan['manual_cuts'],math.ceil(plan['samples']/1000)]
        return bounds[index],bounds[index+1]
    if index and str(index) not in seams or index+1<len(rows) and str(index+1) not in seams:
        raise ValueError('相邻窗口尚未完成，请先预览窗口原片；最终采用区间还未确定。')
    start=seams[str(index)]['frame'] if index else 0
    end=seams[str(index+1)]['frame'] if index+1<len(rows) else math.ceil(plan['samples']/1000)
    if not row['window_start_frame']<=start<end<=row['window_end_frame']:
        raise ValueError('实际采用区间超出生成窗口。')
    return start,end


def preview(root,pid,index,raw=False):
    from .project import digest
    plan=read_plan(root,pid);verify_clock(plan);directory=project_path(root,pid)
    if not 0<=index<len(plan['segments']):raise ValueError('段号超出范围。')
    row=plan['segments'][index];job=row.get('job',{})
    if job.get('status')!='completed':raise ValueError('本窗口尚未完成。')
    source=inside(directory,job['video'])
    if digest(source)!=job['video_sha256']:raise ValueError('原片文件已变化。')
    if raw:return source
    if digest(audio_file(directory,'source.wav'))!=plan['source_sha256']:
        raise ValueError('项目原声已变化，已停止预览。')
    start,end=interval(plan,index)
    token=hashlib.sha256(json.dumps([job['video_sha256'],start,end,plan['source_sha256']]).encode()).hexdigest()[:20]
    target=directory/'cache'/f'overlap_{index}_{token}.mp4'
    if not target.exists():
        temp=target.with_suffix('.'+uuid.uuid4().hex+'.tmp.mp4')
        try:
            controller.command(['ffmpeg','-v','error','-n','-i',str(source),'-i',str(audio_file(directory,'source.wav')),
                '-filter_complex',f'[0:v]trim=start_frame={start-row["window_start_frame"]}:end_frame={end-row["window_start_frame"]},setpts=N/(24*TB)[v];'
                f'[1:a]apad,atrim=start_sample={start*1000}:end_sample={end*1000},asetpts=PTS-STARTPTS[a]',
                '-map','[v]','-map','[a]','-r','24','-frames:v',str(end-start),
                '-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','320k','-movflags','+faststart',str(temp)])
            if int(controller.probe_video(temp)['nb_read_frames'])!=end-start:raise ValueError('采用区间预览帧数不符。')
            temp.replace(target)
        finally:temp.unlink(missing_ok=True)
    return target


def assemble(root,pid):
    from .project import digest
    plan=read_plan(root,pid);verify_clock(plan)
    if any(r.get('job',{}).get('status')!='completed' for r in plan['segments']):raise ValueError('仍有窗口未完成。')
    plan=resolve(root,plan);directory=project_path(root,pid)
    outputs=Path(root).resolve().parent/'final_videos';outputs.mkdir(parents=True,exist_ok=True)
    target=outputs/f'{pid}_overlap_{uuid.uuid4().hex[:12]}.mp4';temporary=target.with_suffix('.tmp.mp4')
    clips=[preview(root,pid,i) for i in range(len(plan['segments']))]
    # Concat manifest uses generated relative names in a dedicated work folder.
    import os,shutil
    work=directory/'work'/('overlap_'+uuid.uuid4().hex);work.mkdir()
    try:
        for i,path in enumerate(clips):
            dest=work/f'{i:04}.mp4'
            try:os.link(path,dest)
            except OSError:shutil.copyfile(path,dest)
        (work/'concat.txt').write_text('\n'.join(f"file '{i:04}.mp4'" for i in range(len(clips))),encoding='utf-8')
        total=math.ceil(plan['samples']/1000)
        controller.command(['ffmpeg','-v','error','-n','-f','concat','-safe','1','-i',str(work/'concat.txt'),
             '-i',str(audio_file(directory,'source.wav')),'-map','0:v:0','-map','1:a:0','-c:v','copy',
             '-af',f'apad,atrim=end_sample={total*1000}','-c:a','aac','-b:a','320k','-movflags','+faststart',str(temporary)])
        if int(controller.probe_video(temporary)['nb_read_frames'])!=total:raise ValueError('合并帧数与完整音频时钟不符。')
        temporary.replace(target)
        plan=read_plan(root,pid);plan['final_sha256']=digest(target);write_plan(root,plan)
        return str(target)
    finally:
        temporary.unlink(missing_ok=True)
        shutil.rmtree(work,ignore_errors=True)
