"""Sampler seed ownership, queued values and old project compatibility."""
import tempfile
import types
import unittest
from unittest.mock import patch
import torch

from test_talking_public import public_canvas,node,project,canvas
from test_talking_overlap import fixture,graph
from test_talking_overlap import config
import importlib
from pathlib import Path


class SamplerSeedTests(unittest.TestCase):
    def test_public_seed_ports_removed(self):
        schema=node.TalkingAvatar.INPUT_TYPES()
        self.assertNotIn('seed',schema['required'])
        self.assertNotIn('seed',node.TalkingAvatar.RETURN_NAMES)

    def inputs(self):
        return dict(audio={'waveform':torch.zeros(1,1,430000),'sample_rate':24000},
            reference_image=torch.zeros(1,64,32,3),width=768,height=1376,unique_id='5')

    def test_k_seed_is_recorded_and_reaches_actual_segment_graph(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:tmp)),patch.object(canvas,'blocked',side_effect=lambda p:(p['id'],)+(None,)*27):
            raw=public_canvas();raw['270']['inputs']['noise_seed']=78123
            pid=node.TalkingAvatar().prepare(**self.inputs(),prompt=raw)['result'][0]
            plan=project.read_plan(node.root(),pid)
            self.assertEqual(plan['config']['seed'],78123)
            queued=graph.build(plan,plan['segments'][0],{'attempt':'a'*12})
            self.assertEqual(queued['270']['inputs']['noise_seed'],78123)

    def test_same_seed_unwiring_can_resume_legacy_project_without_state_change(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:tmp)),patch.object(canvas,'blocked',side_effect=lambda p:(p['id'],)+(None,)*27):
            kwargs=self.inputs()
            pid=node.TalkingRH().prepare(**kwargs,prompt=fixture(True),seed=9260,backend='本地',
                execution_mode='画布',continuation_mode='独立窗口（自然动作）')['result'][0]
            path=project.project_path(node.root(),pid)/'state/segments.json';before=path.read_bytes()
            raw=public_canvas();raw['273']['_meta']={'title':'视频输出'}
            result=node.TalkingAvatar().prepare(**kwargs,prompt=raw,project_id=pid)
            self.assertEqual(result['result'][0],pid)
            self.assertEqual(path.read_bytes(),before)
            raw['270']['inputs']['noise_seed']=9261
            with self.assertRaisesRegex(ValueError,'新建项目'):
                node.TalkingAvatar().prepare(**kwargs,prompt=raw,project_id=pid)
            self.assertEqual(path.read_bytes(),before)

    def test_dynamic_or_invalid_sampler_seed_is_rejected(self):
        for seed in (-1,True,1.2,['123',0],2**64):
            raw=fixture(True);raw['270']['inputs']['noise_seed']=seed
            raw['123']={'class_type':'PrimitiveInt','inputs':{'value':123}}
            with self.subTest(seed=seed),self.assertRaisesRegex(ValueError,'固定整数种子'):
                canvas.capture(raw,'5',independent=True)

    def test_canvas_preview_follows_segment_then_final_and_ignores_stale_final(self):
        routes=importlib.import_module('h3talk_test.talking_rh.routes')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'H3TalkingRH/projects';root.mkdir(parents=True)
            import numpy as np
            from PIL import Image
            plan=project.prepare(root,np.zeros((24000,1),dtype='float32'),24000,
                [Image.new('RGB',(32,64))],None,config(True),'input')
            raw=project.project_path(root,plan['id'])/'takes/raw.mp4';raw.touch()
            plan['segments'][0]['job']={'status':'completed','video':str(raw)}
            final=Path(tmp)/'H3TalkingRH/final_videos/final.mp4';final.parent.mkdir();final.touch()
            with patch.object(routes,'root',return_value=root),patch.object(routes.runner,'busy',return_value=False):
                value=routes.public(plan)
                self.assertEqual(value['canvas_preview']['filename'],'raw.mp4')
                self.assertEqual(value['canvas_video_id'],'273')
                plan.update(final_video=str(final),final_stale=False)
                value=routes.public(plan)
                self.assertEqual(value['canvas_preview']['filename'],'final.mp4')
                self.assertEqual(value['canvas_preview_kind'],'final')
                plan['final_stale']=True
                self.assertEqual(routes.public(plan)['canvas_preview']['filename'],'raw.mp4')
