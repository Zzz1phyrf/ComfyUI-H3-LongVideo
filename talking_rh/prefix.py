"""Checked standard-K prefix normalization; raw returned files stay immutable."""
import logging
from pathlib import Path
import uuid

import numpy as np

from .project import PREFIX_TOKENS

RTOL=ATOL=1e-6


def canonicalize(previous,current,target,tail):
    from safetensors import safe_open
    from safetensors.numpy import save
    previous,current,target=map(Path,(previous,current,target))
    if target.resolve() in (previous.resolve(),current.resolve()):
        raise ValueError('标准K规范续接文件不能覆盖原始返回或上一段文件。')
    with safe_open(str(previous),framework='np') as source:
        source_shape=source.get_slice('latent_tensor').get_shape()
        if len(source_shape)!=5 or tail<0 or tail+PREFIX_TOKENS>source_shape[2]:
            raise ValueError('标准K续接前缀的来源或范围无效。')
        expected=source.get_slice('latent_tensor')[:,:,tail:tail+PREFIX_TOKENS]
    with safe_open(str(current),framework='np') as source:
        arrays={key:source.get_tensor(key) for key in source.keys()}
        metadata=source.metadata()
    samples=arrays['latent_tensor']
    if samples.ndim!=5 or samples.shape[2]<PREFIX_TOKENS or expected.shape!=samples[:,:,:PREFIX_TOKENS].shape:
        raise ValueError('标准K续接前缀的形状无效。')
    actual=samples[:,:,:PREFIX_TOKENS]
    if (not np.isfinite(expected).all() or not np.isfinite(samples).all()
            or not np.allclose(actual,expected,rtol=RTOL,atol=ATOL)):
        raise ValueError('标准K采样改变了冻结前缀，超过浮点往返误差；已停止，原始返回未覆盖。')
    exact=bool(np.array_equal(actual,expected))
    proof={'raw_bit_exact':exact,'max_absolute_error':float(np.max(np.abs(actual-expected))),
           'restored':not exact,'previous_tail_start':tail,'prefix_tokens':PREFIX_TOKENS}
    if exact:return current,proof
    restored=samples.copy();restored[:,:,:PREFIX_TOKENS]=expected
    arrays['latent_tensor']=restored
    if target.exists():
        with safe_open(str(target),framework='np') as source:
            valid=source.metadata()==metadata and set(source.keys())==set(arrays)
            valid=valid and all(np.array_equal(source.get_tensor(key),value) for key,value in arrays.items())
        if not valid:raise ValueError('已有标准K续接缓存已变化，已停止；没有覆盖原文件或重复生成。')
        return target,proof
    partial=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.part')
    created=False
    try:
        payload=save(arrays,metadata=metadata)
        with partial.open('xb') as stream:
            created=True
            stream.write(payload)
        partial.replace(target)
    finally:
        if created:
            try:partial.unlink(missing_ok=True)
            except OSError as exc:logging.warning('[H3LongVideo] 无法清理本次续接残片（%s）：%s',type(exc).__name__,partial)
    return target,proof
