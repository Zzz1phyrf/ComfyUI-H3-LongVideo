"""Reference replacement must not reanalyse speech or mix existing takes."""
import copy
import importlib
from pathlib import Path
import tempfile
import types
import sys
import unittest
import json
from unittest.mock import patch,AsyncMock

import numpy as np
from PIL import Image
from test_talking_flat_rh import project, flat, config, runner
from test_talking_rh import FakeClient,remote


class ReferenceSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'projects'
        cfg = config();cfg['reference_geometry'] = flat.GEOMETRY
        self.plan = project.prepare(self.root,np.zeros((600000,1),np.float32),24000,
                                    [Image.new('RGB',(94,167),'white')],None,cfg,'initial')
        self.plan = project.update_cuts(self.root,self.plan['id'],[141/24,282/24,423/24])
        self.plan.update(approved=True,approved_fingerprint=project.fingerprint(self.plan))
        self.plan['segments'][1]['prompt_draft']={'text':'custom speaking','sha256':'test'}
        project.write_plan(self.root,self.plan)
        self.refs = importlib.import_module('h3talk_test.talking_rh.references')
        self.image = Image.new('RGB',(94,167),'black')

    def sync(self,**options):
        return self.refs.update(self.root,self.plan['id'],self.image,
                                project.fingerprint(self.plan),**options)

    def test_unrendered_updates_only_reference_and_keeps_cuts_audio_prompts(self):
        with patch.object(flat,'segment',side_effect=AssertionError('must not analyse')):
            result=self.sync()
        new=result['plan']
        self.assertEqual(new['id'],self.plan['id'])
        for key in ('segments_clock','manual_cuts','segments','source_sha256','config'):
            self.assertEqual(new[key],self.plan[key])
        self.assertEqual(new['approved_fingerprint'],project.fingerprint(new))
        self.assertNotEqual(new['assets'],self.plan['assets'])
        project.verify_assets(self.root,new)
        again=self.refs.update(self.root,new['id'],self.image,project.fingerprint(new))
        self.assertEqual(again['status'],'unchanged')
        self.assertTrue(self.refs.inputs_match(self.root,new,np.zeros((600000,1),np.float32),24000,[self.image],new['config']))
        self.assertFalse(self.refs.inputs_match(self.root,new,np.ones((600000,1),np.float32),24000,[self.image],new['config']))
        changed=copy.deepcopy(new['config']);changed['seed']+=1
        self.assertFalse(self.refs.inputs_match(self.root,new,np.zeros((600000,1),np.float32),24000,[self.image],changed))

    def test_generated_project_requires_confirmation_then_forks_without_old_mutation(self):
        self.plan['segments'][0]['job']={'status':'completed','video':'old.mp4'}
        self.plan['final_video']='old-final.mp4'
        project.write_plan(self.root,self.plan)
        path=project.project_path(self.root,self.plan['id'])/'state/segments.json'
        before=path.read_bytes()
        check=self.sync()
        self.assertEqual(check['status'],'confirmation_required')
        self.assertEqual(path.read_bytes(),before)
        result=self.sync(confirmed_sha256=check['candidate_sha256'])
        new=result['plan']
        self.assertNotEqual(new['id'],self.plan['id'])
        self.assertEqual(new['parent_project'],self.plan['id'])
        self.assertEqual(new['manual_cuts'],self.plan['manual_cuts'])
        self.assertEqual(new['segments'][1]['prompt_draft'],self.plan['segments'][1]['prompt_draft'])
        self.assertTrue(all(not r.get('job') for r in new['segments']))
        self.assertNotIn('final_video',new)
        self.assertEqual(path.read_bytes(),before)
        project.verify_assets(self.root,new)

    def test_busy_unknown_and_stale_changes_rejected_without_mutation(self):
        path=project.project_path(self.root,self.plan['id'])/'state/segments.json'
        before=path.read_bytes()
        with patch.object(runner,'claim',side_effect=ValueError('busy')),self.assertRaises(ValueError):self.sync()
        with self.assertRaises(ValueError):
            self.refs.update(self.root,self.plan['id'],self.image,'stale')
        self.assertEqual(path.read_bytes(),before)
        self.plan['segments'][0]['job']={'status':'creating_unknown'}
        project.write_plan(self.root,self.plan)
        with self.assertRaises(ValueError):self.sync()

    def test_confirmation_bound_to_actual_candidate(self):
        self.plan['segments'][0]['job']={'status':'failed'}
        project.write_plan(self.root,self.plan)
        with self.assertRaises(ValueError):self.sync(confirmed_sha256='wrong-image')

    def test_unchanged_image_allows_recovery_of_successful_task_pending_download(self):
        self.plan['segments'][0]['job']={'status':'submitted','provider_status':'SUCCESS','task_id':'123'}
        project.write_plan(self.root,self.plan)
        before=(project.project_path(self.root,self.plan['id'])/'state/segments.json').read_bytes()
        result=self.refs.update(self.root,self.plan['id'],Image.new('RGB',(94,167),'white'),project.fingerprint(self.plan))
        self.assertEqual(result['status'],'unchanged')
        self.assertEqual((project.project_path(self.root,self.plan['id'])/'state/segments.json').read_bytes(),before)
        with self.assertRaises(ValueError):self.sync()

    def run_with(self,client):
        def merge(root,pid):
            path=project.project_path(root,pid)/'final.mp4';path.write_bytes(b'assembled');return str(path)
        runner.run_project(self.root,self.plan['id'],client,validate=lambda *_:{},assemble=merge,sleep=lambda _:None)
        return project.read_plan(self.root,self.plan['id'])

    def sync_same(self):
        current=project.read_plan(self.root,self.plan['id'])
        result=self.refs.update(self.root,current['id'],Image.new('RGB',(94,167),'white'),project.fingerprint(current))
        self.assertEqual(result['status'],'unchanged')
        return result['plan']

    def test_seven_segment_run_download_sixth_fails_then_sync_and_resume_without_duplicate_tasks(self):
        cfg=config();cfg['reference_geometry']=flat.GEOMETRY
        self.plan=project.prepare(self.root,np.zeros((2029392,1),np.float32),24000,
                                  [Image.new('RGB',(94,167),'white')],None,cfg,'input')
        self.plan=project.update_cuts(self.root,self.plan['id'],[294*i/24 for i in range(1,7)])
        self.plan.update(approved=True,approved_fingerprint=project.fingerprint(self.plan));project.write_plan(self.root,self.plan)
        client=FakeClient();download=client.download
        def fail_sixth(url,target):
            if len(client.created)==6:raise remote.RemoteError('download interrupted')
            return download(url,target)
        client.download=fail_sixth
        with self.assertRaises(remote.RemoteError):self.run_with(client)
        failed=self.sync_same();prefix=copy.deepcopy(failed['segments'][:5]);task=failed['segments'][5]['job']['task_id']
        self.assertEqual(len(client.created),6)
        self.assertEqual(failed['segments'][5]['job']['provider_status'],'SUCCESS')
        self.assertNotIn('job',failed['segments'][6])
        client.download=download
        completed=self.run_with(client)
        self.assertEqual(len(client.created),7)
        self.assertEqual(completed['segments'][:5],prefix)
        self.assertEqual(completed['segments'][5]['job']['task_id'],task)
        self.assertEqual(completed['run_status'],'completed')
        self.assertTrue(all(row['job']['status']=='completed' for row in completed['segments']))

    def test_pause_stop_then_sync_resume_and_single_redo_keep_completed_takes(self):
        for flag in ('pause_requested','stop_requested'):
            with self.subTest(flag=flag):
                client=FakeClient()
                # Reset only this temporary test plan, never a user's project.
                plan=copy.deepcopy(self.plan);project.write_plan(self.root,plan)
                client.after_query=lambda _:runner.change(self.root,plan['id'],lambda p:p.update({flag:True}))
                stopped=self.run_with(client);first=copy.deepcopy(stopped['segments'][0]['job'])
                self.assertEqual(len(client.created),1)
                self.sync_same()
                client.after_query=None;runner.change(self.root,plan['id'],lambda p:p.update({flag:False}))
                complete=self.run_with(client);count=len(client.created)
                self.assertEqual(complete['segments'][0]['job'],first)
                project.request_regeneration(self.root,plan['id'],1)
                self.sync_same()
                current=project.read_plan(self.root,plan['id'])
                with self.assertRaises(ValueError):
                    self.refs.update(self.root,plan['id'],self.image,project.fingerprint(current))
                redo=self.run_with(client)
                self.assertEqual(len(client.created),count+1)
                self.assertEqual(redo['segments'][0]['job'],first)
                self.assertEqual(redo['segments'][2]['job'],complete['segments'][2]['job'])

    def test_capture_matches_prepare_rounding_and_rejects_batch_and_outside_paths(self):
        import torch
        with patch.dict(sys.modules,folder_paths=types.SimpleNamespace(get_temp_directory=lambda:self.temp.name)):
            tensor=torch.full((1,3,4,3),0.503)
            capture=self.refs.ReferenceCapture().capture(tensor)['ui']['images'][0]
            image=self.refs.read_capture(capture)
            self.assertEqual(image.getpixel((0,0)),(128,128,128))
            with self.assertRaises(ValueError):self.refs.ReferenceCapture().capture(tensor.repeat(2,1,1,1))
            with self.assertRaises(ValueError):self.refs.read_capture({'type':'temp','filename':'../outside.png'})
            with self.assertRaises(ValueError):self.refs.read_capture({'type':'output','filename':'video.mp4'})

    def test_public_node_resume_after_sync_still_rejects_changed_audio(self):
        import torch
        from test_talking_flat_rh import node,source
        with patch.dict(sys.modules,folder_paths=types.SimpleNamespace(get_output_directory=lambda:self.temp.name)),patch.object(node,'Client') as client:
            client.return_value.workflow.return_value=source()
            args=dict(audio={'waveform':torch.zeros(1,1,400001),'sample_rate':24000},reference_image=torch.zeros(1,32,32,3),
                      backend='RunningHub',workflow_id='123',width=768,height=1376,fixed_prompt=flat.DEFAULT_PROMPT,generation_strategy=flat.MODE)
            pid=node.TalkingAvatar().prepare(**args)['result'][0]
            old=project.read_plan(node.root(),pid)
            old.update(approved=True,approved_fingerprint=project.fingerprint(old))
            project.write_plan(node.root(),old)
            args['reference_image']=torch.ones(1,32,32,3)
            with patch.object(flat,'segment',side_effect=AssertionError('must not reanalyse')):
                review=node.TalkingAvatar().prepare(**args,project_id=pid)
            self.assertTrue(review['ui']['talking_rh_open_review'][0])
            self.assertEqual(project.read_plan(node.root(),pid),old)
            updated=self.refs.update(node.root(),pid,Image.new('RGB',(32,32),'white'),project.fingerprint(old))['plan']
            args['reference_image']=torch.ones(1,32,32,3)
            with patch.object(flat,'segment',side_effect=AssertionError('must not reanalyse')):
                self.assertEqual(node.TalkingAvatar().prepare(**args,project_id=pid)['result'][0],pid)
            args['audio']['waveform']=torch.ones(1,1,400001)
            with self.assertRaisesRegex(ValueError,'音频'):
                node.TalkingAvatar().prepare(**args,project_id=pid)
            self.assertEqual(project.read_plan(node.root(),pid),updated)
            client.return_value.create.assert_not_called()

    def test_local_canvas_after_reference_update_keeps_models_cuts_and_blocked_outputs(self):
        import torch
        from test_talking_flat_local import expanded,node
        src=expanded();inverse={slot:i for i,slot in enumerate(node.TalkingAvatar.LEGACY_SLOTS)}
        for n in src.values():
            for key,value in n['inputs'].items():
                if isinstance(value,list) and len(value)==2 and value[0]=='5':n['inputs'][key]=['5',inverse[value[1]]]
        with patch.dict(sys.modules,{
            'folder_paths':types.SimpleNamespace(get_output_directory=lambda:self.temp.name),
            'comfy_execution.graph':types.SimpleNamespace(ExecutionBlocker=lambda msg:('blocked',msg))}),\
             patch.object(node,'Client',side_effect=AssertionError('must not call RH')):
            args=dict(audio={'waveform':torch.zeros(1,1,400001),'sample_rate':24000},
                      reference_image=torch.zeros(1,301,101,3),width=96,height=160,backend='本地',
                      fixed_prompt=flat.DEFAULT_PROMPT,generation_strategy=flat.MODE,unique_id='5',prompt=src)
            pid=node.TalkingAvatar().prepare(**args)['result'][0]
            old=project.read_plan(node.root(),pid)
            new=self.refs.update(node.root(),pid,Image.new('RGB',(101,301),'white'),project.fingerprint(old))['plan']
            args['reference_image']=torch.ones(1,301,101,3)
            with patch.object(flat,'segment',side_effect=AssertionError('must not reanalyse')):
                result=node.TalkingAvatar().prepare(**args,project_id=pid)
            self.assertTrue(all(r==('blocked',None) for r in result['result'][1:]))
            self.assertEqual(old['config'],new['config'])
            self.assertEqual(old['segments_clock'],new['segments_clock'])
            self.assertEqual(project.read_plan(node.root(),pid),new)


class ReferenceRouteTests(unittest.IsolatedAsyncioTestCase):
    setUp=ReferenceSyncTests.setUp

    async def invoke(self,method,url,data=None,kind=None,action=None):
        routes=importlib.import_module('h3talk_test.talking_rh.routes');handlers={}
        class Router:
            def get(self,path):return self.add('GET',path)
            def post(self,path):return self.add('POST',path)
            def add(self,verb,path):
                def record(fn):handlers[(verb,path)]=fn;return fn
                return record
        server=types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=Router())))
        with patch.dict(sys.modules,server=server,folder_paths=types.SimpleNamespace(get_temp_directory=lambda:self.temp.name)),\
             patch.object(routes,'root',return_value=self.root),\
             patch.object(routes.rh_credentials,'public_settings',return_value={'configured':True}),\
             patch.object(runner,'start',side_effect=AssertionError('must not generate')):
            routes.register()
            req=types.SimpleNamespace(path='/h3talk/test',match_info={'pid':self.plan['id'],'kind':kind,'action':action},json=AsyncMock(return_value=data))
            return await handlers[(method,url)](req)

    async def test_real_reference_route_and_preview_use_new_asset_no_analysis(self):
        capture=Path(self.temp.name)/'h3talk-reference-test.png';self.image.save(capture)
        result=await self.invoke('POST','/h3talk/project/{pid}/reference',
            {'image':{'filename':capture.name,'subfolder':'','type':'temp'},'expected_fingerprint':project.fingerprint(self.plan)})
        self.assertEqual(result.status,200,result.text)
        new=json.loads(result.text)['plan']
        self.assertEqual(new['manual_cuts'],self.plan['manual_cuts'])
        preview=await self.invoke('GET','/h3talk/project/{pid}/file/{kind}',kind='reference')
        self.assertEqual(preview.status,200)
        self.assertEqual(preview._path.name,new['assets'][0]['name'])
        self.assertNotEqual(preview._path.name,'reference_0.png')
        stale=await self.invoke('POST','/h3talk/project/{pid}/{action}',{'expected_fingerprint':'old'},action='run')
        self.assertEqual(stale.status,400)
        old_ui=await self.invoke('POST','/h3talk/project/{pid}/{action}',{},action='run')
        self.assertEqual(old_ui.status,400)
        self.assertIn('刷新',json.loads(old_ui.text)['error'])


if __name__=='__main__':unittest.main()
