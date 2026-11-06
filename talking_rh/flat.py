"""Isolated RH first/last portrait strategy, matching the accepted experiment.

The pause/grid objective follows the reviewed friend's behavior; this module
implements it independently and leaves historical project profiles unchanged.
"""
import copy
import json
import math
from pathlib import Path
import uuid

PROFILE = 'fl2va-flat-rh-v1'
GEOMETRY = 'center-lanczos-v1'
MODE = '原图首尾锚定'
MODES = (MODE, '原图首尾锚定（RH测试）')
LEGACY = '原有续接'
LENGTHS = tuple(range(107, 346, 17))
DEFAULT_PROMPT = ('The camera stays fixed, with framing consistent with the reference image. '
                  'The person speaks naturally with natural hand gestures. '
                  'Their appearance, clothing colors, background and lighting remain consistent throughout.')


def enabled(plan):
    return plan.get('config', {}).get('talking_profile') == PROFILE


def reference_image(picture, target):
    """Match Comfy center-crop rounding once, before the shared first/last input."""
    from PIL import Image
    width, height = picture.size
    aspect, desired = width / height, target[0] / target[1]
    x = round((width - width * desired / aspect) / 2) if aspect > desired else 0
    y = round((height - height * aspect / desired) / 2) if aspect < desired else 0
    return picture.crop((x, y, width-x, height-y)).resize(target, Image.Resampling.LANCZOS)


def clock(samples, cuts):
    total = math.ceil(samples / 1000)
    if total < 1 or cuts != sorted(set(cuts)) or any(type(x) is not int or not 0 < x < total for x in cuts):
        raise ValueError('首尾锚定切点须递增且位于原声范围内。')
    bounds = [0, *cuts, total]
    rows = []
    for index, (start, end) in enumerate(zip(bounds, bounds[1:])):
        kept = end - start
        generated = next((n for n in LENGTHS if n >= kept), None) if end == total else kept
        if generated not in LENGTHS:
            raise ValueError('首尾锚定非末段须为107至345帧的17k+5网格；末段可补齐。请调整相邻切点。')
        rows.append(dict(index=index, start_sample=start*1000, end_sample=min(end*1000, samples),
                         edit_frames=kept, generation_frames=generated, edit_start_frame=0,
                         audio_start_sample=start*1000, window_start_frame=start,
                         window_end_frame=start+generated, actual_last_retained_frame=kept-1,
                         tail_token_start=None))
    return rows


def segment(wave):
    from .speech import pauses
    total = math.ceil(len(wave) / 1000)
    quiet = pauses(wave)
    # DAG shortest path: prefer fewer non-pause boundaries before duration cost.
    best = {0: (0, 0.)}
    previous = {}
    evidence = {frame: next((p for p in quiet if p['low'] <= frame <= p['high']), None)
                for frame in range(total + 1)}
    for start in range(total):
        if start not in best:
            continue
        ends = {start+n for n in LENGTHS if start+n <= total}
        if 96 <= total-start <= LENGTHS[-1] or start == 0 and total < 96:
            ends.add(total)
        for end in sorted(ends):
            pause = evidence[end]
            span = end-start
            center = abs(end-pause['frame'])/max(1, pause['high']-pause['low'])*.04 if pause else 0.
            score = (best[start][0] + int(pause is None),
                     best[start][1] + ((span-144)/144)**2 + .08 + center + 1.5*(max(0, span-209)/209)**2)
            if end not in best or score < best[end]:
                best[end], previous[end] = score, start
    if total not in best:
        raise ValueError('原声无法按首尾锚定网格分段。')
    cuts = []
    cursor = total
    while previous[cursor]:
        cursor = previous[cursor]
        cuts.append(cursor)
    cuts.reverse()
    boundaries = [dict(frame=f, kind='pause' if evidence[f] else 'grid_limit',
                       low=evidence[f]['low'] if evidence[f] else f,
                       high=evidence[f]['high'] if evidence[f] else f) for f in cuts]
    return dict(algorithm='flat-rh-pauses-v1', mode='grid', cuts=cuts, boundaries=boundaries,
                pause_count=len(quiet), fallback_count=sum(p['kind']=='grid_limit' for p in boundaries))


def prompt(text, frames):
    header = ('How the reference pictures align with the target video — '
              'Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; '
              f'Picture 2 (from Shot 1) aligns with the {frames/24:.2f}-second mark of the target video.')
    text = text.strip()
    # Saved take/draft evidence may already contain a complete FL2VA prompt.
    if 'integrated_multimodal_description:' in text:
        return header + '\n\n' + text[text.index('integrated_multimodal_description:'):].rstrip() + '\n'
    return (header + '\n\nintegrated_multimodal_description:\n[Shot 1] ' + text +
            '\n\noverall_soundscape:\nThe supplied speech forms the soundtrack of this recording.'
            '\n\nnon_diegetic_music:\nN/A\n')


def freeze(source, reference_source=None):
    from . import sampling
    source = copy.deepcopy(source)
    sampler, ref = sampling.binding(source)
    condition = source[ref]['inputs']
    if source[ref]['class_type'] != 'MiniMaxH3ImageToVideo' or source[sampler]['class_type'] != 'SamplerCustom':
        raise ValueError('首尾锚定RH模板需要原生ImageToVideo和标准SamplerCustom。')
    first = condition.get('first_frame')
    if not sampling.link(first, source) or first != condition.get('last_frame'):
        raise ValueError('首帧、尾帧须连接同一张原图。')
    if reference_source is not None:
        if first != reference_source:
            raise ValueError('本地首尾图须连接口播节点的规范参考图输出。')
    elif source[str(first[0])]['class_type'] != 'LoadImage':
        raise ValueError('首尾锚定模板须直接使用同一LoadImage原图。')
    guide_ids = [key for key,n in source.items() if n['class_type']=='MiniMaxH3AddGuide']
    guides = [source[key]['inputs'] for key in guide_ids]
    if len(guides) != 1 or 'image' in guides[0] or guides[0].get('frame_idx') != 0:
        raise ValueError('首尾锚定只接受一条frame0音频guide。')
    guide = guides[0]
    if source[sampler]['inputs']['positive'] != [guide_ids[0],0]:
        raise ValueError('采样器正面条件须连接音频guide输出。')
    if guide.get('positive') != [ref, 0] or guide.get('latent') != [ref, 1]:
        raise ValueError('音频guide须直接连接首尾帧条件。')
    merged = source[str(source[sampler]['inputs']['latent_image'][0])]['inputs']
    masked = source[str(merged['audio_latent'][0])]['inputs']
    encoded = source[str(masked['samples'][0])]['inputs']
    if guide.get('audio') != encoded.get('audio') or guide.get('audio_vae') != encoded.get('audio_vae'):
        raise ValueError('参考人声与零罩原声必须使用同一输入及音频VAE。')
    negative = source[sampler]['inputs'].get('negative')
    if not sampling.link(negative, source) or source[str(negative[0])]['class_type'] != 'ConditioningZeroOut':
        raise ValueError('首尾锚定模板须使用零化负面条件。')
    source[str(negative[0])]['inputs']['conditioning'] = [ref, 0]
    if any(n['class_type'] in ('LoadLatent', 'ReplaceVideoLatentFrames', 'LoadVideo') for n in source.values()):
        raise ValueError('首尾锚定模板不能包含上一段视频或latent续接。')
    return sampling.freeze(source)


def capture_canvas(prompt, loader):
    """Keep the user's visible local generation graph, not a hidden preset."""
    from . import canvas
    loader = str(loader)
    if not prompt or loader not in prompt:
        raise ValueError('本地首尾锚定需要完整展开画布，请导入当前H3_口播工作流。')
    outputs=[]
    for key,node in prompt.items():
        if node['class_type']!='SaveVideo':continue
        ids=canvas.ancestors(prompt,key,loader)
        if loader in ids and any(prompt[n]['class_type']=='SamplerCustom' for n in ids):outputs.append((key,ids))
    if len(outputs)!=1:raise ValueError('本地首尾锚定须连接唯一的原片SaveVideo。')
    video,ids=outputs[0]
    source={key:copy.deepcopy(prompt[key]) for key in sorted(ids)}
    source[loader]={'class_type':'H3LVTalkingSegmentState','inputs':{}}
    from .sampling import binding
    sampler,_=binding(source)
    masks=[key for key,n in source.items() if n['class_type']=='SetLatentNoiseMask']
    if len(masks)!=1:raise ValueError('本地首尾锚定须使用唯一的音频零噪声遮罩。')
    saved={'prompt':source,'loader_id':loader,'video_id':video,'sampler_id':sampler,
           'audio_mask_id':masks[0],'profile':PROFILE}
    verify_canvas(saved)
    return saved


def verify_canvas(saved):
    from . import canvas,sampling
    source=saved['prompt'];loader=saved['loader_id'];sampler,ref=sampling.binding(source)
    if sampler!=saved['sampler_id']:raise ValueError('本地采样器绑定已变化。')
    freeze(source,reference_source=[loader,canvas.SLOT['reference_image']])
    condition=source[ref]['inputs']
    for field,slot in [('width','width'),('height','height'),('length','generation_frames'),('prompt','fixed_prompt')]:
        if condition.get(field)!=[loader,canvas.SLOT[slot]]:
            raise ValueError('首尾锚定尺寸、帧数和提示词须连接口播项目输出。')
    guide=next(n['inputs'] for n in source.values() if n['class_type']=='MiniMaxH3AddGuide')
    audio=[loader,canvas.SLOT['condition_audio']]
    if guide.get('audio')!=audio:raise ValueError('首尾锚定参考人声须使用本段原声。')
    def inputs(value,kind,slot=0):
        node=source.get(str(value[0]),{}) if sampling.link(value,source) else {}
        if node.get('class_type')!=kind or value[1]!=slot:raise ValueError('本地首尾锚定接线无效：'+kind)
        return node['inputs']
    combined=inputs(source[sampler]['inputs']['latent_image'],'LTXVConcatAVLatent')
    split=inputs(combined.get('video_latent'),'LTXVSeparateAVLatent')
    if split.get('av_latent')!=[ref,1] or combined.get('audio_latent')!=[saved['audio_mask_id'],0]:
        raise ValueError('首尾锚定须使用当前条件的视频latent与本段零罩原声。')
    raw=inputs(source[saved['video_id']]['inputs'].get('video'),'CreateVideo')
    if raw.get('audio')!=audio or raw.get('fps') not in (24,24.,[loader,canvas.SLOT['fps']]):
        raise ValueError('首尾锚定原片须连接本段原声并使用24fps。')
    decoder=inputs(raw.get('images'),'VAEDecode')
    split=inputs(decoder.get('samples'),'LTXVSeparateAVLatent')
    if split.get('av_latent')!=[sampler,0] or decoder.get('vae')!=condition.get('vae') or decoder.get('vae')==guide.get('audio_vae'):
        raise ValueError('首尾锚定须直接解码当前采样结果，使用同一视频VAE。')
    canvas.sampler_seed(saved)


def build(plan, row, uploaded):
    from . import graph, sampling, prompts, project
    from ..storage import TALKING_PREFIX
    if plan['config'].get('backend') == 'local' or plan['config'].get('continuity'):
        raise ValueError('此首尾锚定候选仅支持RH独立分段。')
    if len(uploaded['references']) != 1 or uploaded.get('previous_latent'):
        raise ValueError('首尾锚定每段须复用一张原图，不接前段latent。')
    value = json.loads(graph.TEMPLATE.read_text(encoding='utf-8'))
    width, height = project.output_size(plan['config'])
    value['9000']['inputs']['audio'] = uploaded['audio']
    value['9001']['inputs']['image'] = uploaded['references'][0]
    value['268'] = {'class_type':'MiniMaxH3ImageToVideo', 'inputs':{
        'clip':['128',0], 'vae':['119',0], 'width':width, 'height':height,
        'length':row['generation_frames'], 'prompt':prompts.effective(plan,row),
        'first_frame':['9001',0], 'last_frame':['9001',0]}}
    value['9101'] = {'class_type':'LTXVSeparateAVLatent', 'inputs':{'av_latent':['268',1]}}
    value['246']['inputs']['video_latent'] = ['9101',0]
    value['270']['inputs'].update(seed=plan['config']['seed'], positive=['268',0])
    value['273']['inputs']['filename_prefix'] = f'{TALKING_PREFIX}/projects/{plan["id"]}/takes/{row["index"]:04d}/{uploaded["attempt"]}/generated/raw'
    value = sampling.apply(value, plan['config']['rh_sampling'])
    audio_vae = value['248']['inputs']['audio_vae']
    value['26000'] = {'class_type':'MiniMaxH3AddGuide', 'inputs':{
        'positive':['268',0], 'latent':['268',1], 'audio':['9000',0], 'audio_vae':audio_vae, 'frame_idx':0}}
    value['270']['inputs']['positive'] = ['26000',0]
    negative = value['270']['inputs']['negative']
    value[str(negative[0])]['inputs']['conditioning'] = ['26000',0]
    used = sampling.ancestors(value, ['273'])
    return {k:n for k,n in value.items() if k in used}


def assemble(root, plan):
    """Crop raw frames once and reset the video clock; retain continuous source audio."""
    from .. import controller
    from ..core import audio_file, inside, project_path, read_plan, write_plan
    from .project import digest
    directory=project_path(root,plan['id'])
    source=audio_file(directory,'source.wav')
    if digest(source)!=plan['source_sha256']:raise ValueError('项目原声已变化，已停止合并。')
    outputs=Path(root).resolve().parent/'final_videos';outputs.mkdir(parents=True,exist_ok=True)
    target=outputs/f'{plan["id"]}_flat_{uuid.uuid4().hex[:12]}.mp4'
    temporary=target.with_suffix('.tmp.mp4')
    args=['ffmpeg','-v','error','-n'];filters=[]
    rows=plan['segments'];total=math.ceil(plan['samples']/1000)
    for i,row in enumerate(rows):
        raw=inside(directory,row['job']['video'])
        if digest(raw)!=row['job']['video_sha256']:raise ValueError('原片已变化，已停止合并。')
        args+=['-i',str(raw)]
        filters.append(f'[{i}:v]trim=end_frame={row["edit_frames"]},setpts=PTS-STARTPTS[v{i}]')
    args+=['-i',str(source)]
    filters+=[''.join(f'[v{i}]' for i in range(len(rows)))+f'concat=n={len(rows)}:v=1:a=0[cat]',
              '[cat]settb=1/24,setpts=N[v]',
              f'[{len(rows)}:a]apad,atrim=end_sample={total*1000},asetpts=PTS-STARTPTS[a]']
    try:
        controller.command(args+['-filter_complex',';'.join(filters),'-map','[v]','-map','[a]',
            '-c:v','libx264','-crf','17','-pix_fmt','yuv420p','-r','24','-fps_mode','cfr',
            '-c:a','aac','-b:a','192k','-movflags','+faststart',str(temporary)])
        if int(controller.probe_video(temporary)['nb_read_frames'])!=total:raise ValueError('首尾锚定成片帧数不符。')
        temporary.replace(target)
        saved=read_plan(root,plan['id']);saved['final_sha256']=digest(target);write_plan(root,saved)
        return str(target)
    finally:
        temporary.unlink(missing_ok=True)
