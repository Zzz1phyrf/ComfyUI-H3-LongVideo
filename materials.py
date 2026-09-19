"""Resolved, ordered segment materials shared by images and prompt expansion."""
import hashlib
import json
from pathlib import Path


def effective(plan, row, directory=None, require=False):
    from .core import normalize_reference_names
    source = row.get('reference_source', 'custom' if row.get('refs') else 'default')
    if source not in ('default', 'custom'):
        raise ValueError('参考图来源无效。')
    names = plan.get('default_refs', []) if source == 'default' else row.get('refs', [])
    note = plan.get('default_material_note', '') if source == 'default' else row.get('material_note', '')
    names = normalize_reference_names(names, directory)
    kind = row.get('visual_type', 'performance')
    if kind not in ('performance', 'atmosphere', 'environment'):
        raise ValueError('画面类型无效。')
    if require and not names:
        raise ValueError('本段没有有效参考图，请上传默认图或本段自定义图。')
    if require and not str(note).strip():
        raise ValueError('请填写本段参考图用途说明，图号需与图片顺序一致。')
    return {'refs': names, 'material_note': str(note).strip(), 'visual_type': kind}


def packet(plan, row, directory):
    from .core import reference_directory, inside
    value = effective(plan, row, directory, require=True)
    paths = [inside(reference_directory(directory), reference_directory(directory)/name) for name in value['refs']]
    return {**value, 'project_id': plan['id'], 'segment_index': row['index'],
            'mode': plan['mode'], 'brief': row['prompt'], 'duration': row['duration'],
            'generation_frames': row['generation_frames'],
            'generation_seconds': row['generation_frames']/24,
            'audio_role': row.get('audio_role', 'uncertain'),
            'audio_section': row.get('audio_section', ''),
            'audio_role_reason': row.get('audio_role_reason', ''),
            'paths': [str(p) for p in paths],
            'hashes': [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths],
            'cache_dir': str(Path(directory)/'cache'/'expansion'),
            'expanded_prompt': row.get('expanded_prompt'), 'expanded_key': row.get('expanded_key')}


def images(material):
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    result = []
    for path in material['paths']:
        with Image.open(path) as im:
            value = ImageOps.exif_transpose(im).convert('RGB')
            result.append(torch.from_numpy(np.asarray(value).astype(np.float32)/255).unsqueeze(0))
    return tuple(result + [None]*(6-len(result)))


def stamp(plan, row):
    return hashlib.sha256(json.dumps(effective(plan, row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
