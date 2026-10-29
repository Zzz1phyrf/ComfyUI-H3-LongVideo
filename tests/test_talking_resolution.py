"""External preset dimensions reach the frozen project, segment reader and graph."""
import tempfile
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import torch

from test_talking_public import public_canvas, node, project, canvas
from test_talking_overlap import fixture, graph


class ExternalResolutionTests(unittest.TestCase):
    def test_saved_canvas_migration_preserves_seed_prompt_and_output_links(self):
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('talking_resolution_migration',root/'scripts/migrate_talking_resolution.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        old={'last_node_id':5,'last_link_id':4,'nodes':[
            {'id':5,'type':'H3LVTalkingAvatar','pos':[400,0],'widgets_values':['本地','','海外版','ultra',1.,9260,'old','prompt',None],
             'inputs':[{'name':'audio','link':1},{'name':'reference_image','link':2},{'name':'seed','link':3,'widget':{'name':'seed'}}],
             'outputs':[{'name':'project_id','links':[4]}]}],
             'links':[[1,1,0,5,0,'AUDIO'],[2,2,0,5,1,'IMAGE'],[3,3,0,5,2,'INT'],[4,5,0,6,0,'STRING']]}
        converted=module.migrate(old);loader=converted['nodes'][0]
        self.assertEqual(loader['widgets_values'],['本地','','海外版','ultra',9260,'old','prompt'])
        self.assertEqual(converted['links'][2][4],4)
        self.assertEqual(converted['links'][3],old['links'][3])
        self.assertEqual(converted['nodes'][1]['type'],'ResolutionSelector')
        self.assertEqual(module.migrate(converted),converted)
        self.assertEqual(old['nodes'][0]['widgets_values'][4],1.)

    def test_public_dimensions_are_external_inputs_not_internal_mp(self):
        schema=node.TalkingAvatar.INPUT_TYPES()
        for name in ('width','height'):
            self.assertEqual(schema['required'][name][0],'INT')
            self.assertTrue(schema['required'][name][1]['forceInput'])
        self.assertNotIn('megapixels',{**schema['required'],**schema['optional']})

    def test_external_landscape_size_reaches_real_reader_and_is_frozen(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:tmp)),patch.object(node.canvas,'blocked',side_effect=lambda p:(p['id'],)+(None,)*27):
            kwargs=dict(audio={'waveform':torch.zeros(1,1,240000),'sample_rate':24000},
                        reference_image=torch.zeros(1,64,32,3),width=1024,height=576,prompt=public_canvas(),unique_id='5')
            pid=node.TalkingAvatar().prepare(**kwargs)['result'][0]
            plan=project.read_plan(node.root(),pid)
            self.assertEqual((plan['config']['width'],plan['config']['height']),(1024,576))
            self.assertNotIn('megapixels',plan['config'])
            plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(node.root(),plan)
            with patch.dict('sys.modules',nodes=types.SimpleNamespace()):
                state=canvas.SegmentState().load(pid,0,'a'*12)
            self.assertEqual((state[canvas.SLOT['width']],state[canvas.SLOT['height']]),(1024,576))
            with self.assertRaisesRegex(ValueError,'新建项目'):
                node.TalkingAvatar().prepare(**{**kwargs,'width':960},project_id=pid)

    def test_rh_binding_uses_exact_preset_size_without_reinterpreting_as_mp(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:tmp)),patch.object(node,'Client') as client:
            client.return_value.workflow.return_value=fixture()
            pid=node.TalkingAvatar().prepare(audio={'waveform':torch.zeros(1,1,24000),'sample_rate':24000},
                reference_image=torch.zeros(1,64,32,3),backend='RunningHub',workflow_id='123',width=1408,height=800)['result'][0]
            plan=project.read_plan(node.root(),pid)
            queued=graph.build(plan,plan['segments'][0],{'audio':'clip.wav','references':['person.png'],'attempt':'a'*12})
            self.assertEqual((queued['268']['inputs']['width'],queued['268']['inputs']['height']),(1408,800))
            client.return_value.create.assert_not_called()

    def test_existing_mp_project_can_resume_through_equal_external_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:tmp)),patch.object(node,'Client') as client:
            client.return_value.workflow.return_value=fixture()
            kwargs=dict(audio={'waveform':torch.zeros(1,1,24000),'sample_rate':24000},
                reference_image=torch.zeros(1,64,32,3),backend='RunningHub',workflow_id='123')
            pid=node.TalkingAvatar().prepare(**kwargs)['result'][0]
            self.assertEqual(node.TalkingAvatar().prepare(**kwargs,project_id=pid,width=768,height=1376)['result'][0],pid)
            with self.assertRaisesRegex(ValueError,'新建项目'):
                node.TalkingAvatar().prepare(**kwargs,project_id=pid,width=736,height=1312)
            self.assertEqual(client.return_value.workflow.call_count,1)

    def test_old_mp_projects_keep_their_size_and_external_size_is_validated(self):
        self.assertEqual(project.output_size({'megapixels':1.}),(768,1376))
        self.assertEqual(project.output_size({'width':736,'height':1312}),(736,1312))
        for config in ({'width':768},{'width':True,'height':1376},{'width':767,'height':1376},
                       {'width':768.5,'height':1376},{'width':0,'height':1376}):
            with self.subTest(config=config),self.assertRaisesRegex(ValueError,'宽高'):
                project.output_size(config)
