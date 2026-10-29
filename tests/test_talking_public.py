"""Public single-image interface and exact local/RH request routing; no generation."""
import copy
import tempfile
import types
import unittest
from unittest.mock import patch

import torch

from test_talking_local import node
from test_talking_overlap import fixture, graph, project, canvas, overlap


def public_canvas():
    raw=fixture(True)
    for item in raw.values():
        if item['class_type']=='SamplerCustom':item['inputs']['noise_seed']=9260
    old_to_new={old:new for new,old in enumerate(node.TalkingAvatar.LEGACY_SLOTS)}
    for item in raw.values():
        for name,value in item['inputs'].items():
            if isinstance(value,list) and len(value)==2 and value[0]=='5':
                item['inputs'][name]=['5',old_to_new[value[1]]]
    raw['5']['class_type']='H3LVTalkingAvatar'
    return raw


class PublicTalkingTests(unittest.TestCase):
    def test_schema_has_one_image_and_only_consumed_outputs(self):
        schema=node.TalkingAvatar.INPUT_TYPES()
        images=[name for group in ('required','optional') for name,spec in schema[group].items() if spec[0]=='IMAGE']
        self.assertEqual(images,['reference_image'])
        self.assertEqual(len(node.TalkingAvatar.RETURN_TYPES),10)
        self.assertEqual(len(node.TalkingRH.RETURN_TYPES),29)
        self.assertFalse(node.TalkingAvatar.DEPRECATED)
        self.assertTrue(node.TalkingRH.DEPRECATED)
        for name in ('person_mask','execution_mode','continuation_mode','local_model','reference_3'):
            self.assertNotIn(name,{**schema['required'],**schema['optional']})

    def test_local_single_image_reaches_real_segment_reader(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:tmp)),patch.object(node.canvas,'blocked',side_effect=lambda p:(p['id'],)+(None,)*28),patch.object(node,'Client') as client,patch.object(node,'validate_models') as models:
            raw=public_canvas();before=copy.deepcopy(raw)
            kwargs=dict(audio={'waveform':torch.zeros(1,1,430000),'sample_rate':24000},
                reference_image=torch.zeros(1,64,32,3),prompt=raw,unique_id='5')
            result=node.TalkingAvatar().prepare(**kwargs)
            self.assertEqual(len(result['result']),10)
            plan=project.read_plan(node.root(),result['result'][0])
            self.assertEqual(len(project.reference_assets(plan)),1)
            self.assertEqual(raw,before)
            self.assertEqual(plan['config']['fixed_prompt'],overlap.DEFAULT_PROMPT.strip())
            queued=graph.build(plan,plan['segments'][1],{'attempt':'a'*12})
            condition=queued['268']['inputs']
            self.assertEqual(condition['ref_images.ref_image_0'],['5',canvas.SLOT['reference_image']])
            self.assertNotIn('ref_images.ref_image_1',condition)
            plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(node.root(),plan)
            with patch.dict('sys.modules',nodes=types.SimpleNamespace()):
                state=canvas.SegmentState().load(plan['id'],1,'a'*12)
            self.assertEqual(float(state[canvas.SLOT['reference_image']].max()),0.)
            self.assertIsNone(state[canvas.SLOT['reference_2']])
            models.assert_not_called();client.assert_not_called()

    def test_remote_single_image_prompt_frozen_and_resume_checked(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:tmp)),patch.object(node,'Client') as client:
            client.return_value.workflow.return_value=fixture()
            kwargs=dict(audio={'waveform':torch.zeros(1,1,24000),'sample_rate':24000},reference_image=torch.zeros(1,64,32,3),
                        backend='RunningHub',workflow_id='123')
            pid=node.TalkingAvatar().prepare(**kwargs)['result'][0]
            plan=project.read_plan(node.root(),pid)
            raw=graph.build(plan,plan['segments'][0],{'audio':'window.wav','reference_audio':'window.wav','references':['person.png'],'attempt':'a'*12})
            refs=raw['268']['inputs']
            self.assertEqual(raw[refs['ref_images.ref_image_0'][0]]['inputs']['image'],'person.png')
            self.assertNotIn('ref_images.ref_image_1',refs)
            self.assertEqual(refs['prompt'],overlap.DEFAULT_PROMPT.strip())
            self.assertEqual(node.TalkingAvatar().prepare(**kwargs,project_id=pid)['result'][0],pid)
            with self.assertRaisesRegex(ValueError,'新建项目'):
                node.TalkingAvatar().prepare(**{**kwargs,'reference_image':torch.ones(1,64,32,3)},project_id=pid)
            self.assertEqual(client.return_value.workflow.call_count,1)
            client.return_value.create.assert_not_called()

    def test_legacy_background_argument_is_not_accepted(self):
        with self.assertRaises(TypeError):
            node.TalkingAvatar().prepare(None,None,background_image=torch.zeros(1))

    def test_custom_prompt_is_not_silently_rewritten_and_legacy_graph_is_unchanged(self):
        with patch.object(node.TalkingRH,'prepare',return_value={'result':tuple(range(29))}) as prepare:
            value=node.TalkingAvatar().prepare(None,None,fixed_prompt='My single picture prompt.')
            self.assertEqual(prepare.call_args.kwargs['fixed_prompt'],'My single picture prompt.')
            self.assertEqual(value['result'],node.TalkingAvatar.LEGACY_SLOTS)
        raw=public_canvas();raw['268']['inputs']['length']=['5',10]
        with self.assertRaisesRegex(ValueError,'接线已过期'):
            node.TalkingAvatar.legacy_prompt(raw,'5')
