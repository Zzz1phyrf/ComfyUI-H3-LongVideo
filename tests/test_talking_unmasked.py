"""No person segmentation: keep audio zero and only freeze temporal context."""
import ast
import os
import copy
import importlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch

import test_talking_rh as rh
from test_talking_rh import project, graph, runner, FakeClient
from test_talking_local import node, local


class UnmaskedProjectTests(unittest.TestCase):
    def test_new_node_does_not_segment_or_require_a_person_mask_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            folders=types.SimpleNamespace(get_output_directory=lambda:temporary)
            with patch.dict('sys.modules',folder_paths=folders),patch.object(node,'automatic_mask',side_effect=AssertionError('New projects must not segment people')),patch.object(node,'Client') as client:
                client.return_value.workflow.return_value=json.loads((Path(__file__).parent/'fixtures/talking_remote_config.json').read_text(encoding='utf-8'))
                audio={'waveform':torch.zeros(1,1,277000),'sample_rate':24000}
                image=torch.zeros(1,64,32,3)
                kwargs={'workflow_id':'123','backend':'RunningHub','megapixels':1.,'seed':999}
                result=node.TalkingRH().prepare(audio,image,reference_2=image,**kwargs)
                pid=result['result'][0];plan=project.read_plan(node.root(),pid)
                self.assertEqual(plan['config']['person_mask_mode'],'none')
                self.assertNotIn('auto_mask',plan['config'])
                self.assertEqual([a['name'] for a in plan['assets']],['reference_0.png','reference_1.png'])
                self.assertFalse((project.project_path(node.root(),pid)/'refs/person_mask.png').exists())
                restored=node.TalkingRH().prepare(audio,image,reference_2=image,project_id=pid,**kwargs)
                self.assertEqual(restored['result'][0],pid)
                self.assertEqual(client.return_value.workflow.call_count,1)

    def test_maskless_preparation_has_reference_only_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            cfg={'megapixels':1.,'seed':999,'first_seconds':6.,'segment_seconds':5.667,
                 'person_mask_mode':'none','template_sha256':project.digest(graph.TEMPLATE)}
            plan=project.prepare(Path(temporary),np.zeros((277000,1),dtype='float32'),24000,
                 [Image.new('RGB',(32,64))],None,cfg,'input')
            project.verify_assets(Path(temporary),plan)
            self.assertEqual(len(project.reference_assets(plan)),1)

    def test_expanded_local_state_uses_full_frame_and_keeps_the_last_reference(self):
        canvas=importlib.import_module('h3talk_test.talking_rh.canvas')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'projects'
            cfg={'megapixels':.6,'seed':999,'first_seconds':6.,'segment_seconds':5.667,
                 'person_mask_mode':'none','template_sha256':project.digest(graph.TEMPLATE)}
            plan=project.prepare(root,np.zeros((277000,1),dtype='float32'),24000,
                [Image.new('RGB',(32,64)),Image.new('RGB',(32,64),'red')],None,cfg,'input')
            plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(root,plan)
            with patch.object(node,'root',return_value=root),patch.dict('sys.modules',nodes=types.SimpleNamespace(),
                    folder_paths=types.SimpleNamespace(get_output_directory=lambda:str(root.parent))):
                state=canvas.SegmentState().load(plan['id'],0,'a'*12)
            self.assertTrue((state[canvas.SLOT['person_mask']]==1).all())
            self.assertEqual(tuple(state[canvas.SLOT['person_mask']].shape),(1,1056,608))
            self.assertIsNotNone(state[canvas.SLOT['reference_2']])
            self.assertEqual(canvas.RETURN_NAMES[canvas.SLOT['person_mask']],'video_generation_mask')


class UnmaskedGraphTests(unittest.TestCase):
    def plan(self):
        return {'id':'a'*32,'config':{'megapixels':1.,'seed':999,'person_mask_mode':'none'}}

    def test_cloud_graph_does_not_load_a_person_mask_but_initializes_video_as_generate(self):
        value=graph.build(self.plan(),{'index':1,'generation_frames':175},
            {'audio':'a.wav','references':['r0.png','r1.png'],'previous_latent':'p.latent','attempt':'test'},
            {'tail_token_start':80})
        self.assertNotIn('9102',value)
        self.assertEqual(value['9103'],{'class_type':'SolidMask','inputs':{'value':1.,'width':64,'height':64}})
        self.assertEqual(value['9104']['inputs']['masks'],['9103',0])
        self.assertEqual(value['9113']['inputs']['existing_mask_mode'],'subtract')
        self.assertEqual(value['249']['inputs']['value'],0)
        self.assertEqual(value['270']['class_type'],'SelfLiftAvatarH3Sampler')
        self.assertIn('ref_images.ref_image_1',value['268']['inputs'])

    def test_actual_temporal_node_leaves_future_generatable(self):
        # Execute the actual local KJ method, rather than merely checking graph topology.
        kj=Path(os.environ.get('H3_TEST_COMFYUI_ROOT', ''))/'custom_nodes/ComfyUI-KJNodes/nodes/ltxv_nodes.py'
        if not kj.is_file():self.skipTest('Installed KJ source needed')
        tree=ast.parse(kj.read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LTXVAudioVideoMask')
        method=copy.deepcopy(next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='execute'))
        method.decorator_list=[]
        scope={'torch':torch,'np':np,'io':types.SimpleNamespace(NodeOutput=lambda *v:v)}
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(kj),'exec'),scope)
        value=graph.build(self.plan(),{'index':1,'generation_frames':175},
            {'audio':'a','references':['r'],'previous_latent':'p','attempt':'test'},{'tail_token_start':80})
        white=value['9103']['inputs']['value']
        video=local.StaticVideoMask().apply({'samples':torch.zeros(1,24,52,4,4)},torch.full((1,64,64),white))[0]
        args=dict(value['9113']['inputs']);args.pop('video_latent')
        masked=scope['execute'](None,video_latent=video,**args)[0]['noise_mask']
        self.assertTrue((masked[:,:,:12]==0).all())
        self.assertTrue((masked[:,:,12:]==1).all())


class UnmaskedRunnerTests(unittest.TestCase):
    setUp=rh.LifecycleTests.setUp
    approve=rh.LifecycleTests.approve

    def test_runner_uploads_all_references_and_no_person_mask(self):
        plan=project.read_plan(self.root,self.pid)
        directory=project.project_path(self.root,self.pid)
        plan['config']['person_mask_mode']='none';plan['assets']=plan['assets'][:-1]
        # A second reference detects accidental assets[:-1] truncation.
        extra=directory/'refs/reference_1.png';Image.new('RGB',(32,64),'red').save(extra)
        plan['assets'].append({'name':extra.name,'sha256':project.digest(extra)})
        project.write_plan(self.root,plan);self.approve()
        uploaded=[]
        class Client(FakeClient):
            def upload(self,path):uploaded.append(Path(path).name);return super().upload(path)
        client=Client()
        def merge(*_):
            final=directory/'result.mp4';final.write_bytes(b'assembled test video');return str(final)
        runner.run_project(self.root,self.pid,client,validate=lambda *_:{},
            assemble=merge,sleep=lambda _:None)
        self.assertNotIn('person_mask.png',uploaded)
        self.assertEqual(uploaded.count('reference_0.png'),2)
        self.assertEqual(uploaded.count('reference_1.png'),2)
        self.assertNotIn('9102',client.created[1])


if __name__=='__main__':unittest.main()
