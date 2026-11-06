import copy
import hashlib
import importlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_talking_rh import graph, project

sampling=importlib.import_module('h3talk_test.talking_rh.sampling')
node=importlib.import_module('h3talk_test.talking_rh')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class RemoteSamplingTests(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((Path(__file__).parent/'fixtures/talking_remote_config.json').read_text(encoding='utf-8'))
        self.plan = {'id':'a'*32, 'config':{'seed':999, 'megapixels':1.,
                    'rh_sampling':{'graph':self.source, 'sha256':digest(self.source)}}}
        self.rows = project.clock(583000, [311,447])
        self.uploaded = {'audio':'segment.wav','references':['original.png'],
                         'mask':'activity.png','previous_latent':'previous.latent','attempt':'new'}

    def test_cloud_sampler_and_entire_model_sigma_chain_replace_template_defaults(self):
        value = graph.build(self.plan, self.rows[0], self.uploaded)
        sampler = value['270']['inputs']
        self.assertEqual(sampler['lowres_scale'], .5)
        self.assertEqual(sampler['transition_step'], 4)
        self.assertEqual(sampler['seed'], 999)  # Explicit project control, not a sampler default.
        loras = [n['inputs'] for n in value.values() if n['class_type']=='LoraLoaderModelOnly']
        self.assertEqual([(n['lora_name'],n['strength_model']) for n in loras],
                         [('dmad_minimax_h3_4step_full_critic_comfy.safetensors', .8)])
        self.assertEqual([n['inputs']['steps'] for n in value.values() if n['class_type']=='BasicScheduler'], [6])
        self.assertNotIn('H3SigmaRefiner', [n['class_type'] for n in value.values()])
        self.assertEqual(value['249']['inputs']['value'], 0)
        self.assertEqual(value['246']['inputs']['audio_latent'], ['250',0])
        self.assertEqual(value['268']['class_type'], 'MiniMaxH3ImageToVideo')

    def test_continuation_keeps_actual_retained_tail_and_source_is_not_mutated(self):
        before = copy.deepcopy(self.plan)
        value = graph.build(self.plan, self.rows[1], self.uploaded, self.rows[0])
        self.assertEqual(value['9111']['inputs']['index'], self.rows[0]['tail_token_start'])
        self.assertEqual(value['9111']['inputs']['amount'], 12)
        self.assertEqual(value['268']['class_type'], 'MiniMaxH3ReferenceToVideo')
        self.assertEqual(self.plan, before)

    def test_freeze_excludes_unused_assets_and_secrets_and_verifies_integrity(self):
        self.source['unused']={'class_type':'ExternalAPI','inputs':{'api_key':'must-not-persist'}}
        snapshot=sampling.freeze(self.source)
        self.assertNotIn('must-not-persist',json.dumps(snapshot))
        self.assertNotIn('fixture',json.dumps(snapshot))
        self.assertEqual(sampling.summary(snapshot)['avatar']['lowres_scale'],.5)
        snapshot['graph']['213']['inputs']['strength_model']=1
        with self.assertRaisesRegex(ValueError,'已变化'):sampling.verify(snapshot)

    def test_bad_audio_mask_or_non_avatar_is_rejected_without_fallback(self):
        self.source['247']['inputs']['value']=1
        with self.assertRaisesRegex(ValueError,'全零'):sampling.freeze(self.source)
        self.source['244']['class_type']='KSampler'
        with self.assertRaisesRegex(ValueError,'Avatar'):sampling.freeze(self.source)

    def test_actual_node_freezes_once_and_resume_does_not_read_changed_cloud(self):
        import tempfile
        import torch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'projects'
            audio={'waveform':torch.zeros(1,1,180000),'sample_rate':24000}
            image=torch.zeros(1,64,32,3)
            mask=torch.zeros(1,64,32);mask[:,10:55,5:27]=1
            with patch.object(node,'root',return_value=root),patch.object(node,'Client') as client:
                client.return_value.workflow.return_value=self.source
                created=node.TalkingRH().prepare(audio,image,mask,workflow_id='123',backend='RunningHub')
                pid=created['result'][0]
                first=project.read_plan(root,pid)
                self.assertEqual(first['config']['rh_sampling']['graph']['213']['inputs']['strength_model'],.8)
                client.return_value.workflow.side_effect=AssertionError('Resume must use frozen config')
                restored=node.TalkingRH().prepare(audio,image,mask,workflow_id='123',backend='RunningHub',project_id=pid)
                self.assertEqual(restored['result'][0],pid)
                self.assertEqual(client.return_value.workflow.call_count,1)


if __name__ == '__main__':
    unittest.main()
