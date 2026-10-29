"""New speech projects match the accepted native Ref2VA execution contract."""
import copy
import importlib
import json
from pathlib import Path
import unittest
import tempfile
import numpy as np

from test_talking_rh import graph, project
from test_talking_rh import runner

sampling = importlib.import_module('h3talk_test.talking_rh.sampling')


class Ref2VATests(unittest.TestCase):
    def setUp(self):
        source = json.loads((Path(__file__).parent/'fixtures/talking_remote_config.json').read_text(encoding='utf-8'))
        source['244']['inputs']['highres_tiling_mode'] = ''
        self.plan = {'id':'e'*32, 'config':{'seed':999, 'megapixels':1.,
            'person_mask_mode':'none', 'talking_profile':'ref2va-v1',
            'rh_sampling':sampling.freeze(source)}}
        self.rows = project.clock(277000,[141])
        self.uploaded = {'audio':'segment.wav','references':['original.png'],
                         'previous_latent':'previous.latent','attempt':'a'*12}

    def build(self,index):
        return graph.build(self.plan,self.rows[index],self.uploaded,self.rows[index-1] if index else None)

    def test_every_segment_has_direct_ref2va_reference_audio_and_empty_video_latent(self):
        for index in (0,1):
            value = self.build(index)
            self.assertEqual(value['268']['class_type'],'MiniMaxH3ReferenceToVideo')
            self.assertEqual(value['270']['inputs']['positive'],['268',0])
            source = value['268']['inputs']['ref_audios.ref_audio_0']
            self.assertEqual(source[1],3)
            separation = value[source[0]]
            self.assertEqual(separation['class_type'],'AudioSeparation')
            self.assertEqual(separation['inputs']['audio'],['9000',0])
            self.assertEqual(value['9101']['class_type'],'LTXVSeparateAVLatent')
            self.assertEqual(value['9101']['inputs']['av_latent'],['268',1])
            self.assertEqual(value['248']['inputs']['audio'],['9000',0])
            self.assertEqual(value['249']['inputs']['value'],0)
            self.assertNotIn('MiniMaxH3AddGuide',[n['class_type'] for n in value.values()])

    def test_first_and_continuation_use_half_and_full_scale(self):
        self.assertAlmostEqual(self.build(0)['270']['inputs']['lowres_scale'],.5)
        self.assertEqual(self.build(1)['270']['inputs']['lowres_scale'],1.)
        self.assertEqual(self.build(1)['9111']['inputs']['index'],30)
        self.assertEqual(self.build(1)['9111']['inputs']['amount'],12)

    def test_invalid_disabled_tiling_enum_is_normalized_without_changing_snapshot(self):
        before = copy.deepcopy(self.plan)
        self.assertEqual(self.build(0)['270']['inputs']['highres_tiling_mode'],'spatial')
        self.assertEqual(self.plan,before)

    def test_single_reference_is_reused_for_the_two_references_in_fixed_prompt(self):
        reference = self.build(0)['268']['inputs']
        self.assertEqual(reference['ref_images.ref_image_0'],['9001',0])
        self.assertEqual(reference['ref_images.ref_image_1'],reference['ref_images.ref_image_0'])

    def test_reference_audio_transform_settings_are_frozen_and_hashed(self):
        snapshot = self.plan['config']['rh_sampling']
        self.assertIn('225',snapshot['graph'])
        self.assertNotIn('audio',snapshot['graph']['225']['inputs'])
        snapshot['graph']['225']['inputs']['chunk_overlap']=.4
        with self.assertRaisesRegex(ValueError,'已变化'):sampling.verify(snapshot)

    def test_actual_saved_prefix_rejects_a_sampler_that_changes_frozen_frames(self):
        from safetensors.numpy import save_file
        with tempfile.TemporaryDirectory() as temporary:
            first=np.arange(52*4,dtype='float32').reshape(1,1,52,2,2)
            second=np.zeros_like(first);second[:,:,:12]=first[:,:,30:42]
            a=Path(temporary)/'first.latent';b=Path(temporary)/'second.latent'
            save_file({'latent_tensor':first},str(a));save_file({'latent_tensor':second},str(b))
            self.assertTrue(runner.validate_prefix(a,b,30)['bit_exact'])
            second[:,:,11,0,0]+=.01
            save_file({'latent_tensor':second},str(b))
            with self.assertRaisesRegex(ValueError,'续接前缀'):runner.validate_prefix(a,b,30)

    def test_expanded_canvas_exposes_the_same_native_reference_audio_and_scale(self):
        canvas=importlib.import_module('h3talk_test.talking_rh.canvas')
        raw=json.loads((Path(__file__).parent/'fixtures/talking_ref2va_canvas_api.json').read_text(encoding='utf-8'))
        saved=canvas.capture(raw,'5')
        canvas.verify_ref2va(saved)
        self.assertEqual(saved['sampler_id'],'270')
        self.assertEqual(raw['270']['inputs']['positive'],['9400',0])
        self.assertEqual(raw['9400']['inputs']['ref_audios.ref_audio_0'],['9250',3])
        self.assertEqual(raw['270']['inputs']['lowres_scale'],['5',canvas.SLOT['lowres_scale']])
        self.assertEqual(raw['270']['inputs']['vae'],['9602',0])

    def test_new_canvas_rejects_legacy_condition_and_mismatched_audio_clock(self):
        canvas=importlib.import_module('h3talk_test.talking_rh.canvas')
        fixtures=Path(__file__).parent/'fixtures'
        legacy=json.loads((fixtures/'talking_canvas_api.json').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ValueError,'Ref2VA'):canvas.verify_ref2va(canvas.capture(legacy,'5'))
        raw=json.loads((fixtures/'talking_ref2va_canvas_api.json').read_text(encoding='utf-8'))
        for key,field,value in [('248','audio',['5',canvas.SLOT['preview_audio']]),('9400','audio_vae',None)]:
            changed=copy.deepcopy(raw);changed[key]['inputs'][field]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'分段时钟'):
                canvas.verify_ref2va(canvas.capture(changed,'5'))


if __name__=='__main__':unittest.main()
