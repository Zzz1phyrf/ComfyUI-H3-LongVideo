import copy
import importlib
import json
from pathlib import Path
import unittest
import sys
import types
from unittest.mock import AsyncMock, patch

import test_talking_overlap as overlap_tests
from test_talking_rh import project, runner, graph

prompts=importlib.import_module('h3talk_test.talking_rh.prompts')


class PromptTests(unittest.TestCase):
    setUp=overlap_tests.LifecycleTests.setUp
    approve=overlap_tests.LifecycleTests.approve
    run_project=overlap_tests.LifecycleTests.run_project

    def save(self,index=1,text='A relaxed conversation.',expected=None):
        return prompts.save_draft(self.root,self.pid,index,text,expected)

    def test_save_draft_keeps_completed_takes_approval_and_final_unchanged(self):
        before=self.run_project();before['final_video']='preserved-final';before['final_stale']=False
        project.write_plan(self.root,before)
        current=self.save()
        expected=copy.deepcopy(before);expected['segments'][1]['prompt_draft']=current['segments'][1]['prompt_draft']
        self.assertEqual(current,expected);self.assertEqual(len(self.client.created),3)
        self.assertEqual(project.fingerprint(current),project.fingerprint(before))

    def test_single_redo_uses_saved_text_and_preserves_other_actual_versions(self):
        old=self.run_project();self.save();marked=project.request_regeneration(self.root,self.pid,1)
        self.assertEqual(marked['segments'][1]['generation_prompt']['text'],'A relaxed conversation.')
        current=self.run_project();self.assertEqual(len(self.client.created),4)
        self.assertEqual(self.client.created[-1]['268']['inputs']['prompt'],'A relaxed conversation.')
        self.assertEqual(old['segments'][0],current['segments'][0])
        self.assertEqual(old['segments'][2],current['segments'][2])
        view=prompts.versions(self.root,current)
        self.assertEqual(view['windows'][1]['current']['state'],'submitted')
        self.assertEqual(view['windows'][1]['history'][0]['text'],old['config']['fixed_prompt'])

    def test_mixed_historical_prompt_redo_uses_own_take_instead_of_current_config(self):
        old=self.run_project();current=copy.deepcopy(old)
        current['config']['fixed_prompt']='New project text that old takes never used.'
        current['approved_fingerprint']=project.fingerprint(current);project.write_plan(self.root,current)
        marked=project.request_regeneration(self.root,self.pid,0)
        self.assertEqual(marked['segments'][0]['generation_prompt']['text'],old['config']['fixed_prompt'])

    def test_actual_graph_wins_and_missing_evidence_never_invents_actual_text(self):
        p=self.run_project();job=p['segments'][1]['job'];folder=Path(job['directory'])
        submitted=json.loads((folder/'submitted_graph.json').read_text(encoding='utf-8'))
        actual=copy.deepcopy(submitted);actual['268']['inputs']['prompt']='Actually executed variant'
        (folder/'actual_graph.json').write_text(json.dumps(actual),encoding='utf-8')
        value=prompts.evidence(self.directory,job)
        self.assertEqual(value['state'],'mismatch');self.assertEqual(value['text'],'Actually executed variant')
        actual['268']['inputs']['prompt']=['5',17]
        (folder/'actual_graph.json').write_text(json.dumps(actual),encoding='utf-8')
        submitted['268']['inputs']['prompt']=['5',17]
        (folder/'submitted_graph.json').write_text(json.dumps(submitted),encoding='utf-8')
        unknown=prompts.evidence(self.directory,job)
        self.assertEqual(unknown['state'],'unavailable');self.assertNotIn('text',unknown)

    def test_verified_version_matches_both_immutable_graphs(self):
        p=self.run_project();job=p['segments'][0]['job'];folder=Path(job['directory'])
        (folder/'actual_graph.json').write_bytes((folder/'submitted_graph.json').read_bytes())
        before=project.read_plan(self.root,self.pid);version=prompts.versions(self.root,before)
        self.assertEqual(version['windows'][0]['current']['state'],'verified')
        self.assertEqual(project.read_plan(self.root,self.pid),before)

    def test_conflicting_save_invalid_input_and_pending_redo_do_not_mutate(self):
        self.run_project();self.save()
        before=project.read_plan(self.root,self.pid)
        for index,text,expected in [(1,'overwrite',None),(-1,'text',None),(True,'text',None),(1,' ',None),(1,'x'*32001,None)]:
            with self.assertRaises(ValueError):self.save(index,text,expected)
            self.assertEqual(project.read_plan(self.root,self.pid),before)
        revision=before['segments'][1]['prompt_draft']['sha256']
        self.save(text='Second saved revision',expected=revision)
        project.request_regeneration(self.root,self.pid,1)
        with self.assertRaises(ValueError):self.save(index=0)

    def test_restore_returns_original_take_and_keeps_draft_for_manual_retry(self):
        old=self.run_project();saved=self.save();project.request_regeneration(self.root,self.pid,1)
        restored=project.restore_regeneration(self.root,self.pid)
        self.assertEqual(restored['segments'][1]['job'],old['segments'][1]['job'])
        self.assertEqual(restored['segments'][1]['prompt_draft'],saved['segments'][1]['prompt_draft'])
        self.assertEqual(len(self.client.created),3)

    def test_new_draft_after_completed_redo_supersedes_earlier_frozen_version(self):
        self.run_project();self.save();project.request_regeneration(self.root,self.pid,1);self.run_project()
        p=project.read_plan(self.root,self.pid)
        project.finish_regeneration(p);project.write_plan(self.root,p)
        p=self.save(text='New draft',expected=p['segments'][1]['prompt_draft']['sha256'])
        self.assertEqual(prompts.effective(p,p['segments'][1]),'New draft')

    def test_local_and_rh_graphs_freeze_the_same_literal_prompt(self):
        p=project.read_plan(self.root,self.pid);p['segments'][0]['prompt_draft']=prompts.snapshot('Local exact prompt')
        uploads={'attempt':'a'*12,'references':['image.png'],'audio':'speech.wav'}
        remote=graph.build(p,p['segments'][0],uploads)
        p['config']=overlap_tests.config(True)
        local=overlap_tests.canvas.build(p,p['segments'][0],uploads)
        reference=p['config']['canvas']['prompt'][p['config']['canvas']['sampler_id']]['inputs']['positive'][0]
        self.assertEqual(remote['268']['inputs']['prompt'],'Local exact prompt')
        self.assertEqual(local[reference]['inputs']['prompt'],'Local exact prompt')

    def test_path_escape_does_not_read_outside_project(self):
        self.assertEqual(prompts.evidence(self.directory,{'directory':str(self.directory.parent)})['state'],'unavailable')

    def test_legacy_projects_without_fixed_prompt_still_show_read_only_evidence(self):
        p=self.run_project();p['config'].pop('fixed_prompt');p['config']['talking_profile']='ref2va-v1'
        view=prompts.versions(self.root,p)
        self.assertFalse(view['editable']);self.assertEqual(view['windows'][0]['current']['state'],'submitted')


class PromptRouteTests(unittest.IsolatedAsyncioTestCase):
    setUp=PromptTests.setUp
    approve=PromptTests.approve
    run_project=PromptTests.run_project

    async def invoke(self,method,path,data=None):
        routes=importlib.import_module('h3talk_test.talking_rh.routes');handlers={}
        class Router:
            def get(self,url):return self.register('GET',url)
            def post(self,url):return self.register('POST',url)
            def register(self,verb,url):
                def record(fn):handlers[(verb,url)]=fn;return fn
                return record
        server=types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=Router())))
        with patch.dict(sys.modules,{'server':server}),patch.object(routes,'root',return_value=self.root),\
             patch.object(runner,'make_client',side_effect=AssertionError('Draft routes cannot start compute')),\
             patch.object(routes.rh_credentials,'public_settings',return_value={'configured':True}):
            routes.register()
            req=types.SimpleNamespace(match_info={'pid':self.pid,'action':'prompt'},json=AsyncMock(return_value=data))
            result=await handlers[(method,path)](req)
        return result.status,json.loads(result.text)

    async def test_get_and_save_real_routes_keep_actual_version_and_do_not_start_compute(self):
        self.run_project()
        status,view=await self.invoke('GET','/h3talk/project/{pid}/prompts')
        self.assertEqual(status,200);actual=view['windows'][1]['current']
        status,_=await self.invoke('POST','/h3talk/project/{pid}/{action}',
            {'index':1,'text':'Draft through route','expected_sha256':None})
        self.assertEqual(status,200)
        _,view=await self.invoke('GET','/h3talk/project/{pid}/prompts')
        self.assertEqual(view['windows'][1]['current'],actual)
        self.assertEqual(view['windows'][1]['draft']['text'],'Draft through route')
        self.assertEqual(len(self.client.created),3)

    async def test_save_missing_revision_and_malformed_input_return_400_without_changes(self):
        before=project.read_plan(self.root,self.pid)
        for data in ({'index':0,'text':'Missing revision'},
                     {'index':0,'text':[],'expected_sha256':None}):
            status,_=await self.invoke('POST','/h3talk/project/{pid}/{action}',data)
            self.assertEqual(status,400);self.assertEqual(project.read_plan(self.root,self.pid),before)

    async def test_stale_segment_layout_cannot_write_a_prompt_to_a_different_segment(self):
        before=project.read_plan(self.root,self.pid)
        status,_=await self.invoke('POST','/h3talk/project/{pid}/{action}',
            {'index':0,'text':'Do not save in a moved segment','expected_sha256':None,'expected_fingerprint':'old-layout'})
        self.assertEqual(status,400);self.assertEqual(project.read_plan(self.root,self.pid),before)
