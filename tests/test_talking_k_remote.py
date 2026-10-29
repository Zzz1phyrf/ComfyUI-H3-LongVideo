import copy
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from safetensors.numpy import load_file,save_file

from test_talking_rh import graph,project,runner,remote

sampling=importlib.import_module('h3talk_test.talking_rh.sampling')
prefix=importlib.import_module('h3talk_test.talking_rh.prefix')


def source(k=True):
    value=json.loads((Path(__file__).parent/'fixtures/talking_remote_config.json').read_text(encoding='utf-8'))
    if k:
        old=value['244']['inputs']
        value['244']={'class_type':'SamplerCustom','inputs':{
            'model':old['low_res_model'],'add_noise':True,'noise_seed':31,'cfg':old['cfg'],
            **{key:old[key] for key in ('positive','negative','sampler','sigmas','latent_image')}}}
    return value


class RemoteKGraphTests(unittest.TestCase):
    def test_freeze_and_submit_keep_k_recipe_and_project_audio_clock(self):
        original=source();before=copy.deepcopy(original)
        frozen=sampling.freeze(original)
        plan={'id':'a'*32,'config':{'rh_sampling':frozen,'seed':999,'megapixels':1.,
              'talking_profile':'ref2va-v1','person_mask_mode':'none'}}
        rows=project.clock(277000,[141])
        uploaded={'attempt':'test','audio':'audio.wav','references':['image.png'],'previous_latent':'previous.latent'}
        for i,row in enumerate(rows):
            value=graph.build(plan,row,uploaded,rows[i-1] if i else None)
            sampler=value['270']
            self.assertEqual(sampler['class_type'],'SamplerCustom')
            self.assertEqual(sampler['inputs']['noise_seed'],999)
            self.assertTrue(sampler['inputs']['add_noise'])
            self.assertFalse({'lowres_scale','transition_step','upscaler_model','seed'} & sampler['inputs'].keys())
            self.assertEqual(value['249']['inputs']['value'],0)
            self.assertEqual(value['268']['inputs']['length'],row['generation_frames'])
            audio=value['268']['inputs']['ref_audios.ref_audio_0']
            self.assertEqual(value[audio[0]]['inputs']['audio'],['9000',0])
            self.assertEqual(audio[1],3)
        self.assertEqual(sampling.summary(frozen)['sampler_class'],'SamplerCustom')
        self.assertEqual(original,before)

    def test_mixed_sampler_or_bad_audio_mask_is_rejected(self):
        value=source();value['extra']=source(False)['244']
        with self.assertRaisesRegex(ValueError,'采样器'):sampling.freeze(value)
        value=source();value['247']['inputs']['value']=1
        with self.assertRaisesRegex(ValueError,'全零'):sampling.freeze(value)

    def test_transport_binds_native_k_noise_seed_and_retains_avatar_seed(self):
        cfg={'base_url':'https://www.runninghub.ai','workflow_id':'123','instance_type':'ultra','seed':999}
        with patch.dict('os.environ',{'RUNNINGHUB_API_KEY':'test-only'}):client=remote.Client(cfg)
        for kind,field in [('SamplerCustom','noise_seed'),('SelfLiftAvatarH3Sampler','seed')]:
            with patch.object(client,'_post',return_value={'code':0,'data':{'taskId':'77'}}) as post:
                client.create({'270':{'class_type':kind,'inputs':{field:999}}})
            self.assertEqual(post.call_args.args[1]['nodeInfoList'],[{'nodeId':'270','fieldName':field,'fieldValue':999}])


class PrefixFileTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.a=self.root/'previous.latent';self.b=self.root/'raw.latent';self.c=self.root/'carry.latent'
        self.previous=np.random.default_rng(1).normal(.2,.1,(1,24,16,2,2)).astype('float32')
        self.current=np.ones((1,24,15,2,2),'float32')
        self.current[:,:,:12]=self.previous[:,:,2:14]+np.float32(1e-7)
        save_file({'latent_tensor':self.previous},str(self.a))
        self.write_raw()

    def write_raw(self):
        save_file({'latent_tensor':self.current,'latent_format_version_0':np.zeros(0,'float32')},str(self.b),metadata={'test':'preserved'})

    def test_checked_carry_preserves_raw_suffix_metadata_and_resume(self):
        from safetensors import safe_open
        raw=self.b.read_bytes()
        target,proof=prefix.canonicalize(self.a,self.b,self.c,2)
        result=load_file(str(target))
        self.assertTrue(proof['restored'])
        self.assertTrue(np.array_equal(result['latent_tensor'][:,:,:12],self.previous[:,:,2:14]))
        self.assertTrue(np.array_equal(result['latent_tensor'][:,:,12:],self.current[:,:,12:]))
        self.assertEqual(self.b.read_bytes(),raw)
        self.assertTrue(runner.validate_prefix(self.a,target,2)['bit_exact'])
        with safe_open(str(target),framework='np') as stream:self.assertEqual(stream.metadata(),{'test':'preserved'})
        saved=self.c.read_bytes()
        prefix.canonicalize(self.a,self.b,self.c,2)
        self.assertEqual(self.c.read_bytes(),saved)

    def test_exact_raw_does_not_create_a_duplicate_file(self):
        self.current[:,:,:12]=self.previous[:,:,2:14];self.write_raw()
        target,proof=prefix.canonicalize(self.a,self.b,self.c,2)
        self.assertEqual(target,self.b);self.assertFalse(proof['restored']);self.assertFalse(self.c.exists())

    def test_meaningful_damage_or_nonfinite_output_is_rejected(self):
        for index,value in [((0,0,0,0,0),.01),((0,0,14,0,0),np.nan)]:
            with self.subTest(index=index):
                before=self.current.copy();self.current[index]+=value;self.write_raw()
                with self.assertRaisesRegex(ValueError,'浮点往返'):prefix.canonicalize(self.a,self.b,self.c,2)
                self.assertFalse(self.c.exists());self.current=before

    def test_tampered_carry_is_not_overwritten(self):
        prefix.canonicalize(self.a,self.b,self.c,2)
        self.c.write_bytes(self.b.read_bytes())
        before=self.c.read_bytes()
        with self.assertRaisesRegex(ValueError,'缓存已变化'):prefix.canonicalize(self.a,self.b,self.c,2)
        self.assertEqual(self.c.read_bytes(),before)

    def test_output_cannot_replace_raw_or_previous(self):
        for target in (self.a,self.b):
            with self.assertRaisesRegex(ValueError,'不能覆盖'):prefix.canonicalize(self.a,self.b,target,2)

    def test_failed_atomic_publish_removes_only_its_partial(self):
        unrelated=self.root/'.old.part';unrelated.write_bytes(b'keep')
        with patch.object(Path,'replace',side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(PermissionError,'denied'):prefix.canonicalize(self.a,self.b,self.c,2)
        self.assertEqual(set(self.root.iterdir()),{self.a,self.b,unrelated})


class KClient:
    def __init__(self,error=1e-7):self.created=[];self.tensors={};self.error=error
    def upload(self,path):return str(path)
    def create(self,value):
        self.created.append(value);task=str(len(self.created))
        tensor=np.ones((1,24,52,2,2),'float32')*.25
        if '9111' in value:
            previous=load_file(value['9110']['inputs']['latent'])['latent_tensor']
            tail=value['9111']['inputs']['index']
            tensor[:,:,:12]=previous[:,:,tail:tail+12]+np.float32(self.error)
        self.tensors[task]=tensor
        return task
    def query(self,task):
        return {'status':'SUCCESS','results':[{'nodeId':node,'outputType':kind,'url':f'https://example.test/{task}/{kind}'}
                 for node,kind in [('273','mp4'),('9200','latent')]]}
    def download(self,url,target):
        if Path(target).exists():return
        if url.endswith('latent'):save_file({'latent_tensor':self.tensors[url.split('/')[-2]]},str(target))
        else:Path(target).write_bytes(url.encode())


class RemoteKLifecycleTests(unittest.TestCase):
    def make_plan(self,root,k=True,samples=277000):
        cfg={'base_url':'https://www.runninghub.ai','workflow_id':'123','instance_type':'ultra',
             'megapixels':1.,'seed':999,'first_seconds':6.,'segment_seconds':5.667,
             'template_sha256':project.digest(graph.TEMPLATE),'person_mask_mode':'none',
             'talking_profile':'ref2va-v1','rh_sampling':sampling.freeze(source(k))}
        p=project.prepare(root,np.zeros((samples,1),'float32'),24000,[Image.new('RGB',(32,64))],None,cfg,'test')
        p.update(approved=True,approved_fingerprint=project.fingerprint(p));project.write_plan(root,p)
        return p

    def run_plan(self,root,p,client):
        def assemble(root,pid):
            path=project.project_path(root,pid)/'final.mp4';path.write_bytes(b'final');return str(path)
        runner.run_project(root,p['id'],client,validate=lambda *args:{},assemble=assemble,sleep=lambda _:None)

    def test_real_controller_keeps_raw_and_resumes_canonical_carry_without_new_task(self):
        with tempfile.TemporaryDirectory() as value:
            root=Path(value);p=self.make_plan(root);client=KClient()
            self.run_plan(root,p,client)
            saved=project.read_plan(root,p['id']);job=saved['segments'][1]['job']
            self.assertNotEqual(job['latent'],job['raw_latent'])
            self.assertEqual(project.digest(job['raw_latent']),job['raw_latent_sha256'])
            self.assertTrue(runner.validate_prefix(saved['segments'][0]['job']['latent'],job['latent'],saved['segments'][0]['tail_token_start'])['bit_exact'])
            self.run_plan(root,p,client);self.assertEqual(len(client.created),2)
            Path(job['raw_latent']).write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'文件已变化'):self.run_plan(root,p,client)
            self.assertEqual(len(client.created),2)

    def test_bad_k_prefix_stops_before_further_cloud_submission(self):
        with tempfile.TemporaryDirectory() as value:
            root=Path(value);p=self.make_plan(root,samples=400000);client=KClient(.01)
            with self.assertRaisesRegex(ValueError,'浮点往返'):self.run_plan(root,p,client)
            self.assertEqual(len(client.created),2)

    def test_selflift_still_requires_an_exact_raw_prefix(self):
        with tempfile.TemporaryDirectory() as value:
            root=Path(value);p=self.make_plan(root,k=False);client=KClient()
            with self.assertRaisesRegex(ValueError,'实际输出的续接前缀'):self.run_plan(root,p,client)
            self.assertFalse(list(root.rglob('carry.latent')))


if __name__=='__main__':unittest.main()
