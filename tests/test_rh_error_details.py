import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import requests
import copy
import numpy as np
from test_talking_rh import remote

diagnostics = importlib.import_module('h3talk_test.diagnostics')
errors = importlib.import_module('h3talk_test.workflow_errors')


class RHErrorDetailsTests(unittest.TestCase):
    def setUp(self):
        with patch.object(remote, 'api_key', return_value='private-fixture-key'):
            self.client = remote.Client({'base_url':'https://www.runninghub.ai',
                'workflow_id':'123', 'instance_type':'plus', 'seed':42})

    def test_download_failure_identifies_http_or_timeout_without_signed_url(self):
        for error,label in [(requests.HTTPError('private-signed-url',response=Mock(status_code=403)),'HTTP 403'),
                            (requests.ReadTimeout('private-signed-url'),'ReadTimeout')]:
            with self.subTest(label=label),tempfile.TemporaryDirectory() as temp,\
                 patch.object(remote.requests,'get',side_effect=error):
                with self.assertRaises(remote.RemoteError) as caught:
                    self.client.download('https://example.test/private-signed-url',Path(temp)/'raw.mp4')
                self.assertIn(label,str(caught.exception))
                self.assertNotIn('private-signed-url',str(caught.exception))
                self.assertFalse((Path(temp)/'raw.mp4').exists())

    def test_creation_rejection_reaches_ui_and_log_without_secrets(self):
        reply = {'code':416, 'msg':'TASK_CREATE_FAILED_BY_NOT_ENOUGH_WALLET',
                 'data':{'errorMessage':'余额不足 private-fixture-key https://example.test/private',
                         'apiKey':'NEVER-STORE', 'workflow':'PRIVATE-GRAPH'}}
        with patch.object(self.client, '_post', return_value=reply) as post:
            with self.assertRaises(remote.Rejected) as caught:
                self.client.create({})
        post.assert_called_once()
        message = str(caught.exception)
        self.assertIn('416', message)
        self.assertIn('TASK_CREATE_FAILED_BY_NOT_ENOUGH_WALLET', message)
        self.assertIn('余额不足', message)
        for secret in ('private-fixture-key', 'NEVER-STORE', 'PRIVATE-GRAPH', 'https://example.test/private'):
            self.assertNotIn(secret, message)
        report = errors.failure_report(caught.exception, 'rh_segment_generation', 0)
        self.assertIn('416', report['reason'])
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(sys.modules, folder_paths=types.SimpleNamespace(get_output_directory=lambda:temp)):
                self.assertTrue(diagnostics.record_error('rh_segment_generation', caught.exception))
                log = diagnostics.error_log_path().read_text(encoding='utf-8')
                self.assertIn('TASK_CREATE_FAILED_BY_NOT_ENOUGH_WALLET', log)
                self.assertNotIn('private-fixture-key', log)

    def test_http_error_retains_status_and_provider_message_without_retrying_create(self):
        response = Mock(status_code=429)
        response.json.return_value = {'code':429, 'msg':'TASK_QUEUE_MAXED'}
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with patch.object(remote.requests, 'post', return_value=response) as post:
            with self.assertRaises(remote.TransientRemoteError) as caught:
                self.client.create({})
        post.assert_called_once()
        self.assertIn('HTTP 429', str(caught.exception))
        self.assertIn('TASK_QUEUE_MAXED', str(caught.exception))

    def test_node_errors_are_bounded_and_inputs_omitted(self):
        reply = {'code':433, 'msg':'PROMPT_ERROR', 'data':{'promptTips':json.dumps({
            'node_errors':{'127':{'class_type':'UNETLoader','errors':[{
                'type':'value_not_in_list','message':'Value not in list',
                'details':'unet_name missing.safetensors',
                'extra_info':{'input_name':'unet_name','input_value':'PRIVATE-INPUT'}}]}}})}}
        with patch.object(self.client, '_post', return_value=reply):
            with self.assertRaises(remote.Rejected) as caught:self.client.create({})
        message=str(caught.exception)
        for value in ('127', 'UNETLoader', 'missing.safetensors'):self.assertIn(value,message)
        self.assertNotIn('PRIVATE-INPUT', message)
        with patch.object(self.client,'_post',return_value={'code':1,'msg':'x'*10000}):
            with self.assertRaises(remote.Rejected) as caught:self.client.create({})
        self.assertLess(len(str(caught.exception)),2500)

    def test_success_and_unknown_creation_contract_unchanged(self):
        with patch.object(self.client,'_post',return_value={'code':0,'data':{'taskId':'456'}}):
            self.assertEqual(self.client.create({}),'456')
        with patch.object(self.client,'_post',return_value={'code':0,'data':{}}):
            with self.assertRaises(remote.RemoteError) as caught:self.client.create({})
        self.assertNotIsInstance(caught.exception,remote.Rejected)

    def test_json_encoded_msg_extracts_node_error_without_input_metadata(self):
        reply={'code':433,'msg':json.dumps({'error':{'type':'prompt_outputs_failed_validation'},
            'node_errors':{'272':{'class_type':'CreateVideo','errors':[{
                'type':'value_not_in_list','details':"chroma_subsampling: 'none' not in ['auto', '444', '422', '420']",
                'extra_info':{'received_value':'PRIVATE-INPUT'}}]}}})}
        with patch.object(self.client,'_post',return_value=reply):
            with self.assertRaises(remote.Rejected) as caught:self.client.create({})
        message=str(caught.exception)
        for value in ('433','272','CreateVideo','chroma_subsampling'):self.assertIn(value,message)
        self.assertNotIn('PRIVATE-INPUT',message)
        self.assertNotIn('node_errors',message)

    def test_mv_build_translates_legacy_video_auto_value_without_changing_snapshot(self):
        mv=importlib.import_module('h3talk_test.mv_rh')
        nodes=importlib.import_module('h3talk_test.nodes')
        progress=importlib.import_module('h3talk_test.progress')
        graph={str(n):{'class_type':kind,'inputs':{}} for n,kind in
               [(10,'LoadAudio'),(11,'LoadAudio'),(20,'MiniMaxH3ReferenceToVideo'),(272,'CreateVideo'),(273,'SaveVideo')]}
        mapping={'original':'10','vocals':'11','reference':'20','creator':'272','output':'273'}
        audio={'waveform':[types.SimpleNamespace(numpy=lambda:np.zeros((1,1000),dtype=np.float32))],'sample_rate':24000}
        loaded=[audio,audio,141,'prefix',{'visual_type':'performance','paths':[]}]+[None]*6+['']
        for value in ('none','auto','444','422','420'):
            graph['272']['inputs']={'chroma_subsampling':value,'codec':'none'}
            snapshot={'remote_graph':graph,'binding':mapping,'prompt_source':{'kind':'literal','text':'test'}}
            before=copy.deepcopy(snapshot)
            with tempfile.TemporaryDirectory() as temp,patch.object(nodes.LoadSegment,'load',return_value=loaded),patch.object(progress,'report'):
                actual,_=mv.build_segment(Path(temp),'a'*32,0,snapshot,Mock(upload=Mock(return_value='uploaded.wav')),Path(temp))
            self.assertEqual(actual['272']['inputs']['chroma_subsampling'],'auto' if value=='none' else value)
            self.assertEqual(actual['272']['inputs']['codec'],'none')
            self.assertEqual(snapshot,before)


if __name__ == '__main__':unittest.main()
