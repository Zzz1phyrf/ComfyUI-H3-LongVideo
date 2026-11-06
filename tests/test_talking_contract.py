"""Preflight checks for the actual Ref2VA, audio and continuation clock paths."""
import copy
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import test_talking_rh as rh
from test_talking_rh import project
from test_talking_rh import runner

canvas=importlib.import_module('h3talk_test.talking_rh.canvas')
sampling=importlib.import_module('h3talk_test.talking_rh.sampling')
FIXTURES=Path(__file__).parent/'fixtures'


class Ref2VAContractTests(unittest.TestCase):
    def load(self,name='talking_ref2va_canvas_api.json'):
        return json.loads((FIXTURES/name).read_text(encoding='utf-8'))

    def verify(self,graph):canvas.verify_ref2va(canvas.capture(graph,'5'))

    def test_native_condition_clock_and_video_fps_cannot_bypass_project_timing(self):
        for key,field,value in [('9400','length',175),('9400','width',768),('9400','height',1376),
                                ('272','fps',30),('9421','fps',30)]:
            with self.subTest(node=key,field=field):
                graph=self.load();graph[key]['inputs'][field]=value
                with self.assertRaises(ValueError):self.verify(graph)

    def test_continuation_must_use_actual_retained_tail_and_the_twelve_token_mask(self):
        changes=[('9111','amount',11),('9111','index',35),('9111','dim','y'),
                 ('9112','index',1),('9410','switch',False),
                 ('9113','video_end_time',10.0),('9113','existing_mask_mode','overwrite'),
                 ('9113','max_length','truncate')]
        for key,field,value in changes:
            with self.subTest(node=key,field=field):
                graph=self.load();graph[key]['inputs'][field]=value
                with self.assertRaises(ValueError):self.verify(graph)

    def test_video_paths_cannot_use_the_known_audio_vae(self):
        for key in ('9400','270','122'):
            graph=self.load();graph[key]['inputs']['vae']=['9603',0]
            with self.subTest(node=key),self.assertRaises(ValueError):self.verify(graph)

    def test_a_separate_video_decoder_vae_is_not_silently_replaced(self):
        graph=self.load()
        graph['other_video_vae']={'class_type':'VAELoader','inputs':{'vae_name':'separate-video-decoder.safetensors'}}
        graph['122']['inputs']['vae']=['other_video_vae',0]
        self.verify(graph)
        self.assertEqual(graph['122']['inputs']['vae'],['other_video_vae',0])

    def test_equivalent_mask_units_freeze_the_same_twelve_tokens(self):
        graph=self.load();graph['9113']['inputs'].update(video_fps=16.,video_end_time=90/16)
        self.verify(graph)

    def test_rh_ref2va_audio_vae_mismatch_is_rejected_instead_of_silently_rebound(self):
        for value in (None,['214',0]):
            graph=self.load('talking_remote_config.json')
            graph['243']['inputs']['audio_vae']=value;before=copy.deepcopy(graph)
            with self.subTest(vae=value),self.assertRaises(ValueError):sampling.freeze(graph)
            self.assertEqual(graph,before)

    def test_valid_selflift_and_standard_k_canvases_keep_their_parameters(self):
        for name in ('talking_ref2va_canvas_api.json','talking_ref2va_k_canvas_api.json'):
            graph=self.load(name);before=copy.deepcopy(graph)
            self.verify(graph);self.assertEqual(graph,before)


class PersistedClockTests(unittest.TestCase):
    setUp=rh.LifecycleTests.setUp
    approve=rh.LifecycleTests.approve

    def test_approved_clock_does_not_allow_execution_rows_to_shift_audio_or_tail(self):
        original=project.read_plan(self.root,self.pid)
        original['config']['talking_profile']='ref2va-v1'
        for field in ('audio_start_sample','edit_start_frame','generation_frames','tail_token_start'):
            plan=copy.deepcopy(original)
            index=0 if field=='tail_token_start' else 1
            plan['segments'][index][field]+=1
            project.write_plan(self.root,plan)
            with self.subTest(field=field),self.assertRaises(ValueError):project.verify_assets(self.root,plan)

    def test_corrupt_crop_clock_blocks_preview_and_merge_before_media_processing(self):
        plan=project.read_plan(self.root,self.pid);plan['config']['talking_profile']='ref2va-v1'
        plan['approved_fingerprint']=project.fingerprint(plan)
        plan['segments'][1]['edit_start_frame']+=1
        project.write_plan(self.root,plan)
        with patch.object(runner.controller,'assemble',side_effect=AssertionError('Invalid clock must stop before FFmpeg')):
            with self.assertRaisesRegex(ValueError,'时钟'):canvas.assemble_project(self.root,self.pid)
            with self.assertRaisesRegex(ValueError,'时钟'):canvas.segment_preview(self.root,self.pid,1)

    def test_completed_take_cannot_override_the_reviewed_crop_clock(self):
        plan=project.read_plan(self.root,self.pid);plan['config']['talking_profile']='ref2va-v1'
        plan['segments'][1]['job']={'status':'completed','edit_start_frame':0}
        with self.assertRaisesRegex(ValueError,'裁切'):project.verify_clock(plan)


class AudioWindowTests(unittest.TestCase):
    setUp=rh.LifecycleTests.setUp
    approve=rh.LifecycleTests.approve

    def test_conditioning_preserves_sample_tails_at_rounding_and_half_frame_boundaries(self):
        for samples in (277001,277499,294500,294501):
            with self.subTest(samples=samples):
                wave=np.arange(samples,dtype='float32')[:,None]
                row=project.clock(samples,[141])[-1]
                actual=project.condition_audio(wave,row)
                wanted=wave[row['audio_start_sample']:]
                self.assertGreaterEqual(len(actual),len(wanted))
                self.assertTrue(np.array_equal(actual[:len(wanted)],wanted))

    def test_managed_and_canvas_inputs_include_the_fractional_last_audio_frame(self):
        import torch
        node=importlib.import_module('h3talk_test.talking_rh')
        wave=np.arange(277013,dtype='float32')/1000000
        source=np.column_stack([wave,wave*.5+.05]).astype('float32')
        sf.write(project.audio_file(self.dir,'source.wav'),source,24000,subtype='FLOAT')
        p=project.read_plan(self.root,self.pid)
        p.update(samples=len(source),duration=len(source)/24000,
                 source_sha256=project.digest(project.audio_file(self.dir,'source.wav')))
        p['config']['talking_profile']='ref2va-v1'
        p['segments_clock']=project.clock(len(source),[141])
        p['segments']=copy.deepcopy(p['segments_clock'])
        project.write_plan(self.root,p);self.approve()
        captured={}
        class Client(rh.FakeClient):
            def upload(self,path,**kwargs):
                if Path(path).name=='condition.wav':
                    captured[int(Path(path).parents[1].name)]=sf.read(path,dtype='float32',always_2d=True)
                return super().upload(path,**kwargs)
        def merge(*_):
            final=self.dir/'unused.mp4';final.write_bytes(b'assembled test video');return str(final)
        with patch.object(runner,'validate_prefix',return_value={'bit_exact':True}):
            runner.run_project(self.root,self.pid,Client(),validate=lambda *_:{},
                               assemble=merge,sleep=lambda _:None)
        p=project.read_plan(self.root,self.pid)
        mono=source.mean(axis=1,keepdims=True)
        fake_nodes=types.SimpleNamespace(LoadLatent=lambda:types.SimpleNamespace(load=lambda *_:({'samples':torch.zeros(1,24,52,1,1)},)))
        paths=types.SimpleNamespace(get_output_directory=lambda:str(self.root.parent))
        with patch.object(node,'root',return_value=self.root),patch.dict(sys.modules,{'nodes':fake_nodes,'folder_paths':paths}):
            for row in p['segments']:
                managed,sr=captured[row['index']]
                expected_length=max(row['generation_frames']*1000,row['end_sample']-row['audio_start_sample'])
                self.assertEqual(len(managed),expected_length)
                available=mono[row['audio_start_sample']:row['audio_start_sample']+len(managed)]
                self.assertTrue(np.array_equal(managed[:len(available)],available))
                loaded=canvas.SegmentState().load(self.pid,row['index'],'a'*12)
                expanded=loaded[canvas.SLOT['condition_audio']]['waveform'][0].numpy().T
                self.assertTrue(np.array_equal(expanded,managed));self.assertEqual(sr,24000)
                preview=loaded[canvas.SLOT['preview_audio']]['waveform'][0].numpy().T
                self.assertTrue(np.array_equal(preview,source[row['start_sample']:row['end_sample']]))


if __name__=='__main__':unittest.main()
