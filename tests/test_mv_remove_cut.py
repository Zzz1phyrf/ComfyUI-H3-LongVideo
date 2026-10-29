"""Removing a cut merges only its two adjacent segments; no generation."""
import copy
import json
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_plugin import core, routes, sample_plan, controller
from test_mv_cut import updates


def plan():
    p = sample_plan()
    p['segments'] = [dict(start_sample=i*500, end_sample=(i+1)*500, energy_db=-20) for i in range(6)]
    return core.decorate(p)


class RemoveCutTests(unittest.TestCase):
    def test_merge_preserves_exact_coverage_and_other_completed_segments(self):
        p = plan()
        p.update(approved=True, final_video='old-final.mp4')
        for i, row in enumerate(p['segments']):
            row['job'] = {'status': 'completed', 'video': f'old-{i}.mp4'}
        p['segments'][1].update(refs=['left.png'], material_note='left', final_prompt='old', expanded_prompt='old', expanded_key='old')
        p['segments'][2].update(refs=['right.png'], material_note='right')
        before = copy.deepcopy(p)
        merged = core.edit_plan(p, updates(p), remove_cut=1)
        self.assertEqual(p, before)
        self.assertEqual([r['duration'] for r in merged['segments']], [5, 10, 5, 5, 5])
        self.assertEqual(sum(r['edit_frames'] for r in merged['segments']), 720)
        self.assertEqual([r['end_sample'] for r in merged['segments'][:-1]], [r['start_sample'] for r in merged['segments'][1:]])
        left = merged['segments'][1]
        self.assertEqual(left['refs'], ['left.png'])
        self.assertTrue(left['needs_regeneration'])
        self.assertEqual(left['job'], before['segments'][1]['job'])
        self.assertEqual(merged['removed_segments'][0]['job'], before['segments'][2]['job'])
        self.assertEqual(merged['removed_segments'][0]['refs'], ['right.png'])
        for key in ('final_prompt', 'expanded_prompt', 'expanded_key'):
            self.assertNotIn(key, left)
        self.assertTrue(left['prompt'])
        for old, new in ((0, 0), (3, 2), (4, 3), (5, 4)):
            self.assertEqual(core.segment_fingerprint(before['segments'][old]), core.segment_fingerprint(merged['segments'][new]))
            self.assertEqual(before['segments'][old]['job'], merged['segments'][new]['job'])
            self.assertFalse(merged['segments'][new].get('needs_regeneration'))
        self.assertEqual(merged['changed_segments'], [1])
        self.assertTrue(merged['final_stale'])
        self.assertEqual(merged['final_video'], p['final_video'])
        self.assertFalse(merged['approved'])
        self.assertEqual(merged['revision'], p['revision']+1)

    def test_unsaved_edits_and_last_boundary_and_fifteen_second_limit(self):
        p = plan(); submitted = updates(p)
        submitted[0]['end'] = 4
        submitted[4].update(refs=['new.png'], material_note='new')
        result = core.edit_plan(p, submitted, remove_cut=4)
        self.assertEqual([r['duration'] for r in result['segments']], [4, 6, 5, 5, 10])
        self.assertEqual(result['segments'][4]['refs'], ['new.png'])
        self.assertEqual(result['changed_segments'], [0, 1, 4])
        result = core.edit_plan(result, updates(result), remove_cut=3)
        self.assertEqual(result['segments'][-1]['duration'], 15)
        with self.assertRaisesRegex(ValueError, '4–15'):
            core.edit_plan(result, updates(result), remove_cut=2)

    def test_invalid_requests_do_not_modify_source(self):
        for value in (-1, 5, 6, None, True, 1.5, '1'):
            p = plan(); before = copy.deepcopy(p)
            with self.subTest(value=value), self.assertRaises(ValueError):
                core.edit_plan(p, updates(p), remove_cut=value)
            self.assertEqual(p, before)
        p = plan(); p['max_seconds'] = 8
        with self.assertRaisesRegex(ValueError, '4–8'):
            core.edit_plan(p, updates(p), remove_cut=0)
        with self.assertRaisesRegex(ValueError, '同时'):
            core.edit_plan(p, updates(p), split_time=2, remove_cut=0)

    def test_busy_and_unresolved_tasks_are_rejected(self):
        p = plan(); p['run_status'] = 'running'
        with self.assertRaisesRegex(ValueError, '停止'):
            core.edit_plan(p, updates(p), remove_cut=0)
        p['run_status'] = 'failed'; p['segments'][5]['job'] = {'backend':'runninghub', 'status':'unknown'}
        with self.assertRaisesRegex(ValueError, '未解决'):
            core.edit_plan(p, updates(p), remove_cut=0)


class RemoveRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_merge_updates_lyrics_and_rejects_stale_active_or_invalid_requests(self):
        from aiohttp import web
        table = web.RouteTableDef()
        server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=table)))
        with patch.dict('sys.modules', server=server):routes.register_routes()
        handler = next(r.handler for r in table if r.method == 'POST' and r.path == '/h3lv/project/{project_id}/edit')
        with tempfile.TemporaryDirectory() as root, patch.object(routes, 'data_root', return_value=root), patch.object(routes, 'record_error'), patch.object(routes, 'read_project_transcript', return_value=({'segments':[{'start':1,'end':2,'text':'first'}, {'start':6,'end':7,'text':'second'}]},False)):
            p = plan(); core.write_plan(root, p)
            body = dict(revision=p['revision'], segments=updates(p), remove_cut=0)
            request = types.SimpleNamespace(json=AsyncMock(return_value=body), match_info={'project_id':p['id']})
            with patch.dict(controller.TASKS, {p['id']: object()}):
                self.assertEqual((await handler(request)).status, 400)
            self.assertEqual(core.read_plan(root,p['id']),p)
            response = await handler(request)
            self.assertEqual(response.status,200,response.text)
            saved = core.read_plan(root,p['id'])
            self.assertEqual(saved['segments'][0]['text'],'first / second')
            self.assertEqual(len(saved['segments']),5)
            self.assertEqual((await handler(request)).status,400)
            body.update(revision=saved['revision'],segments=updates(saved),remove_cut=10)
            self.assertEqual((await handler(request)).status,400)
            self.assertEqual(core.read_plan(root,p['id']),saved)
