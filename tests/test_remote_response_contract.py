"""Unexpected provider JSON must not turn into an ambiguous attribute error."""
import importlib
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

import test_materials as fixtures

remote=importlib.import_module('h3lv_test.talking_rh.remote')


class RemoteResponseContractTests(unittest.TestCase):
    setUp=fixtures.MaterialTests.setUp

    def client(self):
        with patch.object(remote,'api_key',return_value='fixture-only'):
            return remote.Client({'base_url':'https://www.runninghub.ai','workflow_id':'123','instance_type':'default','seed':1})

    def response(self,value):
        return Mock(json=Mock(return_value=value))

    def test_query_rejects_nonobject_json_after_one_request(self):
        for value in ([],None,'unexpected',1):
            with self.subTest(value=value),patch.object(remote.requests,'post',return_value=self.response(value)) as post:
                with self.assertRaisesRegex(remote.RemoteError,'返回格式无效'):self.client().query('123')
                post.assert_called_once()

    def test_upload_rejects_invalid_shapes_and_nontext_filenames(self):
        path=self.directory/'audio.wav';path.write_bytes(b'fixture')
        for value in ([],None,{'code':0,'data':[]},{'code':0,'data':{'filename':123}}):
            with self.subTest(value=value),patch.object(remote.requests,'post',return_value=self.response(value)) as post:
                with self.assertRaises(remote.RemoteError):self.client().upload(path)
                post.assert_called_once()

    def test_malformed_successful_create_stays_unknown_not_rejected(self):
        for value in ({'code':0,'data':[]},{'code':0,'data':None}):
            with self.subTest(value=value),patch.object(remote.requests,'post',return_value=self.response(value)) as post:
                with self.assertRaises(remote.RemoteError) as caught:self.client().create({})
                self.assertNotIsInstance(caught.exception,remote.Rejected)
                self.assertIn('结果未知',str(caught.exception));post.assert_called_once()
        with patch.object(remote.requests,'post',return_value=self.response({'code':1,'data':[]})):
            with self.assertRaises(remote.Rejected):self.client().create({})

    def test_workflow_rejects_nonobject_prompt_without_creating_a_task(self):
        for data in ([],{'prompt':'[]'},{'prompt':'null'},{'prompt':'"wrong"'}):
            with self.subTest(data=data),patch.object(remote.requests,'post',return_value=self.response({'code':0,'data':data})) as post:
                with self.assertRaises(remote.RemoteError):self.client().workflow()
                post.assert_called_once()
                self.assertTrue(post.call_args.args[0].endswith('/api/openapi/getJsonApiFormat'))

    def test_valid_provider_objects_keep_existing_contract(self):
        client=self.client()
        for value,method,expected in (({'status':'SUCCESS'},lambda:client.query('123'),{'status':'SUCCESS'}),
            ({'code':0,'data':{'taskId':'12345'}},lambda:client.create({}),'12345'),
            ({'code':0,'data':{'prompt':'{"1":{"class_type":"LoadAudio"}}'}},client.workflow,{'1':{'class_type':'LoadAudio'}})):
            with patch.object(remote.requests,'post',return_value=self.response(value)):
                self.assertEqual(method(),expected)


if __name__=='__main__':unittest.main()
