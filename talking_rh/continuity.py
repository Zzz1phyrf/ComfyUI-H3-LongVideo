"""Versioned reference anchors and audio-aligned native latent continuation."""
import math

LEGACY_VERSION = 'reference-carry-v1'
GUIDE_VERSION = 'reference-carry-v2'
VERSION = 'reference-carry-v3'
TOKENS = 17
CONTEXT_FRAMES = 56
LEGACY_ROLES = ('start', 'end', 'save', 'load', 'cut', 'replace', 'mask', 'prefix')
GUIDE_ROLES = (*LEGACY_ROLES, 'middle_0', 'middle_1')
ROLES = (*GUIDE_ROLES, 'guide_decode', 'guide_prefix')


def enabled(plan):
    return plan.get('config', {}).get('continuity', {}).get('version') in (LEGACY_VERSION, GUIDE_VERSION, VERSION)


def freeze(config):
    """Reserve graph IDs once, before the new project's fingerprint is frozen."""
    from .graph import TEMPLATE
    import json
    graphs = [json.loads(TEMPLATE.read_text(encoding='utf-8'))]
    graphs += [config[key]['prompt' if key == 'canvas' else 'graph']
               for key in ('canvas', 'rh_sampling') if config.get(key)]
    used = set().union(*(set(g) for g in graphs))
    ids = {};number = 18000
    for role in ROLES:
        while str(number) in used:number += 1
        ids[role] = str(number);used.add(str(number));number += 1
    return {'version': VERSION, 'ids': ids,'guide_interval_frames':204}


def clock(samples, cuts):
    """Keep cuts on the speech clock; align generation starts to H3's 17-frame phase."""
    total = math.ceil(samples / 1000)
    if total < 1 or cuts != sorted(set(cuts)) or any(type(f) is not int or not 0 < f < total for f in cuts):
        raise ValueError('切点必须递增且位于音频范围内。')
    bounds = [0, *cuts, total];rows = []
    for i, (start, end) in enumerate(zip(bounds, bounds[1:])):
        if end - start > 360:raise ValueError('每段须为4–15秒。')
        begin = max(0, 17 * math.ceil((start - CONTEXT_FRAMES) / 17)) if i else 0
        generated = max(175, 5 + 17 * math.ceil((end - begin + 17 - 5) / 17))
        prefix = 5 * ((begin - rows[-1]['window_start_frame']) // 17) if i else None
        rows.append(dict(index=i, start_sample=start * 1000, end_sample=min(samples, end * 1000),
                         edit_frames=end-start, generation_frames=generated, edit_start_frame=start-begin,
                         audio_start_sample=begin * 1000, window_start_frame=begin,
                         window_end_frame=begin+generated, actual_last_retained_frame=end-begin-1,
                         tail_token_start=None, continuity_prefix_index=prefix))
    return rows


def clock_for(plan, cuts):
    rows = clock(plan['samples'], cuts)
    settings = plan['config']['continuity']
    if settings['version'] == LEGACY_VERSION:return rows
    from itertools import combinations
    gap = settings['guide_interval_frames']
    candidates = plan.get('speech_segmentation', {}).get('boundaries', [])
    for row in rows:
        left = CONTEXT_FRAMES if row['index'] else 0
        end = row['generation_frames']-1
        if end-left <= gap:
            row['visual_anchors'] = [];continue
        pauses = {p['frame']-row['window_start_frame'] for p in candidates if p.get('kind')=='pause'
                  and left+8 <= p['frame']-row['window_start_frame'] < end}
        pool = sorted(pauses | {(left+end)//2})
        choices = []
        for count in (1,2):
            for points in combinations(pool,count):
                spans = [b-a for a,b in zip((left,*points),(*points,end))]
                if min(spans)<8 or max(spans)>gap:continue
                score = (sum(p not in pauses for p in points),count,
                         sum((s-(end-left)/(count+1))**2 for s in spans))
                choices.append((score,points))
        if not choices:raise ValueError('口播内部引导超出已验证范围，未提交生成。')
        points = min(choices)[1]
        row['visual_anchors'] = [{'frame':p,'kind':'pause' if p in pauses else 'interval_limit'} for p in points]
    return rows


def apply(graph, plan, row, uploaded):
    """Inject the same native nodes for local canvas and RH, without changing weights/audio."""
    from .project import latent_frames
    config = plan['config'];ids = config['continuity']['ids']
    version = config['continuity']['version']
    roles = LEGACY_ROLES if version==LEGACY_VERSION else GUIDE_ROLES if version==GUIDE_VERSION else ROLES
    if set(ids) != set(roles) or len(set(ids.values())) != len(roles) or any(k in graph for k in ids.values()):
        raise ValueError('口播连续性内部节点ID冲突，请新建项目。')
    sampler = config.get('canvas', {}).get('sampler_id', '270')
    settings = graph[sampler]['inputs']
    if graph[sampler]['class_type'] != 'SamplerCustom':raise ValueError('连续口播需要标准K采样器。')
    condition = str(settings['positive'][0]);inputs = graph[condition]['inputs']
    if graph[condition]['class_type'] != 'MiniMaxH3ReferenceToVideo':raise ValueError('缺少口播参考条件。')
    image = inputs['ref_images.ref_image_0'];vae = inputs['vae']
    positive = [condition, 0]
    if not row['index']:
        graph[ids['start']] = {'class_type':'MiniMaxH3AddGuide', 'inputs':{
            'positive':positive, 'latent':[condition,1], 'image':image, 'vae':vae, 'frame_idx':0}}
        positive = [ids['start'], 0]
    if version in (GUIDE_VERSION,VERSION):
        for i,anchor in enumerate(row['visual_anchors']):
            key = ids[f'middle_{i}']
            graph[key] = {'class_type':'MiniMaxH3AddGuide', 'inputs':{
                'positive':positive, 'latent':[condition,1], 'image':image, 'vae':vae, 'frame_idx':anchor['frame']}}
            positive = [key, 0]
    graph[ids['end']] = {'class_type':'MiniMaxH3AddGuide', 'inputs':{
        'positive':positive, 'latent':[condition,1], 'image':image, 'vae':vae, 'frame_idx':-1}}
    for key, node in graph.items():
        if key in (ids['start'], ids['end'], ids.get('middle_0'), ids.get('middle_1')):continue
        for field, value in node['inputs'].items():
            if value == [condition,0]:node['inputs'][field] = [ids['end'],0]
    # Save the sampler's real decoded video latent, before any post-processing.
    video = config.get('canvas', {}).get('video_id', '273')
    create = graph[str(graph[video]['inputs']['video'][0])]['inputs']
    decode = graph[str(create['images'][0])]['inputs']
    graph[ids['save']] = {'class_type':'SaveLatent', 'inputs':{'samples':decode['samples'], 'filename_prefix':''}}
    if row['index']:
        if not uploaded.get('previous_latent'):raise ValueError('上一段实际续接状态缺失，未提交生成。')
        combined = graph[str(settings['latent_image'][0])]['inputs']
        graph[ids['load']] = {'class_type':'LoadLatent','inputs':{'latent':uploaded['previous_latent']}}
        graph[ids['cut']] = {'class_type':'LatentCut','inputs':{'samples':[ids['load'],0], 'dim':'t',
                               'index':row['continuity_prefix_index'], 'amount':TOKENS}}
        graph[ids['replace']] = {'class_type':'ReplaceVideoLatentFrames','inputs':{
            'destination':combined['video_latent'], 'source':[ids['cut'],0], 'index':0}}
        mask = {'video_fps':8., 'video_start_time':0., 'audio_start_time':0., 'audio_end_time':0., 'max_length':'partial'}
        graph[ids['mask']] = {'class_type':'LTXVAudioVideoMask','inputs':{**mask,
            'video_latent':[ids['replace'],0], 'video_end_time':(8*(latent_frames(row['generation_frames'])-1)+1)/8,
            'existing_mask_mode':'add'}}
        graph[ids['prefix']] = {'class_type':'LTXVAudioVideoMask','inputs':{**mask,
            'video_latent':[ids['mask'],0], 'video_end_time':(8*(TOKENS-1)+1)/8, 'existing_mask_mode':'subtract'}}
        combined['video_latent'] = [ids['prefix'],0]
        if version == VERSION:
            graph[ids['guide_decode']]={'class_type':'VAEDecode','inputs':{'samples':[ids['cut'],0],'vae':vae}}
            graph[ids['guide_prefix']]={'class_type':'MiniMaxH3AddGuide','inputs':{
                'positive':[condition,0],'latent':[condition,1],'vae':vae,
                'image':[ids['guide_decode'],0],'frame_idx':0}}
            first=ids['middle_0'] if row['visual_anchors'] else ids['end']
            graph[first]['inputs']['positive']=[ids['guide_prefix'],0]
    return graph


def validate_prefix(previous, current, index):
    """Verify raw saved outputs; float32 round trips can differ by less than 1e-6."""
    import numpy as np
    from safetensors import safe_open
    with safe_open(str(previous), framework='np') as source:
        expected = source.get_slice('latent_tensor')[:, :, index:index+TOKENS]
    with safe_open(str(current), framework='np') as source:
        actual = source.get_slice('latent_tensor')[:, :, :TOKENS]
    if (expected.shape != actual.shape or expected.shape[2] != TOKENS
            or not np.isfinite(expected).all() or not np.isfinite(actual).all()
            or not np.allclose(expected, actual, rtol=1e-6, atol=1e-6)):
        raise ValueError('实际续接前缀没有保留上一段状态，已保留原任务并停止后续生成。')
    delta = np.abs(expected-actual)
    return {'numerically_preserved':True, 'bit_exact':bool(np.array_equal(expected,actual)),
            'max_abs_error':float(delta.max()), 'mean_abs_error':float(delta.mean()),
            'rtol':1e-6, 'atol':1e-6, 'prefix_tokens':TOKENS, 'previous_tail_start':index}
