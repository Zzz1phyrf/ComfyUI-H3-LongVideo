"""CPU speech-pause candidates and duration-constrained planning; no ASR/model I/O."""
from bisect import bisect_left,bisect_right
import math
import numpy as np

ALGORITHM='speech-pauses-v1'


def pauses(wave):
    samples=np.asarray(wave,dtype=np.float64)
    if samples.ndim==1:samples=samples[:,None]
    if not len(samples) or not np.isfinite(samples).all():raise ValueError('口播音频为空或含无效数值。')
    power=np.mean(samples*samples,axis=1);starts=np.arange(0,len(samples),480)
    rms=np.sqrt(np.add.reduceat(power,starts)/np.minimum(480,len(samples)-starts))
    smooth=np.convolve(np.pad(rms,(1,1),mode='edge'),np.ones(3)/3,mode='valid')
    reference=float(np.percentile(smooth,80))
    if reference<=1e-12:reference=float(smooth.max())
    if reference<=1e-12:return []
    floor=float(np.percentile(smooth,10))
    threshold=min(reference*.125,max(reference*.0125,floor*3))
    quiet=smooth<=threshold
    changes=np.diff(np.r_[False,quiet,False].astype(np.int8))
    found=[]
    for a,b in zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1)):
        begin=a*.02;end=min(len(samples)/24000,b*.02)
        if end-begin<.18:continue
        low=math.ceil((begin+.04)*24);high=math.floor((end-.04)*24)
        if low<=high:found.append({'start':begin,'end':end,'low':low,'high':high,'frame':round((low+high)/2)})
    return found


def plan(wave):
    total=math.ceil(len(wave)/1000);quiet=pauses(wave)
    if total<1:raise ValueError('口播音频不能为空。')
    if total<96:return {'algorithm':ALGORITHM,'cuts':[],'boundaries':[],'pause_count':len(quiet),'fallback_count':0}
    count=max(math.ceil(total/360),min(total//96,round(total/144)))
    candidates={0,total,*range(96,total-95,24),*[round(i*total/count) for i in range(1,count)]}
    candidates.update(p['frame'] for p in quiet if 96<=p['frame']<=total-96)
    nodes=sorted(candidates);info={}
    for frame in nodes[1:-1]:
        pause=next((p for p in quiet if p['low']<=frame<=p['high']),None)
        info[frame]=pause
    best={0:(0,0.)};previous={}
    for end in nodes[1:]:
        pause=info.get(end);forced=int(end!=total and pause is None)
        center_penalty=0 if pause is None else abs(end-pause['frame'])/max(1,pause['high']-pause['low'])*.04
        for start in nodes[bisect_left(nodes,end-360):bisect_right(nodes,end-96)]:
            if start not in best:continue
            cost=(best[start][0]+forced,best[start][1]+((end-start-144)/144)**2+.08+center_penalty)
            if end not in best or cost<best[end]:best[end]=cost;previous[end]=start
    if total not in best:raise ValueError('无法在4–15秒范围内覆盖完整口播，请检查音频。')
    cuts=[];cursor=total
    while previous[cursor]:cursor=previous[cursor];cuts.append(cursor)
    cuts.reverse();boundaries=[]
    for frame in cuts:
        pause=info[frame]
        boundaries.append({'frame':frame,'kind':'pause' if pause else 'duration_limit',
            'low':pause['low'] if pause else frame,'high':pause['high'] if pause else frame})
    return {'algorithm':ALGORITHM,'cuts':cuts,'boundaries':boundaries,'pause_count':len(quiet),
            'fallback_count':sum(row['kind']=='duration_limit' for row in boundaries)}
