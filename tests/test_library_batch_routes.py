import asyncio
import copy
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import test_materials as fixtures

core, controller, expansion = fixtures.core, fixtures.controller, fixtures.expansion
routes = importlib.import_module('h3lv_test.routes')
jobs = importlib.import_module('h3lv_test.expansion_jobs')


def image_bytes(color):
    output = io.BytesIO()
    Image.new('RGB', (20, 20), color).save(output, format='PNG')
    return output.getvalue()


class LibraryBatchRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name).resolve()
        self.root, self.library = self.directory/'projects', self.directory/'library'
        self.stack = ExitStack()
        server = types.SimpleNamespace(routes=web.RouteTableDef())
        paths = types.SimpleNamespace(get_output_directory=lambda: str(self.directory),
                                      get_input_directory=lambda: str(self.directory/'input'))
        for context in [
            patch.dict(sys.modules, {'server': types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=server)),
                                     'folder_paths': paths}),
            patch.object(routes, 'data_root', return_value=self.root),
            patch.object(routes, 'library_root', return_value=self.library),
            patch.object(routes, 'final_root', return_value=self.directory/'finals'),
            patch.object(expansion, 'settings', return_value={'base_url': 'https://fixture.test/v1', 'api_key': 'PRIVATE-ROUTE-KEY'}),
        ]:
            self.stack.enter_context(context)
        routes.register_routes()
        app = web.Application(client_max_size=34*1024*1024)
        app.add_routes(server.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.plan = fixtures.sample_plan()
        self.plan.update(samples=10000, duration=100)
        self.plan['segments'] = [{'start_sample': i*1000, 'end_sample': (i+1)*1000,
            'energy_db': -20, 'text': f'fixture {i}'} for i in range(10)]
        core.decorate(self.plan)
        self.project = core.project_path(self.root, self.plan['id'])
        name = core.store_reference(self.project, 0, 'fixture.png', image_bytes((210, 30, 40)))
        self.plan.update(materials_version=1, default_refs=[name], default_material_note='@图1 为角色参考')
        core.decorate(self.plan)
        for i, row in enumerate(self.plan['segments']):
            row['shot_request'] = f'@图1 为角色参考，本段镜头任务标识 {i}。'
        core.write_plan(self.root, self.plan)
        self.base = f"/h3lv/project/{self.plan['id']}"
        self.payload = dict(revision=1, indices=list(range(10)), mode='vision', model='fixture-model',
            rule='fixture rule', expansion_revision=0, provider='api', concurrency=2)
        self.gates = []

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.set()
        for _ in range(200):
            if not jobs.active(self.plan['id']):
                break
            await asyncio.sleep(.01)
        await self.client.close()
        self.stack.close()
        self.tmp.cleanup()

    async def wait_job(self, job_id):
        for _ in range(500):
            response = await self.client.get(self.base+'/expansion-batches/'+job_id)
            self.assertEqual(response.status, 200)
            value = await response.json()
            if value['state'] not in ('pending', 'running', 'cancelling') and not jobs.active(self.plan['id']):
                return value
            await asyncio.sleep(.01)
        self.fail('Batch did not finish')

    async def start(self, payload=None):
        response = await self.client.post(self.base+'/expansion-batches', json=payload or self.payload)
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def upload(self, name, color, **fields):
        form = FormData()
        form.add_field('image', image_bytes(color), filename=name+'.png', content_type='image/png')
        form.add_field('name', name)
        form.add_field('category', fields.pop('category', 'character'))
        for key, value in fields.items():
            form.add_field(key, str(value))
        response = await self.client.post('/h3lv/library/upload', data=form)
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def test_library_upload_import_trash_restore_and_snapshot_protection(self):
        first = await self.upload('角色A', (240, 10, 20))
        second = await self.upload('舞台B', (10, 30, 200), category='scene')
        selection = [{'asset_id': a['asset']['id'], 'version': 1} for a in (first, second)]
        response = await self.client.post(self.base+'/library/import', json={'revision': 1, 'selection': selection})
        self.assertEqual(response.status, 200, await response.text())
        imported = await response.json()
        self.assertEqual(len(imported['refs']), 2)
        self.assertEqual(core.read_plan(self.root, self.plan['id'])['revision'], 1)
        snapshots = {name: (self.project/'refs'/name).read_bytes() for name in imported['refs']}
        delete = await self.client.post('/h3lv/library/'+first['asset']['id']+'/delete', json={'revision': second['revision']})
        self.assertEqual(delete.status, 200)
        trashed = await delete.json()
        listing = await (await self.client.get('/h3lv/library?deleted=1')).json()
        self.assertEqual([a['id'] for a in listing['assets']], [first['asset']['id']])
        for name, data in snapshots.items():
            self.assertEqual((self.project/'refs'/name).read_bytes(), data)
        restored = await self.client.post('/h3lv/library/'+first['asset']['id']+'/restore', json={'revision': trashed['revision']})
        self.assertEqual(restored.status, 200)
        stale = await self.client.post('/h3lv/library/'+first['asset']['id']+'/edit', json={'revision': 0, 'name': 'wrong'})
        self.assertEqual(stale.status, 400)
        protected = await self.client.post(self.base+'/refs/remove', json={'name': imported['refs'][0]})
        self.assertEqual(protected.status, 400)
        self.assertTrue((self.project/'refs'/imported['refs'][0]).is_file())

    async def test_ten_segments_one_atomic_apply_and_equal_text_is_noop(self):
        running, peak, called = 0, 0, 0
        lock = threading.Lock()
        def provider(*args, **kwargs):
            nonlocal running, peak, called
            with lock:
                running += 1; called += 1; peak = max(peak, running)
            time.sleep(.04)
            with lock:
                running -= 1
            return {'choices': [{'message': {'content': fixtures.TEXT}}]}
        self.plan['segments'][0]['job'] = {'status': 'completed', 'video': 'old-take.mp4', 'prompt_id': 'old'}
        core.write_plan(self.root, self.plan)
        with patch.object(expansion, 'call', side_effect=provider):
            job = await self.start()
            result = await self.wait_job(job['id'])
        self.assertEqual(called, 10)
        self.assertEqual(peak, 2)
        self.assertEqual(result['counts'].get('succeeded'), 10)
        self.assertNotIn('PRIVATE-ROUTE-KEY', json.dumps(result))
        self.assertEqual(core.read_plan(self.root, self.plan['id'])['revision'], 1)
        response = await self.client.post(self.base+'/expansion-batches/'+job['id']+'/apply',
            json={'revision': 1, 'indices': list(range(10))})
        self.assertEqual(response.status, 200, await response.text())
        saved = await response.json()
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(saved['changed'], list(range(10)))
        plan = core.read_plan(self.root, self.plan['id'])
        self.assertTrue(plan['segments'][0]['needs_regeneration'])
        self.assertEqual(plan['segments'][0]['job']['video'], 'old-take.mp4')
        plan['approved'] = True
        core.write_plan(self.root, plan)
        again = await self.client.post(self.base+'/expansion-batches/'+job['id']+'/apply',
            json={'revision': 2, 'indices': list(range(10))})
        self.assertEqual(again.status, 200, await again.text())
        unchanged = await again.json()
        self.assertEqual(unchanged['revision'], 2)
        self.assertEqual(unchanged['changed'], [])
        self.assertTrue(core.read_plan(self.root, self.plan['id'])['approved'])

    async def test_four_way_batch_route_reaches_limit_and_applies_all_ten_segments(self):
        running = peak = 0
        lock, started, release = threading.Lock(), threading.Event(), threading.Event()
        self.gates.append(release)
        def provider(*args, **kwargs):
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
                if running == 4:
                    started.set()
            try:
                release.wait(4)
                return {'choices':[{'message':{'content':fixtures.TEXT}}]}
            finally:
                with lock:
                    running -= 1
        with patch.object(expansion, 'call', side_effect=provider):
            job = await self.start({**self.payload, 'concurrency': 4})
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
            finally:
                release.set()
            result = await self.wait_job(job['id'])
        self.assertEqual(peak, 4)
        self.assertEqual(result['concurrency'], 4)
        self.assertEqual(result['counts'].get('succeeded'), 10)
        response = await self.client.post(self.base+'/expansion-batches/'+job['id']+'/apply',
            json={'revision': 1, 'indices': list(range(10))})
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual((await response.json())['revision'], 2)

    async def test_manual_content_is_protected_and_force_keeps_old_expansion_history(self):
        self.plan['segments'][0]['expanded_prompt'] = '旧的人工扩写内容。'
        self.plan['segments'][1]['final_prompt'] = 'Handwritten passthrough must stay'
        core.write_plan(self.root, self.plan)
        with patch.object(expansion, 'call', return_value={'choices':[{'message':{'content':fixtures.TEXT}}]}) as provider:
            job = await self.start({**self.payload, 'indices': [0, 1]})
            result = await self.wait_job(job['id'])
            provider.assert_not_called()
            self.assertEqual(result['counts'].get('protected'), 2)
            forced = await self.start({**self.payload, 'indices': [0, 1], 'force': True})
            result = await self.wait_job(forced['id'])
            self.assertEqual(result['items'][1]['status'], 'protected')
        response = await self.client.post(self.base+'/expansion-batches/'+forced['id']+'/apply',
            json={'revision': 1, 'indices': [0]})
        self.assertEqual(response.status, 200, await response.text())
        plan = core.read_plan(self.root, self.plan['id'])
        self.assertEqual(plan['segments'][1]['final_prompt'], 'Handwritten passthrough must stay')
        self.assertEqual('旧的人工扩写内容。', plan['segments'][0]['expanded_history'][0]['text'])

    async def test_h3_invalid_edit_rejects_batch_and_single_save_without_changes(self):
        with patch.object(expansion, 'call', return_value={'choices':[{'message':{'content':fixtures.TEXT}}]}):
            job = await self.start({**self.payload, 'indices': [0, 1]})
            result = await self.wait_job(job['id'])
        self.assertEqual(result['counts'].get('succeeded'), 2)
        invalid = fixtures.TEXT.replace('fully_preserved', 'fully_copy', 1)
        self.assertNotEqual(invalid, fixtures.TEXT)
        response = await self.client.post(self.base+'/expansion-batches/'+job['id']+'/apply',
            json={'revision': 1, 'indices': [0, 1], 'edits': {'1': invalid}})
        self.assertEqual(response.status, 400, await response.text())
        single = await self.client.post(self.base+'/expand', json={
            **self.payload, 'index': 0, 'key': result['items'][0]['key'], 'text': invalid})
        self.assertEqual(single.status, 400, await single.text())
        current = core.read_plan(self.root, self.plan['id'])
        self.assertEqual(current['revision'], 1)
        self.assertNotIn('expanded_prompt', current['segments'][0])
        self.assertNotIn('expanded_prompt', current['segments'][1])

    async def test_stale_results_reject_all_changes_without_partial_save(self):
        with patch.object(expansion, 'call', return_value={'choices':[{'message':{'content':fixtures.TEXT}}]}):
            job = await self.start({**self.payload, 'indices': [0, 1]})
            await self.wait_job(job['id'])
        plan = core.read_plan(self.root, self.plan['id'])
        plan['revision'] += 1; plan['segments'][0]['shot_request'] += '更新镜头要求。'
        core.write_plan(self.root, plan)
        for revision in (1, 2):
            response = await self.client.post(self.base+'/expansion-batches/'+job['id']+'/apply',
                json={'revision': revision, 'indices': [1, 0]})
            self.assertEqual(response.status, 400)
        current = core.read_plan(self.root, self.plan['id'])
        self.assertNotIn('expanded_prompt', current['segments'][1])
        self.assertEqual(current['revision'], 2)

    async def test_cancel_and_project_mutations_are_mutually_exclusive(self):
        gate = threading.Event(); self.gates.append(gate)
        def provider(*args, **kwargs):
            gate.wait(5)
            return {'choices':[{'message':{'content':fixtures.TEXT}}]}
        with patch.object(expansion, 'call', side_effect=provider):
            job = await self.start({**self.payload, 'indices': [0, 1]})
            self.assertTrue(jobs.active(self.plan['id']))
            for path, payload in [(self.base+'/edit', {'revision': 1, 'segments': self.plan['segments']}),
                                  (self.base+'/approve', {'revision': 1}),
                                  ('/h3lv/projects/delete', {'ids': [self.plan['id']]})]:
                response = await self.client.post(path, json=payload)
                self.assertEqual(response.status, 400, await response.text())
            with self.assertRaisesRegex(ValueError, '批量扩写'):
                controller.start(self.root, self.plan['id'], {}, object())
            cancelled = await self.client.post(self.base+'/expansion-batches/'+job['id']+'/cancel', json={})
            self.assertEqual(cancelled.status, 200)
            gate.set()
            result = await self.wait_job(job['id'])
            self.assertIn(result['state'], ('cancelled', 'succeeded'))
            self.assertNotIn('expanded_prompt', core.read_plan(self.root, self.plan['id'])['segments'][0])

    async def test_retry_only_unfinished_and_reuses_completed_cache(self):
        attempts = []
        def provider(path, payload, **kwargs):
            attempts.append(payload)
            if len(attempts) == 1:
                raise ValueError('Transient fixture failure')
            return {'choices':[{'message':{'content':fixtures.TEXT}}]}
        with patch.object(expansion, 'call', side_effect=provider):
            job = await self.start({**self.payload, 'indices': [0, 1], 'concurrency': 1})
            result = await self.wait_job(job['id'])
            self.assertEqual([item['status'] for item in result['items']], ['failed', 'succeeded'])
            retry = await self.client.post(self.base+'/expansion-batches/'+job['id']+'/retry', json={'revision': 1})
            self.assertEqual(retry.status, 200, await retry.text())
            new = await retry.json(); result = await self.wait_job(new['id'])
            self.assertEqual([item['index'] for item in result['items']], [0])
            self.assertEqual(len(attempts), 3)


if __name__ == '__main__':
    unittest.main()
