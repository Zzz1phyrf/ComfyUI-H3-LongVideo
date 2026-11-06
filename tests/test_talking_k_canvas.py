import copy
import importlib
import json
from pathlib import Path
import unittest

import torch

from test_talking_rh import graph

canvas=importlib.import_module('h3talk_test.talking_rh.canvas')


def k_canvas():
    value=json.loads((Path(__file__).parent/'fixtures/talking_ref2va_canvas_api.json').read_text(encoding='utf-8'))
    old=value['270']['inputs']
    value['270']={'class_type':'SamplerCustom','inputs':{'model':old['low_res_model'],
        'add_noise':True,'noise_seed':old['seed'],'cfg':old['cfg'],'positive':old['positive'],
        'negative':old['negative'],'sampler':old['sampler'],'sigmas':old['sigmas'],'latent_image':old['latent_image']}}
    value['9430']={'class_type':'H3LVTalkingFrozenPrefix','inputs':{'samples':['251',0],
        'has_previous':['5',canvas.SLOT['has_previous']],'previous':['5',canvas.SLOT['previous_latent']],
        'tail_token_start':['5',canvas.SLOT['tail_token_start']]}}
    value['122']['inputs']['samples']=['9430',0]
    value['9200']['inputs']['samples']=['9430',0]
    return value


class FrozenPrefixTests(unittest.TestCase):
    def setUp(self):
        self.previous={'samples':torch.randn(1,24,52,2,2)}
        self.current={'samples':torch.randn(1,24,52,2,2)}
        self.current['samples'][:,:,:12]=self.previous['samples'][:,:,35:47]

    def test_round_trip_noise_is_checked_then_only_prefix_is_restored(self):
        self.current['samples'][:,:,0,0,0]+=4.77e-7
        before=self.current['samples'].clone()
        result=canvas.FrozenPrefix().restore(self.current,True,35,self.previous)[0]
        self.assertTrue(torch.equal(result['samples'][:,:,:12],self.previous['samples'][:,:,35:47]))
        self.assertTrue(torch.equal(result['samples'][:,:,12:],before[:,:,12:]))
        self.assertTrue(torch.equal(self.current['samples'],before))

    def test_actual_sampler_damage_is_rejected_instead_of_hidden_by_copy(self):
        self.current['samples'][:,:,11,1,1]+=.001
        with self.assertRaisesRegex(ValueError,'前缀'):
            canvas.FrozenPrefix().restore(self.current,True,35,self.previous)

    def test_first_segment_does_not_require_a_previous_latent(self):
        self.assertIs(canvas.FrozenPrefix().restore(self.current,False,0)[0],self.current)


class KCanvasTests(unittest.TestCase):
    def test_native_k_is_captured_and_submitted_without_hidden_sampler_rewrite(self):
        raw=k_canvas();saved=canvas.capture(raw,'5');canvas.verify_ref2va(saved)
        plan={'id':'a'*32,'assets':[{}],'config':{'execution_mode':'canvas','talking_profile':'ref2va-v1','canvas':saved,'person_mask_mode':'none'}}
        value=graph.build(plan,{'index':1},{'attempt':'b'*12})
        self.assertEqual(value['270'],raw['270'])
        self.assertEqual(value['9430'],raw['9430'])
        self.assertEqual(value['122']['inputs']['samples'],['9430',0])

    def test_decorative_or_bypassed_prefix_restore_is_rejected(self):
        for key in ('122','9200'):
            raw=k_canvas();raw[key]['inputs']['samples']=['251',0]
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'前缀'):
                canvas.verify_ref2va(canvas.capture(raw,'5'))


if __name__=='__main__':unittest.main()
