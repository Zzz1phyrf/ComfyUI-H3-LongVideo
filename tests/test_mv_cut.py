"""Manual cuts use real plan editing and HTTP handler; no provider calls."""
import copy
import json
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_plugin import core, routes, sample_plan


def updates(plan):
    return [dict(end=row['end'], prompt=row['prompt']) for row in plan['segments']]


class CutTests(unittest.TestCase):
    def test_moving_cut_cannot_bypass_four_second_limit(self):
        p=sample_plan();submitted=updates(p);submitted[0]['end']=3.9
        with self.assertRaisesRegex(ValueError,'4–15'):core.edit_plan(p,submitted)

    def test_split_keeps_samples_and_frames_contiguous_at_four_second_limit(self):
        for time in (4, 6, 14, 26):
            with self.subTest(time=time):
                p=sample_plan();before=copy.deepcopy(p)
                result=core.edit_plan(p,updates(p),split_time=time)
                self.assertEqual(p,before)
                self.assertEqual(len(result['segments']),4)
                self.assertEqual(sum(row['edit_frames'] for row in result['segments']),720)
                self.assertTrue(all(4 <= row['duration'] <= 15 for row in result['segments']))
                self.assertEqual([r['end_sample'] for r in result['segments'][:-1]],
                                 [r['start_sample'] for r in result['segments'][1:]])
                self.assertEqual(result['revision'],p['revision']+1)
                self.assertFalse(result['approved'])

    def test_reject_bad_cut_without_mutating_plan(self):
        for time in (0, 3.9, 6.1, 10, 30, -1, 31, float('nan'), float('inf'), None, 'abc'):
            with self.subTest(time=time):
                p=sample_plan();before=copy.deepcopy(p)
                with self.assertRaises(ValueError):core.edit_plan(p,updates(p),split_time=time)
                self.assertEqual(p,before)

    def test_split_preserves_original_take_and_unaffected_completed_segments(self):
        p=sample_plan();p.update(approved=True,final_video='old-final.mp4')
        for i,row in enumerate(p['segments']):
            row['job']={'status':'completed','video':f'old-{i}.mp4'}
        p['segments'][1].update(final_prompt='old direct prompt',expanded_prompt='old expansion',expanded_key='old-key')
        result=core.edit_plan(p,updates(p),split_time=15)
        self.assertEqual(result['segments'][1]['job'],p['segments'][1]['job'])
        self.assertTrue(result['segments'][1]['needs_regeneration'])
        self.assertNotIn('job',result['segments'][2])
        for row in result['segments'][1:3]:
            self.assertNotIn('final_prompt',row)
            self.assertNotIn('expanded_prompt',row)
            self.assertTrue(row['prompt'])
        for old,new in ((0,0),(2,3)):
            self.assertEqual(result['segments'][new]['job'],p['segments'][old]['job'])
            self.assertEqual(core.segment_fingerprint(result['segments'][new]),core.segment_fingerprint(p['segments'][old]))
            self.assertFalse(result['segments'][new].get('needs_regeneration'))
        self.assertEqual(result['changed_segments'],[1,2])
        self.assertEqual(result['final_video'],'old-final.mp4')
        self.assertTrue(result['final_stale'])

    def test_split_uses_unsaved_boundaries_and_inherits_materials(self):
        p=sample_plan();submitted=updates(p)
        submitted[0]['end']=9
        submitted[1].update(refs=['portrait.png'],material_note='person in scene')
        result=core.edit_plan(p,submitted,split_time=13)
        self.assertEqual([r['duration'] for r in result['segments']],[9,4,7,10])
        for row in result['segments'][1:3]:
            self.assertEqual(row['refs'],['portrait.png'])
            self.assertEqual(row['material_note'],'person in scene')
        self.assertEqual(result['changed_segments'],[0,1,2])

    def test_reject_running_and_unresolved_remote_tasks(self):
        p=sample_plan();p['run_status']='running'
        with self.assertRaisesRegex(ValueError,'停止'):core.edit_plan(p,updates(p),split_time=5)
        p['run_status']='failed';p['segments'][2]['job']={'backend':'runninghub','status':'unknown'}
        with self.assertRaisesRegex(ValueError,'未解决'):core.edit_plan(p,updates(p),split_time=5)

    def test_fifteen_second_cap_even_when_legacy_project_allows_longer(self):
        p=sample_plan();p['segments']=[dict(start_sample=0,end_sample=3000,energy_db=-20)]
        p['max_seconds']=30;core.decorate(p)
        result=core.edit_plan(p,updates(p),split_time=15)
        self.assertEqual([r['duration'] for r in result['segments']],[15,15])
        with self.assertRaisesRegex(ValueError,'4–15'):core.edit_plan(p,updates(p),split_time=14)


class CutRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_save_reassociates_lyrics_and_rejects_stale_or_invalid_edit(self):
        from aiohttp import web
        table=web.RouteTableDef()
        server=types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=table)))
        with patch.dict('sys.modules',server=server):routes.register_routes()
        handler=next(r.handler for r in table if r.method=='POST' and r.path=='/h3lv/project/{project_id}/edit')
        with tempfile.TemporaryDirectory() as root,patch.object(routes,'data_root',return_value=root),patch.object(routes,'record_error'),patch.object(routes,'read_project_transcript',return_value=({'segments':[{'start':1,'end':2,'text':'first'},{'start':6,'end':7,'text':'second'}]},False)):
            p=sample_plan();core.write_plan(root,p)
            body=dict(revision=p['revision'],segments=updates(p),split_time=5)
            request=types.SimpleNamespace(json=AsyncMock(return_value=body),match_info={'project_id':p['id']})
            response=await handler(request)
            self.assertEqual(response.status,200,response.text)
            saved=core.read_plan(root,p['id'])
            self.assertEqual([r['text'] for r in saved['segments'][:2]],['first','second'])
            response=await handler(request)
            self.assertEqual(response.status,400)
            self.assertEqual(core.read_plan(root,p['id']),saved)
            body.update(revision=saved['revision'],segments=updates(saved),split_time=2)
            response=await handler(request)
            self.assertEqual(response.status,400)
            self.assertEqual(core.read_plan(root,p['id']),saved)
