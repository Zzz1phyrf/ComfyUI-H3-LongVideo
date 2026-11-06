import importlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from test_talking_overlap import fixture
from test_talking_rh import runner

routes=importlib.import_module('h3talk_test.talking_rh.routes')

class ControlsTests(unittest.TestCase):
    def test_rh_check_reads_workflow_without_upload_or_submission(self):
        client=Mock();client.workflow.return_value=fixture()
        with patch('h3talk_test.talking_rh.remote.Client',return_value=client):
            result=routes.check_rh({'region':'海外版','workflow_id':'123','instance_type':'ultra'})
        self.assertFalse(result['submitted'])
        client.upload.assert_not_called();client.create.assert_not_called()

    def test_list_only_exposes_talking_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            for pid in ('a'*32,'b'*32):
                (directory/pid/'state').mkdir(parents=True)
                (directory/pid/'state/segments.json').touch()
            def read(root,pid):
                return {'kind':'talking_rh' if pid[0]=='a' else 'mv','created':123,'duration':40,
                        'segments':[{}],'run_status':'completed','config':{'private':'not exposed'}}
            with patch.object(routes,'root',return_value=directory),patch.object(routes,'read_plan',side_effect=read):
                result=routes.project_list()
            self.assertEqual(len(result),1);self.assertNotIn('config',result[0])

    def test_repeated_stage_keeps_elapsed_clock(self):
        plan={}
        with patch.object(runner,'change',side_effect=lambda root,pid,fn:fn(plan)),patch.object(runner.time,'time',side_effect=[100,200]):
            runner.report(None,'id','running',0)
            runner.report(None,'id','running',0)
            self.assertEqual(plan['progress']['since'],100)
            runner.report(None,'id','download',0)
            self.assertEqual(plan['progress']['since'],200)
