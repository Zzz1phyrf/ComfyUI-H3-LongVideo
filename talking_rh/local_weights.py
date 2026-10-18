"""Optional owned-buffer CLIP read, scoped to the talking-head local graph."""
import json
import math
from pathlib import Path
import struct


def read_tensors(path):
    import torch
    import comfy.utils
    size=Path(path).stat().st_size
    with Path(path).open('rb') as stream:
        first=stream.read(8)
        if len(first)!=8:raise ValueError('CLIP文件头不完整。')
        length=struct.unpack('<Q',first)[0]
        if not 0<length<=100*1024*1024 or length+8>size:raise ValueError('CLIP文件头长度无效。')
        header=json.loads(stream.read(length).decode('utf-8'))
        base=length+8;tensors={}
        for name,item in header.items():
            if name=='__metadata__':continue
            dtype=comfy.utils._TYPES[item['dtype']];shape=item['shape'];start,end=item['data_offsets']
            if not 0<=start<=end<=size-base or math.prod(shape)*dtype.itemsize!=end-start:
                raise ValueError('CLIP张量字节区间无效：'+name)
            if start==end:value=torch.empty(shape,dtype=dtype)
            else:
                buffer=bytearray(end-start);view=memoryview(buffer);copied=0
                stream.seek(base+start)
                while copied<len(buffer):
                    count=stream.readinto(view[copied:])
                    if not count:raise ValueError('CLIP张量文件读取不完整：'+name)
                    copied+=count
                value=torch.frombuffer(buffer,dtype=dtype).reshape(shape)
                value.untyped_storage()._h3talk_owned_buffer=buffer
            tensors[name]=value
    return tensors,header.get('__metadata__',{})


def load_clip(clip_name):
    import comfy.sd
    import comfy.utils
    import folder_paths
    path=folder_paths.get_full_path_or_raise('text_encoders',clip_name)
    tensors,metadata=read_tensors(path)
    tensors,_=comfy.utils.convert_old_quants(tensors,model_prefix='',metadata=metadata)
    clip=comfy.sd.load_text_encoder_state_dicts([tensors],embedding_directory=folder_paths.get_folder_paths('embeddings'),
                                              clip_type=comfy.sd.CLIPType.MINIMAX,model_options={})
    clip.patcher.cached_patcher_init=(reload_patcher,(clip_name,))
    return clip


def reload_patcher(clip_name):
    return load_clip(clip_name).patcher


class OwnedClipLoader:
    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths
        return {'required':{'clip_name':(folder_paths.get_filename_list('text_encoders'),)}}
    RETURN_TYPES=('CLIP',)
    FUNCTION='load'
    CATEGORY='像素幻想/H3 口播/内部'
    DEPRECATED=True

    def load(self,clip_name):return (load_clip(clip_name),)
