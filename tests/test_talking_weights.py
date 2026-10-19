import importlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

import torch
from safetensors.torch import save_file

from test_talking_rh import graph,project

weights=importlib.import_module('h3talk_test.talking_rh.local_weights')


class WeightReadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'clip.safetensors'
        self.values={'half':torch.arange(12,dtype=torch.float16).reshape(3,4),
                     'bf16':torch.randn(2,5,dtype=torch.bfloat16),'bytes':torch.arange(-4,4,dtype=torch.int8),
                     'empty':torch.empty(0,3),'fp8':torch.arange(8,dtype=torch.float32).to(torch.float8_e4m3fn)}
        save_file(self.values,str(self.path),metadata={'quantization':'保留原元数据'})
        self.utils=types.ModuleType('comfy.utils')
        self.utils._TYPES={'F16':torch.float16,'BF16':torch.bfloat16,'I8':torch.int8,'F32':torch.float32,'F8_E4M3':torch.float8_e4m3fn}
        self.utils.load_torch_file=Mock(side_effect=AssertionError('Global loader must remain untouched'))
        self.comfy=types.ModuleType('comfy');self.comfy.utils=self.utils
        self.modules={'comfy':self.comfy,'comfy.utils':self.utils}

    def test_owned_buffers_preserve_tensor_bytes_dtype_shape_metadata_and_lifetime(self):
        with patch.dict('sys.modules',self.modules):result,metadata=weights.read_tensors(self.path)
        self.assertEqual(metadata,{'quantization':'保留原元数据'})
        self.assertEqual(set(result),set(self.values))
        for key,original in self.values.items():
            value=result[key]
            self.assertEqual(value.dtype,original.dtype);self.assertEqual(value.shape,original.shape)
            self.assertTrue(torch.equal(value.view(torch.uint8),original.view(torch.uint8)))
            if value.numel():self.assertTrue(hasattr(value.untyped_storage(),'_h3talk_owned_buffer'))
        self.utils.load_torch_file.assert_not_called()

    def test_incomplete_tensor_file_is_rejected(self):
        self.path.write_bytes(self.path.read_bytes()[:-4])
        with patch.dict('sys.modules',self.modules),self.assertRaises(ValueError):weights.read_tensors(self.path)

    def test_clip_construction_and_cache_reload_are_scoped_without_global_patch(self):
        sd=types.ModuleType('comfy.sd');self.comfy.sd=sd
        sd.CLIPType=types.SimpleNamespace(MINIMAX='minimax')
        sd.load_text_encoder_state_dicts=Mock(side_effect=lambda *_,**__:types.SimpleNamespace(patcher=types.SimpleNamespace()))
        self.utils.convert_old_quants=Mock(side_effect=lambda data,**kw:(data,kw['metadata']))
        folders=types.SimpleNamespace(get_full_path_or_raise=lambda *_:str(self.path),get_folder_paths=lambda _:['embeddings'])
        with patch.dict('sys.modules',{**self.modules,'comfy.sd':sd,'folder_paths':folders}):
            clip=weights.load_clip('clip.safetensors')
            fn,args=clip.patcher.cached_patcher_init
            self.assertIs(fn,weights.reload_patcher)
            self.assertEqual(args,('clip.safetensors',))
            self.assertIsNotNone(fn(*args,disable_dynamic=True))
            self.assertTrue(sd.load_text_encoder_state_dicts.call_args.kwargs['disable_dynamic'])
        self.assertEqual(sd.load_text_encoder_state_dicts.call_count,2)
        self.utils.load_torch_file.assert_not_called()

    def test_distinct_queue_node_instances_reuse_one_owned_clip(self):
        folders=types.SimpleNamespace(get_full_path_or_raise=lambda *_:str(self.path))
        factory=Mock(side_effect=lambda _:object())
        with patch.dict('sys.modules',{'folder_paths':folders}),patch.object(weights,'load_clip',factory):
            first=weights.OwnedClipLoader().load('clip.safetensors')[0]
            second=weights.OwnedClipLoader().load('clip.safetensors')[0]
        self.assertIs(first,second)
        factory.assert_called_once()

    def test_replaced_clip_file_invalidates_owned_instance(self):
        import os
        folders=types.SimpleNamespace(get_full_path_or_raise=lambda *_:str(self.path))
        factory=Mock(side_effect=lambda _:object())
        with patch.dict('sys.modules',{'folder_paths':folders}),patch.object(weights,'load_clip',factory):
            first=weights.OwnedClipLoader().load('clip.safetensors')[0]
            stat=self.path.stat();os.utime(self.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000_000))
            second=weights.OwnedClipLoader().load('clip.safetensors')[0]
        self.assertIsNot(first,second)
        self.assertEqual(factory.call_count,2)

    def test_memory_read_graph_keeps_avatar_and_audio_zero_mask(self):
        config={'backend':'local','megapixels':1.0,'seed':999,'models':{
            **{key:'model' for key in ('local_model','local_clip','local_video_vae','local_audio_vae','local_upscaler')},
            'local_clip_read':'内存'}}
        result=graph.build({'id':'a'*32,'config':config},project.clock(277000,[141])[0],
                           {'audio':'a','references':['r'],'mask':'m','attempt':'t'})
        self.assertEqual(result['128'],{'class_type':'H3LVTalkingClipLoader','inputs':{'clip_name':'model'}})
        self.assertEqual(result['249']['inputs']['value'],0)
        self.assertEqual(result['270']['class_type'],'SelfLiftAvatarH3Sampler')


if __name__=='__main__':unittest.main()
