import copy
import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_talking_rh as rh
from test_talking_rh import project, runner, remote


class Client(rh.FakeClient):
    def __init__(self):
        super().__init__()
        self.queries=[]
        self.provider_failure=False

    def reserve(self):return 'local-'+str(len(self.created)+1)

    def create(self,value,task_id=None):
        result=super().create(value)
        return task_id or result

    def query(self,task_id):
        self.queries.append(task_id)
        if self.provider_failure:return {'status':'FAILED'}
        result=super().query(task_id)
        for item in result['results']:item['url']+='/'+task_id
        return result


class RegenerationTests(unittest.TestCase):
    setUp=rh.LifecycleTests.setUp
    approve=rh.LifecycleTests.approve

    def prepare_chain(self,backend='RunningHub'):
        # Three retained segments exercise both the predecessor and descendants.
        import numpy as np
        import soundfile as sf
        p=copy.deepcopy(self.plan)
        sf.write(project.audio_file(self.dir,'source.wav'),np.zeros((400000,1),dtype='float32'),24000,subtype='FLOAT')
        p.update(samples=400000,duration=400000/24000)
        p['source_sha256']=project.digest(project.audio_file(self.dir,'source.wav'))
        p['config']['backend']='local' if backend=='local' else 'runninghub'
        if backend=='local':
            p['config']['models']={k:'test.safetensors' for k in ('local_model','local_clip','local_video_vae','local_audio_vae','local_upscaler')}
        project.write_plan(self.root,p)
        project.update_cuts(self.root,self.pid,[141/24,243/24]);self.approve()
        self.client=Client();self.run_chain()
        self.before=project.read_plan(self.root,self.pid)
        self.old_final=Path(self.before['final_video']).read_bytes()

    def assemble(self,root,pid):
        p=project.read_plan(root,pid)
        folder=Path(root).parent/'final_videos';folder.mkdir(exist_ok=True)
        target=folder/(pid+'_'+str(len(list(folder.glob('*.mp4'))))+'.mp4')
        target.write_bytes(b'assembled:'+b'|'.join(Path(r['job']['video']).read_bytes() for r in p['segments']))
        return str(target)

    def run_chain(self):
        runner.run_project(self.root,self.pid,self.client,validate=lambda *_:{},assemble=self.assemble,sleep=lambda _:None)

    def test_regenerate_preserves_old_files_and_invalidates_only_requested_suffix(self):
        self.prepare_chain()
        p=project.request_regeneration(self.root,self.pid,1)
        self.assertEqual(p['segments'][0],self.before['segments'][0])
        self.assertTrue(all('job' not in r for r in p['segments'][1:]))
        self.assertTrue(p['final_stale']);self.assertTrue(p['approved'])
        self.assertEqual(p['approved_fingerprint'],self.before['approved_fingerprint'])
        for row in self.before['segments']:
            self.assertEqual(project.digest(row['job']['video']),row['job']['video_sha256'])
        self.assertEqual(Path(p['final_video']).read_bytes(),self.old_final)
        self.assertEqual(len(self.client.created),3)

    def test_both_backends_rebuild_suffix_from_new_predecessor_without_overwriting_old_final(self):
        for backend in ('local','RunningHub'):
            with self.subTest(backend=backend):
                self.prepare_chain(backend)
                project.request_regeneration(self.root,self.pid,1)
                self.run_chain();p=project.read_plan(self.root,self.pid)
                self.assertEqual(len(self.client.created),5)
                self.assertEqual(p['segments'][0]['job'],self.before['segments'][0]['job'])
                self.assertEqual(self.client.created[-1]['9110']['inputs']['latent'],
                                 'uploaded_'+p['segments'][1]['job']['latent_sha256'])
                self.assertNotEqual(p['segments'][1]['job']['directory'],self.before['segments'][1]['job']['directory'])
                self.assertNotEqual(p['segments'][1]['job']['latent_sha256'],self.before['segments'][1]['job']['latent_sha256'])
                self.assertNotIn('regeneration',p)
                self.assertEqual(p['regeneration_history'][-1]['outcome'],'completed')
                self.assertEqual(Path(self.before['final_video']).read_bytes(),self.old_final)

    def test_failed_replacement_can_restore_entire_old_chain_and_keep_failed_task_history(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,1)
        self.client.provider_failure=True
        with self.assertRaises(ValueError):self.run_chain()
        failed=project.read_plan(self.root,self.pid)['segments'][1]['job']
        p=project.restore_regeneration(self.root,self.pid)
        self.assertEqual([r['job'] for r in p['segments']],[r['job'] for r in self.before['segments']])
        self.assertIn(failed,p['segments'][1]['previous_jobs'])
        self.assertEqual(p['final_video'],self.before['final_video']);self.assertFalse(p['final_stale'])
        self.assertEqual(Path(p['final_video']).read_bytes(),self.old_final)
        self.client.provider_failure=False;self.run_chain()
        self.assertEqual(len(self.client.created),4)

    def test_download_failure_recovers_same_paid_task_after_restart(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,1)
        self.client.fail_download=True
        with self.assertRaises(remote.RemoteError):self.run_chain()
        task=project.read_plan(self.root,self.pid)['segments'][1]['job']['task_id']
        self.client.fail_download=False;self.run_chain()
        p=project.read_plan(self.root,self.pid)
        self.assertEqual(p['segments'][1]['job']['task_id'],task)
        self.assertEqual(len(self.client.created),5)

    def test_known_success_with_download_failure_can_restore_without_losing_task(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,1)
        self.client.fail_download=True
        with self.assertRaises(remote.RemoteError):self.run_chain()
        failed=project.read_plan(self.root,self.pid)['segments'][1]['job']
        p=project.restore_regeneration(self.root,self.pid)
        self.assertEqual(failed['provider_status'],'SUCCESS')
        self.assertIn(failed,p['segments'][1]['previous_jobs'])

    def test_unknown_creation_blocks_restore_new_redo_and_cut_changes_without_losing_intent(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,1)
        self.client.fail_create=True
        with self.assertRaises(remote.RemoteError):self.run_chain()
        before=project.read_plan(self.root,self.pid)
        for action in (lambda:project.restore_regeneration(self.root,self.pid),
                       lambda:project.request_regeneration(self.root,self.pid,0),
                       lambda:project.update_cuts(self.root,self.pid,[158/24,260/24])):
            with self.assertRaises(ValueError):action()
            self.assertEqual(project.read_plan(self.root,self.pid),before)
        self.client.fail_create=False
        with self.assertRaises(ValueError):self.run_chain()
        self.assertEqual(len(self.client.created),4)

    def test_uncollected_downstream_job_blocks_redo_before_any_state_changes(self):
        self.prepare_chain();p=project.read_plan(self.root,self.pid)
        p['segments'][2]['job']['status']='submitted';project.write_plan(self.root,p)
        with self.assertRaises(ValueError):project.request_regeneration(self.root,self.pid,1)
        self.assertEqual(project.read_plan(self.root,self.pid),p)

    def test_tampered_old_take_prevents_restore_without_destroying_new_state(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,1)
        Path(self.before['segments'][2]['job']['latent']).write_bytes(b'tampered')
        before=project.read_plan(self.root,self.pid)
        with self.assertRaises(ValueError):project.restore_regeneration(self.root,self.pid)
        self.assertEqual(project.read_plan(self.root,self.pid),before)

    def test_pause_resume_preserves_transaction_and_only_renders_remaining_suffix(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,1)
        self.client.after_query=lambda _:runner.change(self.root,self.pid,lambda p:p.update(pause_requested=True))
        self.run_chain();p=project.read_plan(self.root,self.pid)
        self.assertEqual(p['run_status'],'paused');self.assertIn('regeneration',p)
        self.assertEqual(len(self.client.created),4)
        self.client.after_query=None;runner.change(self.root,self.pid,lambda p:p.update(pause_requested=False))
        self.run_chain();self.assertEqual(len(self.client.created),5)

    def test_merge_failure_can_resume_without_resampling_or_restore_old_chain(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,1)
        with self.assertRaisesRegex(RuntimeError,'merge failed'):
            runner.run_project(self.root,self.pid,self.client,validate=lambda *_:{},
                               assemble=lambda *_:(_ for _ in ()).throw(RuntimeError('merge failed')),sleep=lambda _:None)
        self.assertEqual(Path(self.before['final_video']).read_bytes(),self.old_final)
        self.assertIn('regeneration',project.read_plan(self.root,self.pid))
        self.run_chain();self.assertEqual(len(self.client.created),5)
        self.assertNotIn('regeneration',project.read_plan(self.root,self.pid))

    def test_mutations_refuse_active_controller_and_unapproved_projects(self):
        self.prepare_chain();handle=runner.claim(self.dir)
        try:
            with self.assertRaises(ValueError):project.request_regeneration(self.root,self.pid,1)
        finally:handle.close()
        p=project.read_plan(self.root,self.pid);p['approved']=False;project.write_plan(self.root,p)
        with self.assertRaises(ValueError):project.request_regeneration(self.root,self.pid,1)

    def test_restore_before_start_creates_no_task_and_last_segment_has_no_descendants(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,2)
        project.restore_regeneration(self.root,self.pid)
        self.assertEqual(len(self.client.created),3)
        project.request_regeneration(self.root,self.pid,2);self.run_chain()
        self.assertEqual(len(self.client.created),4)
        p=project.read_plan(self.root,self.pid)
        self.assertEqual(p['segments'][:2],self.before['segments'][:2])

    def test_redo_first_segment_rebuilds_entire_chain_with_original_seed(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,0);self.run_chain()
        self.assertEqual(len(self.client.created),6)
        self.assertTrue(all(g['270']['inputs']['seed']==999 for g in self.client.created))
        p=project.read_plan(self.root,self.pid)
        self.assertTrue(all(a['job']['directory']!=b['job']['directory']
                            for a,b in zip(p['segments'],self.before['segments'])))

    def test_output_validation_failure_allows_restore_of_verified_old_chain(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,1)
        with self.assertRaisesRegex(ValueError,'invalid output'):
            runner.run_project(self.root,self.pid,self.client,
                validate=lambda *_:(_ for _ in ()).throw(ValueError('invalid output')),sleep=lambda _:None)
        p=project.restore_regeneration(self.root,self.pid)
        self.assertEqual([r['job'] for r in p['segments']],[r['job'] for r in self.before['segments']])

    def test_changed_configuration_or_old_final_blocks_restore_without_mutating_plan(self):
        self.prepare_chain();project.request_regeneration(self.root,self.pid,1)
        original=project.read_plan(self.root,self.pid)
        changed=copy.deepcopy(original);changed['config']['seed']=1000;project.write_plan(self.root,changed)
        with self.assertRaises(ValueError):project.restore_regeneration(self.root,self.pid)
        self.assertEqual(project.read_plan(self.root,self.pid),changed)
        project.write_plan(self.root,original);Path(self.before['final_video']).write_bytes(b'changed-final')
        with self.assertRaises(ValueError):project.restore_regeneration(self.root,self.pid)
        self.assertEqual(project.read_plan(self.root,self.pid),original)

    def test_canvas_auto_merge_finalizes_rollback_without_extra_sampling(self):
        self.prepare_chain()
        canvas=importlib.import_module('h3talk_test.talking_rh.canvas')
        raw=json.loads((Path(__file__).parent/'fixtures/talking_canvas_api.json').read_text(encoding='utf-8'))
        p=project.read_plan(self.root,self.pid)
        p['config'].update(backend='local',execution_mode='canvas',canvas=canvas.capture(raw,'5'))
        project.write_plan(self.root,p);self.approve()
        project.request_regeneration(self.root,self.pid,1)
        class CanvasClient(Client):
            def query(self,task_id):
                result=super().query(task_id)
                result['results'].append({'nodeId':'9422','outputType':'mp4','url':'https://example.test/preview/'+task_id})
                return result
        self.client=CanvasClient()
        def probe(path):
            index=int(Path(path).parents[1].name)
            return {'nb_read_frames':str(p['segments'][index]['edit_frames']),'r_frame_rate':'24/1'}
        with patch.object(runner.controller,'probe_video',side_effect=probe):self.run_chain()
        p=project.read_plan(self.root,self.pid)
        self.assertEqual(p['run_status'],'completed');self.assertNotIn('regeneration',p)
        self.assertNotIn('regeneration',project.read_plan(self.root,self.pid))
        self.assertEqual(len(self.client.created),2)
        self.assertEqual(Path(self.before['final_video']).read_bytes(),self.old_final)


class RegenerationRouteTests(unittest.IsolatedAsyncioTestCase):
    setUp=rh.LifecycleTests.setUp
    approve=RegenerationTests.approve
    prepare_chain=RegenerationTests.prepare_chain
    assemble=RegenerationTests.assemble
    run_chain=RegenerationTests.run_chain

    async def post(self,action,data=None):
        routes=importlib.import_module('h3talk_test.talking_rh.routes')
        handlers={}
        class Router:
            def get(self,path):return lambda fn:fn
            def post(self,path):
                def register(fn):handlers['post']=fn;return fn
                return register
        server=types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=Router())))
        with patch.dict(sys.modules,{'server':server}),patch.object(routes,'root',return_value=self.root),\
             patch.object(runner,'make_client',side_effect=AssertionError('No compute during edit')):
            routes.register()
            request=types.SimpleNamespace(match_info={'pid':self.pid,'action':action},json=AsyncMock(return_value=data or {}))
            response=await handlers['post'](request)
        return response.status,json.loads(response.text)

    async def test_real_routes_mark_redo_and_restore_without_starting_compute(self):
        self.prepare_chain()
        status,value=await self.post('regenerate',{'index':1})
        self.assertEqual(status,200);self.assertTrue(value['regeneration_can_restore'])
        self.assertNotIn('job',value['segments'][2]);self.assertEqual(len(self.client.created),3)
        status,value=await self.post('restore')
        self.assertEqual(status,200);self.assertEqual(value['segments'][2]['job'],self.before['segments'][2]['job'])

    async def test_real_retry_route_reruns_failed_replacement_and_then_its_descendants(self):
        self.prepare_chain();await self.post('regenerate',{'index':1})
        self.client.provider_failure=True
        with self.assertRaises(ValueError):self.run_chain()
        status,value=await self.post('retry',{'index':1});self.assertEqual(status,200)
        self.client.provider_failure=False;self.run_chain()
        self.assertEqual(len(self.client.created),6)
        self.assertNotIn('regeneration',project.read_plan(self.root,self.pid))

    async def test_real_unknown_task_recovery_preserves_paid_intent_and_never_recreates_it(self):
        self.prepare_chain();await self.post('regenerate',{'index':1})
        self.client.fail_create=True
        with self.assertRaises(remote.RemoteError):self.run_chain()
        status,value=await self.post('restore');self.assertEqual(status,400)
        status,value=await self.post('recover',{'index':1,'task_id':'9999'});self.assertEqual(status,200)
        self.assertFalse(value['regeneration_can_restore'])
        self.client.fail_create=False;self.run_chain()
        self.assertEqual(project.read_plan(self.root,self.pid)['segments'][1]['job']['task_id'],'9999')
        self.assertEqual(len(self.client.created),5)


if __name__=='__main__':unittest.main()
