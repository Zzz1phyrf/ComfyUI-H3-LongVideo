import copy
import importlib
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import soundfile as sf
import torch

import test_talking_rh as rh
from test_talking_rh import graph, project, runner, FakeClient

canvas=importlib.import_module('h3talk_test.talking_rh.canvas')
FIXTURE=Path(__file__).parent/'fixtures/talking_canvas_api.json'


class CanvasGraphTests(unittest.TestCase):
    def graph(self):return json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_real_canvas_edits_reach_queued_graph_without_touching_source(self):
        raw=self.graph();raw['270']['inputs'].update(cfg=1.2,rho=.05,transition_step=4)
        raw['127']['inputs']['unet_name']='explicitly-selected-model.safetensors'
        snapshot=copy.deepcopy(raw);saved=canvas.capture(raw,'5')
        plan={'id':'a'*32,'assets':[{},{}],'config':{'execution_mode':'canvas','canvas':saved}}
        result=graph.build(plan,{'index':1},{'attempt':'a'*12})
        self.assertEqual(result['270']['inputs']['cfg'],1.2)
        self.assertEqual(result['270']['inputs']['rho'],.05)
        self.assertEqual(result['127']['inputs']['unet_name'],'explicitly-selected-model.safetensors')
        self.assertEqual(result['5']['class_type'],'H3LVTalkingSegmentState')
        self.assertEqual(result['5']['inputs']['segment_index'],1)
        self.assertEqual(result['122']['class_type'],'VAEDecode')
        self.assertEqual(raw,snapshot)
        self.assertNotIn('1',result)
        self.assertNotIn('9500',result)

    def test_canvas_snapshot_ignores_only_transient_loader_state(self):
        raw=self.graph();a=canvas.capture(raw,'5')
        raw['5']['inputs']['project_id']='b'*32
        self.assertEqual(a,canvas.capture(raw,'5'))
        raw['270']['inputs']['cfg']=1.3
        self.assertNotEqual(a,canvas.capture(raw,'5'))

    def test_removing_audio_zero_mask_or_preview_clock_is_rejected(self):
        for key,field,value in [('249','value',1),('9420','batch_index',0),('9421','audio',['5',3])]:
            raw=self.graph();raw[key]['inputs'][field]=value
            with self.subTest(key=key),self.assertRaises(ValueError):canvas.capture(raw,'5')

    def test_native_output_ids_are_taken_from_the_canvas(self):
        raw=self.graph();raw['777']=raw.pop('273')
        saved=canvas.capture(raw,'5')
        self.assertEqual(canvas.output_ids({'canvas':saved})['video'],'777')

    def test_unused_reference_ports_are_removed_for_actual_reference_count(self):
        raw=self.graph();saved=canvas.capture(raw,'5')
        plan={'id':'a'*32,'assets':[{},{}],'config':{'canvas':saved}}
        value=canvas.build(plan,{'index':1},{'attempt':'a'*12})
        refs=value['9400']['inputs']
        self.assertIn('ref_images.ref_image_0',refs)
        self.assertNotIn('ref_images.ref_image_1',refs)
        plan['assets'].insert(0,{})
        value=canvas.build(plan,{'index':0},{'attempt':'a'*12})
        self.assertIn('ref_images.ref_image_1',value['9400']['inputs'])

    def test_model_loads_are_lazy_until_the_segment_reader_runs(self):
        raw=self.graph()
        for source,gate in [('127','9600'),('128','9601'),('119','9602'),('120','9603')]:
            self.assertEqual(raw[gate]['class_type'],'LazySwitchKJ')
            self.assertEqual(raw[gate]['inputs']['switch'],['5',canvas.SLOT['use_endpoint']])
            self.assertEqual(raw[gate]['inputs']['on_true'],[source,0])


class CanvasLifecycleTests(unittest.TestCase):
    setUp=rh.LifecycleTests.setUp
    approve=rh.LifecycleTests.approve
    def test_canvas_finishes_segments_and_auto_assembles(self):
        raw=json.loads(FIXTURE.read_text(encoding='utf-8'))
        plan=project.read_plan(self.root,self.pid)
        plan['config'].update(backend='local',execution_mode='canvas',canvas=canvas.capture(raw,'5'))
        project.write_plan(self.root,plan);self.approve()
        class Client(FakeClient):
            def reserve(self):return 'known-'+str(len(self.created))
            def create(self,value,task_id):self.created.append(value);return task_id
            def query(self,task_id):
                result=super().query(task_id)
                result['results'].append({'nodeId':'9422','outputType':'mp4','url':'https://example.test/preview'})
                return result
        client=Client()
        def probe(path):
            index=int(Path(path).parents[1].name)
            return {'nb_read_frames':str(plan['segments'][index]['edit_frames']),'r_frame_rate':'24/1'}
        def merge(*_):
            final=self.dir/'final.mp4';final.write_bytes(b'assembled test video');return str(final)
        with patch.object(runner.controller,'probe_video',side_effect=probe):
            runner.run_project(self.root,self.pid,client,validate=lambda *_:{},assemble=merge,sleep=lambda _:None)
        current=project.read_plan(self.root,self.pid)
        self.assertEqual(current['run_status'],'completed')
        self.assertTrue(Path(current['final_video']).is_file())
        self.assertEqual(len(client.created),2)
        self.assertTrue(all(Path(row['job']['preview']).is_file() for row in current['segments']))
        for i,queued in enumerate(client.created):self.assertEqual(queued['5']['inputs']['segment_index'],i)


class SegmentPreviewTests(unittest.TestCase):
    def test_retained_preview_uses_crop_and_the_matching_source_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'projects';pid='c'*32;directory=project.project_path(root,pid)
            for name in ('state','takes','audio','cache'):(directory/name).mkdir(parents=True,exist_ok=True)
            frames=np.zeros((48,32,32,3),dtype='uint8');frames[:,:,:,1]=100
            frames[:,:,:,0]=np.arange(48,dtype='uint8')[:,None,None]*4
            raw=directory/'takes/raw.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','32x32','-r','24','-i','pipe:0',
                            '-c:v','libx264','-crf','0','-pix_fmt','yuv420p',str(raw)],input=frames.tobytes(),check=True)
            audio=np.concatenate([np.sin(np.arange(5000)*2*np.pi*300/24000),np.sin(np.arange(5000)*2*np.pi*600/24000)]).astype('float32')*.1
            source=directory/'audio/source.wav';sf.write(source,audio,24000,subtype='FLOAT')
            plan={'id':pid,'source_sha256':project.digest(source),'segments':[{'index':0,'start_sample':5000,'end_sample':10000,
                  'edit_start_frame':39,'edit_frames':5,'job':{'status':'completed','video':str(raw),'video_sha256':project.digest(raw)}}]}
            project.write_plan(root,plan)
            preview=canvas.segment_preview(root,pid,0)
            pixels=subprocess.check_output(['ffmpeg','-v','error','-i',str(preview),'-f','rawvideo','-pix_fmt','rgb24','-'])
            value=np.frombuffer(pixels,dtype='uint8').reshape(-1,32,32,3)
            self.assertEqual(len(value),5)
            self.assertLess(abs(float(value[0,:,:,0].mean())-156),6)
            sound=subprocess.check_output(['ffmpeg','-v','error','-i',str(preview),'-map','0:a:0','-f','f32le','-ac','1','-ar','24000','-'])
            decoded=np.frombuffer(sound,dtype='<f4')[:5000]
            self.assertGreater(np.corrcoef(decoded,audio[5000:5000+len(decoded)])[0,1],.98)
            self.assertEqual(preview,canvas.segment_preview(root,pid,0))
            with self.assertRaises(ValueError):canvas.segment_preview(root,pid,-1)


class SegmentStateTests(unittest.TestCase):
    def test_native_segment_inputs_keep_audio_context_and_actual_retained_tail(self):
        node=importlib.import_module('h3talk_test.talking_rh')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'projects'
            cfg={'backend':'local','execution_mode':'canvas','megapixels':.6,'seed':999,'first_seconds':6.,'segment_seconds':5.667,
                 'template_sha256':project.digest(graph.TEMPLATE)}
            wave=np.linspace(-.2,.2,277000,dtype='float32')[:,None]
            mask=np.zeros((64,32),dtype='float32');mask[8:52,7:26]=1
            plan=project.prepare(root,wave,24000,[Image.new('RGB',(32,64),'gray')],mask,cfg,'input')
            plan=project.update_cuts(root,plan['id'],[5.875]);plan.update(approved=True,approved_fingerprint=project.fingerprint(plan))
            project.write_plan(root,plan)
            previous={'samples':torch.zeros(1,24,52,2,2)}
            loader=unittest.mock.Mock();loader.return_value.load.return_value=(previous,)
            with patch.object(node,'root',return_value=root),patch.dict('sys.modules',nodes=types.SimpleNamespace(LoadLatent=loader),
                     folder_paths=types.SimpleNamespace(get_output_directory=lambda:str(root.parent))):
                first=canvas.SegmentState().load(plan['id'],0,'a'*12)
                self.assertTrue(first[canvas.SLOT['use_endpoint']]);self.assertFalse(first[canvas.SLOT['has_previous']])
                self.assertIsNone(first[canvas.SLOT['previous_latent']])
                self.assertEqual(first[canvas.SLOT['condition_audio']]['waveform'].shape[-1],175000)
                self.assertEqual(first[canvas.SLOT['preview_audio']]['waveform'].shape[-1],141000)
                latent=project.project_path(root,plan['id'])/'takes/previous.latent';latent.write_bytes(b'native latent file')
                plan['segments'][0]['job']={'status':'completed','latent':str(latent),'latent_sha256':project.digest(latent)}
                project.write_plan(root,plan)
                second=canvas.SegmentState().load(plan['id'],1,'b'*12)
                self.assertFalse(second[canvas.SLOT['use_endpoint']]);self.assertTrue(second[canvas.SLOT['has_previous']])
                self.assertIs(second[canvas.SLOT['previous_latent']],previous)
                loader.return_value.load.assert_called_once_with(latent.relative_to(root.parent).as_posix()+' [output]')
                self.assertEqual(second[canvas.SLOT['tail_token_start']],30)
                self.assertEqual(second[canvas.SLOT['crop_start']],39)
                self.assertEqual(second[canvas.SLOT['keep_frames']],136)
                np.testing.assert_array_equal(second[canvas.SLOT['condition_audio']]['waveform'].numpy()[0,0],wave[102000:,0])
                np.testing.assert_array_equal(second[canvas.SLOT['preview_audio']]['waveform'].numpy()[0,0],wave[141000:,0])

    def test_merge_refuses_unapproved_and_tampered_completed_media(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'projects';pid='d'*32;directory=project.project_path(root,pid)
            for name in ('state','takes'):(directory/name).mkdir(parents=True,exist_ok=True)
            plan={'id':pid,'approved':False,'segments':[]}
            project.write_plan(root,plan)
            with self.assertRaisesRegex(ValueError,'确认'):canvas.assemble_project(root,pid)
            raw=directory/'takes/raw.mp4';raw.write_bytes(b'original')
            plan.update(config={},assets=[],segments_clock=[],samples=24000,approved=True,
                        segments=[{'job':{'status':'completed','video':str(raw),'video_sha256':project.digest(raw)}}])
            plan['approved_fingerprint']=project.fingerprint(plan);project.write_plan(root,plan)
            raw.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'原片已变化'):canvas.assemble_project(root,pid)


if __name__=='__main__':unittest.main()
