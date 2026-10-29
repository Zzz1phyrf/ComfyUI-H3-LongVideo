"""Exercise project controls through registered handlers without compute or Explorer."""
import importlib
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_talking_rh as lifecycle
from test_talking_rh import project, runner

routes=importlib.import_module('h3talk_test.talking_rh.routes')


class ControlParityTests(unittest.IsolatedAsyncioTestCase):
    setUp=lifecycle.LifecycleTests.setUp
    approve=lifecycle.LifecycleTests.approve

    async def invoke(self,action):
        handlers={}
        class Router:
            def get(self,path):return self.capture('GET',path)
            def post(self,path):return self.capture('POST',path)
            def capture(self,verb,path):
                def save(fn):handlers[verb,path]=fn;return fn
                return save
        server=types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=Router())))
        with patch.dict(sys.modules,server=server),patch.object(routes,'root',return_value=self.root), \
             patch.object(runner,'busy',return_value=True),patch.object(runner,'make_client',side_effect=AssertionError('No compute')):
            routes.register()
            path='/h3talk/project/{pid}/reveal-final' if action=='reveal-final' else '/h3talk/project/{pid}/{action}'
            request=types.SimpleNamespace(match_info={'pid':self.pid,'action':action},json=AsyncMock(return_value={}))
            response=await handlers['POST',path](request)
        return response.status,json.loads(response.text)

    async def test_stop_and_pause_keep_the_running_task_and_stop_has_priority(self):
        plan=project.read_plan(self.root,self.pid)
        job={'status':'submitted','task_id':'existing-paid-job'}
        plan['segments'][0]['job']=job;plan['run_status']='running';project.write_plan(self.root,plan)
        status,value=await self.invoke('stop')
        self.assertEqual(status,200);self.assertEqual(value['run_status'],'stopping')
        _,value=await self.invoke('pause');self.assertEqual(value['run_status'],'stopping')
        current=project.read_plan(self.root,self.pid)
        self.assertEqual(current['segments'][0]['job'],job)
        self.assertTrue(current['stop_requested'])

    async def test_reveal_opens_only_the_managed_final_and_never_mutates_plan(self):
        final=self.root.parent/'final_videos'/'result.mp4';final.parent.mkdir();final.write_bytes(b'final')
        plan=project.read_plan(self.root,self.pid);plan['final_video']=str(final);project.write_plan(self.root,plan)
        before=(self.dir/'state/segments.json').read_bytes()
        with patch.object(runner.controller,'reveal_file',return_value=str(final)) as reveal:
            status,value=await self.invoke('reveal-final')
        self.assertEqual(status,200);self.assertEqual(value['path'],str(final));reveal.assert_called_once_with(final)
        self.assertEqual((self.dir/'state/segments.json').read_bytes(),before)
        plan['final_video']=str(self.root.parent/'outside.mp4');project.write_plan(self.root,plan)
        with patch.object(runner.controller,'reveal_file') as reveal:
            status,_=await self.invoke('reveal-final')
        self.assertEqual(status,400);reveal.assert_not_called()

    def test_continue_clears_stop_without_touching_existing_takes(self):
        plan=project.read_plan(self.root,self.pid);plan.update(stop_requested=True,pause_requested=True,run_status='stopped')
        project.write_plan(self.root,plan);jobs=plan['segments'].copy()
        with patch.object(runner,'run_project') as execute:
            runner.start(self.root,self.pid,client_factory=lambda _:self.client)
            thread=runner.THREADS.get(self.pid)
            if thread:thread.join(5)
        execute.assert_called_once()
        current=project.read_plan(self.root,self.pid)
        self.assertFalse(current['stop_requested']);self.assertFalse(current['pause_requested'])
        self.assertEqual(current['segments'],jobs)
