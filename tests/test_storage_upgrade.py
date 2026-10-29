"""A full-package upgrade keeps old project IDs, takes, finals and receipts."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import test_plugin as mv
import test_talking_auto_merge as auto
import test_talking_management as talking

storage = importlib.import_module('h3lv_test.storage')
talk_routes = importlib.import_module('h3talk_test.talking_rh.routes')
local = importlib.import_module('h3talk_test.talking_rh.local')


class LegacyStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def test_old_project_read_and_update_stays_in_place_new_ids_use_new_bucket(self):
        for category, old in [('MV', 'H3LongVideo'), ('口播', 'H3TalkingRH')]:
            with self.subTest(category=category):
                root = self.output/'H3LongVideo'/category/'projects'
                legacy = self.output/old/'projects'
                plan = mv.sample_plan()
                mv.core.write_plan(legacy, plan)
                before = mv.core.state_file(legacy/plan['id'], 'segments.json').read_bytes()
                self.assertEqual(mv.core.project_path(root, plan['id']), legacy/plan['id'])
                self.assertEqual(mv.core.read_plan(root, plan['id'])['id'], plan['id'])
                self.assertEqual(mv.core.state_file(legacy/plan['id'], 'segments.json').read_bytes(), before)
                plan['run_status'] = 'paused'
                mv.core.write_plan(root, plan)
                self.assertEqual(mv.core.read_plan(legacy, plan['id'])['run_status'], 'paused')
                self.assertFalse((root/plan['id']).exists())
                self.assertEqual(mv.core.project_path(root, 'b'*32), root/('b'*32))

    def test_final_ownership_accepts_both_layouts_and_rejects_other_outputs(self):
        for category, old in [('MV', 'H3LongVideo'), ('口播', 'H3TalkingRH')]:
            root = self.output/'H3LongVideo'/category/'projects'
            for folder in (root.parent, self.output/old):
                path = folder/'final_videos/clip.mp4'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'video')
                self.assertEqual(storage.final_file(root, path), path)
            with self.assertRaises(ValueError):
                storage.final_file(root, self.output/'OtherPlugin/video.mp4')

    def test_current_project_takes_precedence_without_duplicate_listing(self):
        root = self.output/'H3LongVideo/MV/projects'
        legacy = self.output/'H3LongVideo/projects'
        plan = mv.sample_plan()
        mv.core.write_plan(root, plan)
        mv.core.write_plan(legacy, {**plan, 'run_status':'paused'})
        self.assertEqual(mv.core.read_plan(root, plan['id'])['run_status'], 'draft')
        self.assertEqual(list(storage.project_plans(root)), [root/plan['id']/'state/segments.json'])

    def test_old_local_task_receipt_is_used_without_enqueue_or_copy(self):
        import uuid
        task = str(uuid.uuid4())
        old = self.output/'H3TalkingRH/local_tasks'/f'{task}.json'
        old.parent.mkdir(parents=True)
        result = {'status':'SUCCESS', 'results':[]}
        old.write_text(json.dumps({'status':'SUCCESS', 'result':result}), encoding='utf-8')
        client = local.LocalClient({}, server=object(), input_root=self.output/'input', output_root=self.output)
        self.assertEqual(client.query(task), result)
        self.assertEqual(client.record(task), old)
        self.assertFalse((client.records/old.name).exists())


class LegacyProjectRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_mv_and_talking_projects_list_manage_and_stream_final(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            mv_old = output/'H3LongVideo/projects'
            mv_new = output/'H3LongVideo/MV/projects'
            plan = mv.sample_plan()
            final = mv_old.parent/'final_videos/mv.mp4'
            final.parent.mkdir(parents=True)
            final.write_bytes(b'old mv final')
            plan.update(final_video=str(final), run_status='completed')
            mv.core.write_plan(mv_old, plan)
            fixture = talking.TalkingManagementTests()
            fixture.root = output/'H3TalkingRH/projects'
            fixture.root.mkdir(parents=True)
            talk_plan, _, talk_final = fixture.fixture('c'*32)
            talk_new = output/'H3LongVideo/口播/projects'
            registry = web.RouteTableDef()
            server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=registry)))
            paths = types.SimpleNamespace(get_input_directory=lambda:str(output/'input'))
            with patch.dict(sys.modules, {'server':server, 'folder_paths':paths}), \
                 patch.object(mv.routes, 'data_root', return_value=mv_new), \
                 patch.object(mv.routes, 'final_root', return_value=mv_new.parent/'final_videos'), \
                 patch.object(talk_routes, 'root', return_value=talk_new):
                mv.routes.register_routes()
                talk_routes.register()
                app = web.Application()
                app.add_routes(registry)
                async with TestClient(TestServer(app)) as client:
                    for prefix, pid, content in [('h3lv', plan['id'], b'old mv final'),
                                                  ('h3talk', talk_plan['id'], b'final')]:
                        response = await client.get(f'/{prefix}/projects')
                        self.assertEqual(response.status, 200)
                        self.assertEqual([row['id'] for row in await response.json()], [pid])
                        inventory = await client.get(f'/{prefix}/projects/manage')
                        self.assertEqual(inventory.status, 200)
                        self.assertTrue((await inventory.json())[0]['has_final'])
                        route = 'final' if prefix == 'h3lv' else 'file/final'
                        video = await client.get(f'/{prefix}/project/{pid}/{route}')
                        self.assertEqual(video.status, 200)
                        self.assertEqual(await video.read(), content)
            self.assertTrue(final.is_file())
            self.assertTrue(talk_final.is_file())


class LegacyCompletedTakeTests(auto.AutoMergeTests):
    layout = 'H3TalkingRH/projects'

    def setUp(self):
        super().setUp()
        self.root = Path(self.temp.name)/'H3LongVideo/口播/projects'
