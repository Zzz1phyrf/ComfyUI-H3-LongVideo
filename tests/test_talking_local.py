import asyncio
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from test_talking_rh import project, graph, runner, FakeClient

local=importlib.import_module('h3talk_test.talking_rh.local')
node=importlib.import_module('h3talk_test.talking_rh')


class Queue:
    def __init__(self):self.items=[];self.history={}
    def put(self,item):self.items.append(item)
    def get_history(self,prompt_id):return {prompt_id:self.history[prompt_id]} if prompt_id in self.history else {}
    def get_current_queue(self):return [],self.items


class LocalBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.loop=asyncio.new_event_loop()
        self.thread=threading.Thread(target=self.loop.run_forever);self.thread.start()
        self.addCleanup(self.finish_loop)
        self.queue=Queue();self.server=types.SimpleNamespace(loop=self.loop,number=0,prompt_queue=self.queue)
        self.valid=True
        async def validate(*_):return (self.valid,{'error':'missing test node'},['273','9200'],{})
        self.client=local.LocalClient({},server=self.server,input_root=self.base/'input',output_root=self.base/'output',validate=validate)

    def finish_loop(self):
        self.loop.call_soon_threadsafe(self.loop.stop);self.thread.join(timeout=5);self.loop.close()

    def example_graph(self):
        return {'273':{'class_type':'SaveVideo','inputs':{'filename_prefix':'H3TalkingRH/p/0/attempt/raw'}},
                '9200':{'class_type':'SaveLatent','inputs':{'filename_prefix':'H3TalkingRH/p/0/attempt/video'}}}

    def test_native_queue_and_collection_work_without_key_or_http(self):
        with patch.dict(os.environ,{},clear=True),patch('requests.post',side_effect=AssertionError('Local must not call HTTP')):
            task_id=self.client.reserve();self.client.create(self.example_graph(),task_id)
            self.assertEqual(self.queue.items[0][1],task_id)
            self.assertEqual(self.client.query(task_id)['status'],'RUNNING')
            folder=self.client.output/'H3TalkingRH/p/0/attempt';folder.mkdir(parents=True)
            outputs={}
            for number,name,key in [('273','raw.mp4','images'),('9200','video.latent','latents')]:
                (folder/name).write_bytes(name.encode())
                outputs[number]={key:[{'filename':name,'subfolder':'H3TalkingRH/p/0/attempt','type':'output'}]}
            self.queue.history[task_id]={'status':{'status_str':'success'},'outputs':outputs}
            result=self.client.query(task_id)
            self.assertEqual(result['status'],'SUCCESS')
            target=self.base/'collected.mp4';self.client.download(result['results'][0]['url'],target)
            self.assertEqual(target.read_bytes(),b'raw.mp4')
            self.queue.history.clear();self.queue.items.clear()
            self.assertEqual(self.client.query(task_id),result)

    def test_local_rejection_never_enqueues_and_missing_does_not_resubmit(self):
        self.valid=False;task_id=self.client.reserve()
        with self.assertRaises(local.Rejected):self.client.create(self.example_graph(),task_id)
        self.assertEqual(self.queue.items,[])
        self.assertEqual(self.client.query(task_id),{'status':'MISSING'})
        self.assertEqual(self.queue.items,[])

    def test_local_inputs_are_content_addressed_and_reject_external_outputs(self):
        source=self.base/'a.latent';source.write_bytes(b'latents')
        first=self.client.upload(source);second=self.client.upload(source)
        self.assertEqual(first,second)
        self.assertEqual((self.client.input/first).read_bytes(),source.read_bytes())
        with self.assertRaises(ValueError):self.client.download(str(source),self.base/'b.latent')

    def test_completion_between_queue_and_history_reads_is_not_reported_missing(self):
        task_id=self.client.reserve();self.client.create(self.example_graph(),task_id)
        folder=self.client.output/'H3TalkingRH/p/0/attempt';folder.mkdir(parents=True)
        outputs={}
        for number,name,key in [('273','raw.mp4','images'),('9200','video.latent','latents')]:
            (folder/name).write_bytes(name.encode())
            outputs[number]={key:[{'filename':name,'subfolder':'H3TalkingRH/p/0/attempt','type':'output'}]}
        def finished():
            self.queue.history[task_id]={'status':{'status_str':'success'},'outputs':outputs}
            return [],[]
        self.queue.get_current_queue=finished
        self.assertEqual(self.client.query(task_id)['status'],'SUCCESS')


class DualModeNodeTests(unittest.TestCase):
    def test_static_mask_broadcasts_to_every_latent_frame_without_changing_samples(self):
        import torch
        original=torch.randn(2,24,52,4,3)
        mask=torch.linspace(0,1,12).reshape(1,4,3)
        result=local.StaticVideoMask().apply({'samples':original},mask)[0]
        self.assertIs(result['samples'],original)
        self.assertEqual(tuple(result['noise_mask'].shape),(2,1,52,4,3))
        self.assertTrue(torch.equal(result['noise_mask'][1,0,51],mask[0]))
        result['noise_mask'][:,:,0]=0
        self.assertTrue(torch.equal(result['noise_mask'][0,0,1],mask[0]))

    def test_new_ui_defaults_local_and_model_configuration_does_not_need_rh(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            resource=base/'weight';resource.write_bytes(b'weight')
            folders=types.SimpleNamespace(get_output_directory=lambda:str(base),get_filename_list=lambda _:['minimax.safetensors'],
                                         get_full_path=lambda category,name:str(resource) if name=='minimax.safetensors' else None)
            import torch
            with patch.dict('sys.modules',folder_paths=folders),patch.dict(os.environ,{},clear=True):
                self.assertEqual(node.TalkingRH.INPUT_TYPES()['optional']['backend'][0][0],'本地')
                kwargs={field:'minimax.safetensors' for field in local.MODEL_FIELDS}
                mask=torch.zeros(1,64,32);mask[:,8:50,5:26]=1
                value=node.TalkingRH().prepare({'waveform':torch.zeros(1,1,24000),'sample_rate':24000},
                         torch.zeros(1,64,32,3),mask,workflow_id='',backend='本地',**kwargs)
                pid=value['result'][0];p=project.read_plan(node.root(),pid)
                self.assertEqual(p['config']['backend'],'local')
                self.assertNotIn('workflow_id',p['config']);self.assertNotIn('base_url',p['config'])
                self.assertFalse(p['approved'])

    def test_shared_graph_changes_only_explicit_local_model_fields(self):
        config={'megapixels':1.0,'seed':999,'backend':'local',
                'models':{**{f:'selected-'+f for f in local.MODEL_FIELDS},'local_lora':'selected-lora'}}
        p={'id':'a'*32,'config':config};r=project.clock(277000,[141])[0]
        value=graph.build(p,r,{'audio':'a','references':['i'],'mask':'m','attempt':'test'})
        self.assertEqual(value['270']['class_type'],'SelfLiftAvatarH3Sampler')
        self.assertEqual(value['249']['inputs']['value'],0)
        self.assertEqual(value['127']['inputs']['unet_name'],'selected-local_model')
        self.assertEqual(value['242']['inputs']['model'],['9300',0])
        self.assertEqual(value['9300']['inputs']['lora_name'],'selected-lora')

    def test_local_orchestration_reserves_id_before_enqueue_and_never_calls_rh(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'projects'
            cfg={'backend':'local','models':{f:'selected-'+f for f in local.MODEL_FIELDS},'megapixels':.6,'seed':999,'first_seconds':6.,'segment_seconds':5.667,
                 'template_sha256':project.digest(graph.TEMPLATE)}
            mask=np.zeros((64,32),dtype='float32');mask[8:50,5:26]=1
            p=project.prepare(root,np.zeros((277000,1),dtype='float32'),24000,[Image.new('RGB',(32,64))],mask,cfg,'input')
            p=project.update_cuts(root,p['id'],[5.875]);p.update(approved=True,approved_fingerprint=project.fingerprint(p));project.write_plan(root,p)
            class Client(FakeClient):
                def reserve(self):return 'known-'+str(len(self.created))
                def create(self,value,task_id):
                    saved=project.read_plan(root,p['id'])['segments'][len(self.created)]['job']
                    self.test.assertEqual(saved['task_id'],task_id)
                    self.test.assertEqual(saved['status'],'creating_unknown')
                    self.created.append(value);return task_id
            client=Client();client.test=self
            with patch.object(runner,'Client',side_effect=AssertionError('No RH backend')):
                runner.run_project(root,p['id'],client,validate=lambda *_:{},assemble=lambda *_:'final',sleep=lambda _:None)
            self.assertEqual(len(client.created),2)
            self.assertEqual(project.read_plan(root,p['id'])['run_status'],'completed')


if __name__=='__main__':unittest.main()
