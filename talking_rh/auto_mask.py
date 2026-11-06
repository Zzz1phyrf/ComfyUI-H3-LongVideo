"""CPU person activity masks, cached from the original reference only."""
import hashlib
import json
import logging
from pathlib import Path
import threading
import uuid

import numpy as np
from PIL import Image, ImageFilter

from .project import digest

MODEL_NAME = 'selfie_multiclass_256x256.tflite'
ALGORITHM = 'selfie-multiclass-activity-v1'
_lock = threading.Lock()


def model_path():
    import folder_paths
    roots = [Path(folder_paths.models_dir) / 'mediapipe']
    if 'mediapipe' in folder_paths.folder_names_and_paths:
        roots = [Path(p) for p in folder_paths.get_folder_paths('mediapipe')] + roots
    for root in roots:
        path = root / MODEL_NAME
        if path.is_file():
            return path
    raise ValueError('缺少自动人物分割模型。请将官方 ' + MODEL_NAME + ' 放入 ComfyUI/models/mediapipe，详见口播使用说明。')


def segment(image, model):
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
    except ImportError as exc:
        raise ValueError('自动人物活动范围需要mediapipe，请用ComfyUI的Python安装本插件requirements.txt。') from exc
    options = vision.ImageSegmenterOptions(
        base_options=BaseOptions(model_asset_buffer=Path(model).read_bytes(), delegate=BaseOptions.Delegate.CPU),
        running_mode=vision.RunningMode.IMAGE, output_category_mask=True, output_confidence_masks=False)
    # Buffer loading also works when the model directory contains non-ASCII text.
    with vision.ImageSegmenter.create_from_options(options) as engine:
        result = engine.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(image.convert('RGB'))))
        return result.category_mask.numpy_view().copy().reshape(image.height, image.width)


def activity_mask(labels):
    labels = np.asarray(labels)
    if labels.ndim != 2 or not np.isfinite(labels).all() or labels.min() < 0 or labels.max() > 5:
        raise ValueError('自动人物分割结果无效，没有创建生成项目。')
    person = labels > 0  # hair, body skin, face, clothes and accessories
    coverage = float(person.mean())
    if coverage < .03 or coverage > .90 or not np.any(labels == 3):
        raise ValueError('未识别出带背景的清晰口播人物。请换一张面部清楚、人物完整的主参考图。')
    side = min(labels.shape)
    radius = max(2, round(side * .045))
    feather = max(1, side * .006)
    binary = Image.fromarray(person.astype('uint8') * 255)
    expanded = binary.filter(ImageFilter.MaxFilter(2 * radius + 1))
    softened = np.asarray(expanded.filter(ImageFilter.GaussianBlur(feather)), dtype='float32') / 255
    # Keep all detected person pixels fully generatable, including near the edge.
    softened[person] = 1
    softened[softened >= .99] = 1
    softened[softened <= .001] = 0
    if softened.min() != 0 or softened.max() != 1:
        raise ValueError('人物活动范围没有留下可固定的背景，请换一张人物周围有余量的主参考图。')
    return softened


def automatic_mask(image, cache_root):
    image = image.convert('RGB')
    model = model_path()
    identity = {'algorithm': ALGORITHM, 'model_sha256': digest(model),
                'reference_sha256': hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = Path(cache_root) / key
    path, metadata = directory / 'mask.png', directory / 'mask.json'
    with _lock:
        if path.is_file() and metadata.is_file():
            info = json.loads(metadata.read_text(encoding='utf-8'))
            if any(info.get(k) != v for k, v in identity.items()) or info.get('mask_sha256') != digest(path):
                raise ValueError('自动人物活动范围缓存已变化。请保留旧项目，移除该缓存后新建项目。')
            with Image.open(path) as saved:
                if saved.size != image.size:
                    raise ValueError('自动人物活动范围缓存尺寸不匹配。')
                value = np.asarray(saved.convert('L'), dtype='float32') / 255
            return value, info
        value = activity_mask(segment(image, model))
        if value.shape != (image.height, image.width):
            raise ValueError('自动人物活动范围尺寸与主参考图不一致。')
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / (uuid.uuid4().hex + '.png')
        Image.fromarray(np.rint(value * 255).astype('uint8')).save(temporary)
        temporary.replace(path)
        info = dict(identity, mode='automatic', cache_key=key, mask_sha256=digest(path))
        temporary = directory / (uuid.uuid4().hex + '.json')
        temporary.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(metadata)
        logging.info('[H3 Talking] 自动人物活动范围已生成并缓存，后续片段共用原参考图范围。')
        return np.asarray(Image.open(path), dtype='float32') / 255, info
