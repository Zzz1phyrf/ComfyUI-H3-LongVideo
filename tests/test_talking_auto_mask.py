import importlib
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch

from test_talking_rh import project, graph
from test_talking_local import node


class AutomaticNodeTests(unittest.TestCase):
    def test_person_mask_is_not_required(self):
        folders=types.SimpleNamespace(get_filename_list=lambda _:[])
        with patch.dict('sys.modules',folder_paths=folders):
            self.assertNotIn('person_mask',node.TalkingRH.INPUT_TYPES()['required'])

    def test_legacy_automatic_project_restores_its_original_mask_and_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            folders=types.SimpleNamespace(get_output_directory=lambda:str(base))
            mask=np.zeros((64,32),dtype='float32');mask[8:52,7:26]=1
            info={'mode':'automatic','algorithm':'test','model_sha256':'a'*64,'cache_key':'b'*64}
            with patch.dict('sys.modules',folder_paths=folders),patch.object(node,'automatic_mask',return_value=(mask,info),create=True) as generate,patch.object(node,'Client') as client:
                client.return_value.workflow.return_value=json.loads((Path(__file__).parent/'fixtures/talking_remote_config.json').read_text(encoding='utf-8'))
                kwargs={'workflow_id':'123','backend':'RunningHub','megapixels':1.,'seed':999}
                audio={'waveform':torch.zeros(1,1,24000),'sample_rate':24000}
                image=torch.zeros(1,64,32,3)
                sampling=importlib.import_module('h3talk_test.talking_rh.sampling')
                config={'base_url':'https://www.runninghub.ai','workflow_id':'123','instance_type':'ultra',
                        'megapixels':1.,'seed':999,'first_seconds':13.,'segment_seconds':5.667,
                        'template_sha256':project.digest(graph.TEMPLATE),
                        'rh_sampling':sampling.freeze(client.return_value.workflow.return_value),'auto_mask':info}
                wave=np.zeros((24000,1),dtype='float32')
                signature=hashlib.sha256(wave.tobytes()+b'24000')
                for value in (image.numpy(),mask[None]):signature.update(str(value.shape).encode()+value.tobytes())
                signature.update(json.dumps(config,sort_keys=True).encode())
                plan=project.prepare(node.root(),wave,24000,[Image.new('RGB',(32,64))],mask,config,signature.hexdigest())
                pid=plan['id']
                self.assertEqual(plan['config']['auto_mask'],info)
                restored=node.TalkingRH().prepare(audio,image,project_id=pid,**kwargs)
                self.assertEqual(restored['result'][0],pid)
                self.assertEqual(generate.call_count,1)
                client.return_value.workflow.assert_not_called()
                actual=graph.build(plan,plan['segments'][0],{'references':['r.png'],'mask':'auto.png','audio':'a.wav','attempt':'test'})
                self.assertEqual(actual['270']['class_type'],'SelfLiftAvatarH3Sampler')
                self.assertEqual(actual['249']['inputs']['value'],0)

    def test_connected_legacy_mask_keeps_its_project_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            folders=types.SimpleNamespace(get_output_directory=lambda:temporary)
            mask=torch.zeros(1,64,32);mask[:,8:52,7:26]=1
            audio={'waveform':torch.zeros(1,1,24000),'sample_rate':24000}
            image=torch.zeros(1,64,32,3)
            with patch.dict('sys.modules',folder_paths=folders),patch.object(node,'automatic_mask',side_effect=AssertionError('Legacy mask must not be replaced')):
                config={'base_url':'https://www.runninghub.ai','workflow_id':'123','instance_type':'ultra',
                        'megapixels':1.,'seed':999,'first_seconds':13.,'segment_seconds':5.667,
                        'template_sha256':project.digest(graph.TEMPLATE)}
                wave=np.zeros((24000,1),dtype='float32')
                signature=hashlib.sha256(wave.tobytes()+b'24000')
                for value in (image.numpy(),mask.numpy()):signature.update(str(value.shape).encode()+value.tobytes())
                signature.update(json.dumps(config,sort_keys=True).encode())
                before=project.prepare(node.root(),wave,24000,[Image.new('RGB',(32,64))],mask.numpy()[0],config,signature.hexdigest())
                pid=before['id']
                restored=node.TalkingRH().prepare(audio,image,mask,workflow_id='123',backend='RunningHub',project_id=pid)
                self.assertEqual(restored['result'][0],pid)
                self.assertNotIn('auto_mask',before['config'])
                self.assertEqual(project.read_plan(node.root(),pid)['input_fingerprint'],before['input_fingerprint'])


class AutomaticMaskTests(unittest.TestCase):
    def module(self):return importlib.import_module('h3talk_test.talking_rh.auto_mask')

    def labels(self):
        labels=np.zeros((100,60),dtype='uint8');labels[20:75,20:40]=4;labels[16:26,25:35]=3
        return labels

    def test_activity_margin_is_white_with_a_soft_edge_and_black_background(self):
        module=self.module();labels=self.labels()
        mask=module.activity_mask(labels)
        self.assertEqual(float(mask[40,30]),1.)
        self.assertEqual(float(mask[40,19]),1.)
        self.assertEqual(float(mask[0,0]),0.)
        self.assertTrue(((mask>0)&(mask<1)).any())
        with self.assertRaises(ValueError):module.activity_mask(np.zeros_like(labels))

    def test_cache_reuses_result_and_detects_changed_reference_and_tampering(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);model=base/'model.tflite';model.write_bytes(b'model')
            image=Image.new('RGB',(60,100),'gray')
            with patch.object(module,'model_path',return_value=model),patch.object(module,'segment',return_value=self.labels()) as infer:
                first,info=module.automatic_mask(image,base/'cache')
                second,again=module.automatic_mask(image,base/'cache')
                self.assertTrue(np.array_equal(first,second));self.assertEqual(info,again)
                self.assertEqual(infer.call_count,1)
                module.automatic_mask(Image.new('RGB',(60,100),'white'),base/'cache')
                self.assertEqual(infer.call_count,2)
                (base/'cache'/info['cache_key']/'mask.png').write_bytes(b'tampered')
                with self.assertRaises(ValueError):module.automatic_mask(image,base/'cache')


if __name__=='__main__':unittest.main()
