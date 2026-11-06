import importlib
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch, MagicMock

from test_diagnostics import diagnostics

storage = importlib.import_module('h3lv_test.storage')
local = importlib.import_module('h3lv_test.talking_rh.local')
runner = importlib.import_module('h3lv_test.talking_rh.runner')


class OutputDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.output = Path(temp.name)
        paths = types.SimpleNamespace(get_output_directory=lambda: str(self.output))
        mock = patch.dict('sys.modules', {'folder_paths': paths});mock.start();self.addCleanup(mock.stop)
        mock = patch.object(diagnostics, 'configured_key', return_value='private-test-key');mock.start();self.addCleanup(mock.stop)

    def test_layout_matches_and_task_history_stays_readable(self):
        for prefix in (storage.MV_PREFIX, storage.TALKING_PREFIX):
            bucket = storage.ensure_layout(prefix)
            self.assertEqual({p.name for p in bucket.iterdir()}, {'projects','final_videos','logs'})
        client = local.LocalClient({},server=object(),input_root=self.output/'input',output_root=self.output)
        task = client.reserve()
        old = self.output/storage.TALKING_PREFIX/'local_tasks'/f'{task}.json'
        old.parent.mkdir()
        old.write_text(json.dumps({'status':'SUCCESS','result':{'status':'SUCCESS','results':[]}}))
        self.assertEqual(client.query(task)['status'], 'SUCCESS')
        new = client.reserve();client.save(new,{'status':'PENDING'})
        self.assertEqual(client.record(new).parent,self.output/storage.TALKING_PREFIX/'projects/_local_tasks')
        self.assertTrue(old.exists())

    def test_logs_are_separate_and_report_combines_redacted_errors(self):
        diagnostics.record_error('mv-test',ValueError('MV error'))
        diagnostics.record_error('talk-test',ValueError('private-test-key Bearer token-secret'),workflow='口播',task_id='task-123')
        self.assertNotIn('talk-test',diagnostics.download_log().decode())
        self.assertNotIn('mv-test',diagnostics.download_log('口播').decode())
        report=diagnostics.download_report().decode()
        self.assertIn('mv-test',report);self.assertIn('talk-test',report);self.assertIn('task-123',report)
        self.assertNotIn('private-test-key',report);self.assertNotIn('token-secret',report)
        self.assertIn('python',json.loads(report.splitlines()[0]))

    def test_native_talking_failure_records_node_without_inputs(self):
        history={'status':{'status_str':'error','messages':[['execution_error',{
            'node_id':'270','node_type':'SamplerCustom','exception_message':'CUDA out of memory private-test-key',
            'traceback':['sample.py:123'],'current_inputs':{'prompt':'PRIVATE-PROMPT'}}]]}}
        queue=types.SimpleNamespace(get_history=lambda prompt_id:{prompt_id:history})
        client=local.LocalClient({},server=types.SimpleNamespace(prompt_queue=queue),input_root=self.output/'input',output_root=self.output)
        task=client.reserve();client.save(task,{'status':'PENDING'})
        self.assertEqual(client.query(task)['status'],'FAILED')
        self.assertEqual(client.query(task)['status'],'FAILED')
        entries=diagnostics.download_log('口播').decode().splitlines()
        self.assertEqual(len(entries),1)
        item=json.loads(entries[0]);self.assertEqual(item['node_id'],'270');self.assertEqual(item['prompt_id'],task)
        self.assertNotIn('PRIVATE-PROMPT',entries[0]);self.assertNotIn('private-test-key',entries[0])

    def test_background_failure_is_logged_and_original_error_preserved(self):
        done=threading.Event()
        plan={'id':'a'*32,'approved':True,'approved_fingerprint':'same','config':{'backend':'local'},
              'run_status':'draft','segments':[{'job':None}], 'progress':{'index':0}}
        def fail_generation(*args):
            plan['progress']={'index':0}
            raise RuntimeError('worker private-test-key')
        def change(_root,_pid,fn):
            fn(plan);done.set()
        with patch.object(runner,'claim',return_value=MagicMock()),patch.object(runner,'read_plan',return_value=plan), \
             patch.object(runner,'fingerprint',return_value='same'),patch.object(runner,'verify_assets'), \
             patch.object(runner,'write_plan'),patch.object(runner,'change',side_effect=change), \
             patch.object(runner,'configured_key',return_value='private-test-key'), \
             patch.object(runner,'run_project',side_effect=fail_generation):
            runner.start(self.output/'projects',plan['id'],client_factory=lambda config:types.SimpleNamespace())
            self.assertTrue(done.wait(3))
        self.assertEqual(plan['run_status'],'failed');self.assertNotIn('private-test-key',plan['error'])
        event=json.loads(diagnostics.download_log('口播').decode().splitlines()[0])
        self.assertEqual(event['project_id'],plan['id']);self.assertEqual(event['stage'],'talking_generation')

    def test_prepare_node_failure_is_downloadable(self):
        node=importlib.import_module('h3lv_test.talking_rh')
        with patch('h3lv_test.nodes.audio_array',side_effect=ValueError('invalid audio')):
            with self.assertRaisesRegex(ValueError,'invalid audio'):
                node.TalkingAvatar().prepare(None,None)
        event=json.loads(diagnostics.download_log('口播').decode().splitlines()[0])
        self.assertEqual(event['stage'],'talking_prepare')

    def test_public_node_stale_output_link_is_logged_once(self):
        node=importlib.import_module('h3lv_test.talking_rh')
        with self.assertRaisesRegex(ValueError,'接线已过期'):
            node.TalkingAvatar().prepare(None,None,unique_id='5',prompt={'9':{'inputs':{'audio':['5',999]}}})
        events=diagnostics.download_log('口播').decode().splitlines()
        self.assertEqual(len(events),1);self.assertEqual(json.loads(events[0])['stage'],'talking_prepare')


class DiagnosticEndpointsTests(unittest.IsolatedAsyncioTestCase):
    async def test_talking_api_frontend_and_combined_download(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer
        mv=importlib.import_module('h3lv_test.routes')
        talk=importlib.import_module('h3lv_test.talking_rh.routes')
        with tempfile.TemporaryDirectory() as directory:
            server=types.SimpleNamespace(routes=web.RouteTableDef())
            with patch.dict('sys.modules',{'server':types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=server)),
                   'folder_paths':types.SimpleNamespace(get_output_directory=lambda:directory)}), \
                 patch.object(diagnostics,'configured_key',return_value='private-test-key'):
                mv.register_routes();talk.register()
                app=web.Application();app.add_routes(server.routes)
                async with TestClient(TestServer(app)) as client:
                    with patch.object(talk,'check_rh',side_effect=ValueError('RH timeout private-test-key')):
                        response=await client.post('/h3talk/rh-check',json={})
                        self.assertEqual(response.status,400)
                        self.assertNotIn('private-test-key',await response.text())
                    response=await client.post('/h3lv/logs/frontend',json={'workflow':'口播','message':'panel error','project_id':'test'})
                    self.assertEqual(response.status,200)
                    response=await client.get('/h3lv/logs/errors');text=await response.text()
                    self.assertIn('attachment',response.headers['Content-Disposition'])
                    self.assertIn('RH timeout',text);self.assertIn('panel error',text)
                    self.assertNotIn('private-test-key',text)
                    events=[json.loads(x) for x in diagnostics.download_log('口播').decode().splitlines()]
                    self.assertEqual([x['stage'] for x in events],['talking_api','frontend'])
