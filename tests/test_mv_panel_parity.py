import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_plugin import core, controller, routes, sample_plan


class PanelParityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = sample_plan()
        self.plan.update(approved=True, run_status='completed', final_video='old-final.mp4')
        self.plan['approved_fingerprint'] = core.fingerprint(self.plan)
        for index, row in enumerate(self.plan['segments']):
            row['job'] = {'backend': 'runninghub', 'status': 'completed', 'task_id': str(100+index), 'video': f'old-{index}.mp4'}
        self.directory = core.project_path(self.root, self.plan['id'])
        self.directory.mkdir()
        core.write_plan(self.root, self.plan)

    async def invoke(self, action, body):
        handlers = {}
        class Router:
            def get(self, path): return self.capture('GET', path)
            def post(self, path): return self.capture('POST', path)
            def capture(self, method, path):
                def save(fn): handlers[method, path] = fn; return fn
                return save
        server = types.SimpleNamespace(routes=Router())
        with patch.dict(sys.modules, server=types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=server))), \
             patch.object(routes, 'data_root', return_value=self.root), \
             patch.object(controller, 'queued_ids', return_value=set()), \
             patch.object(routes, 'record_error'), \
             patch.object(controller, 'start', side_effect=AssertionError('Must not submit generation')):
            routes.register_routes()
            request = types.SimpleNamespace(match_info={'project_id': self.plan['id']}, json=AsyncMock(return_value=body))
            return await handlers['POST', '/h3lv/project/{project_id}/'+action](request)

    async def test_mark_redo_keeps_old_take_and_other_segments_without_starting(self):
        response = await self.invoke('retry', {'index': 1})
        self.assertEqual(response.status, 200)
        saved = core.read_plan(self.root, self.plan['id'])
        self.assertTrue(saved['segments'][1]['needs_regeneration'])
        self.assertEqual(saved['segments'][1]['job'], self.plan['segments'][1]['job'])
        self.assertEqual(saved['segments'][0], self.plan['segments'][0])
        self.assertEqual(saved['segments'][2], self.plan['segments'][2])
        self.assertTrue(saved['final_stale'])
        self.assertEqual(saved['final_video'], 'old-final.mp4')
        self.assertEqual(saved['approved_fingerprint'], core.fingerprint(saved))

    async def test_pending_task_cannot_be_replaced(self):
        self.plan['segments'][1]['job']['status'] = 'submitted'
        core.write_plan(self.root, self.plan)
        before = (self.directory/'state/segments.json').read_bytes()
        self.assertEqual((await self.invoke('retry', {'index': 1})).status, 400)
        self.assertEqual((self.directory/'state/segments.json').read_bytes(), before)

    async def test_recovery_binds_only_unknown_task_and_does_not_generate(self):
        self.plan['segments'][1]['job'] = {'backend': 'runninghub', 'status': 'creating_unknown'}
        core.write_plan(self.root, self.plan)
        self.assertEqual((await self.invoke('rh-recover', {'index': 1, 'task_id': '123456'})).status, 200)
        saved = core.read_plan(self.root, self.plan['id'])
        self.assertEqual(saved['segments'][1]['job']['task_id'], '123456')
        self.assertEqual(saved['segments'][1]['job']['status'], 'submitted')
        self.assertEqual(saved['segments'][0], self.plan['segments'][0])
        self.assertEqual((await self.invoke('rh-recover', {'index': 0, 'task_id': '999'})).status, 400)

    def test_settings_select_saved_backend_and_never_expose_credentials_or_prompts(self):
        graph = {'1': {'class_type': 'Sampler', 'inputs': {'seed': 123, 'steps': 6, 'api_key': 'SECRET', 'prompt': 'PRIVATE'}}}
        path = self.directory/'state/queue_snapshot.json'
        for remote in (False, True):
            snapshot = {'backend': 'runninghub' if remote else 'local', 'prompt': graph,
                        'remote_graph': {'2': {'class_type': 'RH-Sampler', 'inputs': {'seed': 456}}},
                        'remote': {'workflow_id': '789', 'api_key': 'SECRET'}}
            path.write_text(json.dumps(snapshot), encoding='utf-8')
            before = path.read_bytes()
            value = routes.generation_settings(self.root, self.plan['id'])
            self.assertEqual(value['nodes'][0]['values']['seed'], 456 if remote else 123)
            self.assertEqual(value['backend'], 'RunningHub' if remote else '本地')
            self.assertNotIn('SECRET', json.dumps(value))
            self.assertNotIn('PRIVATE', json.dumps(value))
            self.assertEqual(path.read_bytes(), before)

    def test_settings_without_snapshot_is_explicit(self):
        value = routes.generation_settings(self.root, self.plan['id'])
        self.assertFalse(value['recorded'])
        self.assertEqual(value['nodes'], [])
