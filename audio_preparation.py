"""Prepare one aligned vocal reference per project, shared by MV and speech."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import soundfile as sf

from .core import audio_file

DIRECT='直接使用'
EXTRACT='提取人声'
MODES=(DIRECT,EXTRACT)


def validate_mode(mode):
    if mode not in MODES:raise ValueError('音频处理请选择“直接使用”或“提取人声”。')
    return mode


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def align_reference(wave,rate,target_rate,samples):
    wave=np.asarray(wave,dtype=np.float32)
    if (wave.ndim!=2 or not len(wave) or wave.shape[1] not in (1,2)
            or not np.isfinite(wave).all() or type(rate) is not int or rate<=0):
        raise ValueError('参考人声必须是有效的单声道或双声道音频。')
    if rate!=target_rate:
        from scipy.signal import resample_poly
        divisor=math.gcd(rate,target_rate)
        wave=resample_poly(wave,target_rate//divisor,rate//divisor,axis=0).astype(np.float32)
    if abs(len(wave)-samples)>max(2,math.ceil(target_rate*.002)):
        raise ValueError('参考人声与原音频时长不一致；请使用同版本、未裁切的音频。')
    if len(wave)<samples:wave=np.pad(wave,((0,samples-len(wave)),(0,0)))
    return np.ascontiguousarray(wave[:samples],dtype=np.float32)


def prepare(wave,rate,mode):
    validate_mode(mode)
    if mode==DIRECT:return align_reference(wave,rate,rate,len(wave))
    # Reuse the existing verified downloader, model cache and bounded separator.
    from .nodes import separate
    return align_reference(separate(wave,rate),rate,rate,len(wave))


def verify_reference(directory,record):
    if (not isinstance(record,dict) or record.get('version')!=1 or record.get('file')!='vocals.wav'
            or record.get('mode') not in MODES):
        raise ValueError('项目人声准备记录无效，请保留原项目并重新准备。')
    for name,key in [('source.wav','source_sha256'),('vocals.wav','sha256')]:
        path=audio_file(directory,name)
        if not path.is_file() or digest(path)!=record.get(key):
            raise ValueError('项目原声或参考人声缓存已变化或损坏；没有自动重新分离。')


def cache_reference(directory,mode,external=None):
    validate_mode(mode)
    manifest=audio_file(directory,'preparation.json')
    if manifest.exists():
        try:record=json.loads(manifest.read_text(encoding='utf-8'))
        except ValueError:raise ValueError('项目人声准备记录损坏；没有自动重新分离。') from None
        verify_reference(directory,record)
        if record['mode']!=mode:raise ValueError('音频处理已改变，请新建项目。')
        return record
    wave,rate=sf.read(audio_file(directory,'source.wav'),dtype='float32',always_2d=True)
    reference=(align_reference(external[0],external[1],rate,len(wave)) if external is not None
               else prepare(wave,rate,mode))
    target=audio_file(directory,'vocals.wav');temporary=target.with_suffix('.tmp.wav')
    try:
        sf.write(temporary,reference,rate,subtype='FLOAT');temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
    record={'version':1,'mode':mode,'file':'vocals.wav','sample_rate':rate,'samples':len(wave),
            'source_sha256':digest(audio_file(directory,'source.wav')),'sha256':digest(target)}
    temporary=manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(manifest)
    return record
