import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
import shutil
import subprocess

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
package=types.ModuleType('h3talk_test');package.__path__=[str(ROOT)]
sys.modules.setdefault('h3talk_test',package)
project=importlib.import_module('h3talk_test.talking_rh.project')
graph=importlib.import_module('h3talk_test.talking_rh.graph')
runner=importlib.import_module('h3talk_test.talking_rh.runner')
remote=importlib.import_module('h3talk_test.talking_rh.remote')


class ClockTests(unittest.TestCase):
    def test_accepted_complete_audio_clocks_and_last_13_samples(self):
        for samples,cuts,kept,first_gen in [(980992,[311,447,583,719,855],[311,136,136,136,136,126],328),
                                          (1046013,[294,430,566,702,838,974],[294,136,136,136,136,136,72],311)]:
            rows=project.clock(samples,cuts)
            self.assertEqual([r['edit_frames'] for r in rows],kept)
            self.assertEqual(rows[0]['generation_frames'],first_gen)
            self.assertEqual(rows[-1]['end_sample'],samples)
            self.assertEqual(sum(r['edit_frames'] for r in rows),round(samples/1000))
            self.assertEqual(rows[1]['audio_start_sample'],(cuts[0]-39)*1000)

    def test_tail_follows_actual_crop_and_keep_not_generated_end(self):
        rows=project.clock(400000,[141,243])
        self.assertEqual(rows[1]['generation_frames'],175)
        self.assertEqual(rows[1]['actual_last_retained_frame'],140)
        self.assertEqual(rows[1]['tail_token_start'],30)
        self.assertNotEqual(rows[1]['tail_token_start'],40)

    def test_invalid_phase_order_range_and_length_rejected(self):
        for samples,cuts in [(277000,[142]),(277000,[141,124]),(277000,[141,141]),(277000,[39]),(500000,[345]),(800000,[141])]:
            with self.subTest(cuts=cuts),self.assertRaises(ValueError):project.clock(samples,cuts)

    def test_short_single_and_alignment(self):
        self.assertEqual(project.clock(24000,[])[0]['generation_frames'],175)
        self.assertEqual(project.snap_cuts([6.0],277000),[141])
        self.assertEqual(project.dimensions(1.0),(768,1376))


class FakeClient:
    def __init__(self):self.created=[];self.downloads=[];self.fail_create=False;self.fail_download=False;self.after_query=None
    def upload(self,path,*,attempt=''):return 'uploaded_'+project.digest(path)+('_'+attempt if attempt else '')
    def create(self,value):
        self.created.append(value)
        if self.fail_create:raise remote.RemoteError('Unconfirmed creation')
        return str(1000+len(self.created))
    def query(self,task_id):
        if self.after_query:self.after_query(task_id)
        return {'status':'SUCCESS','results':[{'nodeId':'273','outputType':'mp4','url':'https://example.test/raw'},
                                             {'nodeId':'9200','outputType':'latent','url':'https://example.test/latent'}]}
    def download(self,url,target):
        self.downloads.append(url)
        if self.fail_download:raise remote.RemoteError('Download interrupted')
        if not Path(target).exists():Path(target).write_bytes(url.encode())


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'projects'
        cfg={'base_url':'https://www.runninghub.ai','workflow_id':'123','instance_type':'ultra',
             'megapixels':1.0,'seed':999,'first_seconds':6.0,'segment_seconds':5.667,
             'template_sha256':project.digest(graph.TEMPLATE)}
        wave=np.sin(np.arange(277000,dtype='float32')*.01)[:,None]*.1
        mask=np.zeros((64,32),dtype='float32');mask[10:55,5:27]=1
        self.plan=project.prepare(self.root,wave,24000,[Image.new('RGB',(32,64),'gray')],mask,cfg,'input')
        self.pid=self.plan['id'];self.dir=project.project_path(self.root,self.pid)
        project.update_cuts(self.root,self.pid,[141/24])
        self.approve()
        self.client=FakeClient()

    def approve(self):
        p=project.read_plan(self.root,self.pid);p.update(approved=True,approved_fingerprint=project.fingerprint(p));project.write_plan(self.root,p)

    def run_project(self):
        def assemble(root,pid):
            path=project.project_path(root,pid)/'final.mp4';path.write_bytes(b'assembled');return str(path)
        runner.run_project(self.root,self.pid,self.client,validate=lambda *args:{'actual_seed':999,'audio_zero_mask':True},assemble=assemble,sleep=lambda _:None)

    def test_approval_blocks_cloud_calls(self):
        p=project.read_plan(self.root,self.pid);p['approved']=False;project.write_plan(self.root,p)
        with self.assertRaises(ValueError):self.run_project()
        self.assertEqual(self.client.created,[])

    def test_real_orchestrator_sequence_and_resume_do_not_regenerate(self):
        self.run_project();self.assertEqual(len(self.client.created),2)
        self.run_project();self.assertEqual(len(self.client.created),2)
        p=project.read_plan(self.root,self.pid)
        self.assertEqual(p['run_status'],'completed')
        self.assertEqual(p['segments'][1]['job']['edit_start_frame'],39)
        self.assertEqual(self.client.created[1]['9110']['inputs']['latent'],'uploaded_'+project.digest(p['segments'][0]['job']['latent']))
        self.assertEqual(self.client.created[1]['9111']['inputs']['index'],30)

    def test_unknown_creation_is_persistent_and_never_automatically_retried(self):
        self.client.fail_create=True
        with self.assertRaises(remote.RemoteError):self.run_project()
        self.client.fail_create=False
        self.assertEqual(project.read_plan(self.root,self.pid)['segments'][0]['job']['status'],'creating_unknown')
        with self.assertRaises(ValueError):self.run_project()
        self.assertEqual(len(self.client.created),1)

    def test_download_failure_resumes_same_task(self):
        self.client.fail_download=True
        with self.assertRaises(remote.RemoteError):self.run_project()
        first_id=project.read_plan(self.root,self.pid)['segments'][0]['job']['task_id']
        self.client.fail_download=False;self.run_project()
        self.assertEqual(len(self.client.created),2)
        self.assertEqual(project.read_plan(self.root,self.pid)['segments'][0]['job']['task_id'],first_id)

    def test_pause_finishes_current_only_then_resume(self):
        def pause(_):runner.change(self.root,self.pid,lambda p:p.update(pause_requested=True))
        self.client.after_query=pause
        self.run_project();p=project.read_plan(self.root,self.pid)
        self.assertEqual(len(self.client.created),1);self.assertEqual(p['run_status'],'paused')
        self.assertEqual(p['segments'][0]['job']['status'],'completed')
        self.client.after_query=None;runner.change(self.root,self.pid,lambda p:p.update(pause_requested=False))
        self.run_project();self.assertEqual(len(self.client.created),2)

    def test_changed_upstream_preserves_files_and_invalidates_dependents(self):
        self.run_project();old=project.read_plan(self.root,self.pid)
        p=project.update_cuts(self.root,self.pid,[158/24])
        self.assertFalse(p['approved']);self.assertNotIn('job',p['segments'][0]);self.assertNotIn('job',p['segments'][1])
        self.assertTrue(Path(old['segments'][0]['job']['video']).is_file())
        self.assertEqual(p['segments'][0]['previous_jobs'][0]['task_id'],old['segments'][0]['job']['task_id'])

    def test_tampered_completed_file_stops_without_new_task(self):
        self.run_project();p=project.read_plan(self.root,self.pid)
        Path(p['segments'][0]['job']['latent']).write_bytes(b'tampered')
        with self.assertRaises(ValueError):self.run_project()
        self.assertEqual(len(self.client.created),2)

    def test_cross_process_lock_prevents_second_claim(self):
        handle=runner.claim(self.dir);self.addCleanup(handle.close)
        with self.assertRaises(ValueError):runner.claim(self.dir)

    def test_multi_reference_graph_uses_all_references_and_audio_zero_mask(self):
        p=project.read_plan(self.root,self.pid);row=p['segments'][0]
        value=graph.build(p,row,{'audio':'a','references':['i0','i1','i2'],'mask':'m','attempt':'test'})
        self.assertEqual(value['268']['class_type'],'MiniMaxH3ReferenceToVideo')
        self.assertEqual(len([k for k in value['268']['inputs'] if k.startswith('ref_images.')]),3)
        self.assertEqual(value['249']['inputs']['value'],0)
        self.assertEqual(value['270']['class_type'],'SelfLiftAvatarH3Sampler')

    def test_invalid_mask_does_not_leave_an_orphan_audio_project(self):
        root=self.root.parent/'invalid'
        with self.assertRaises(ValueError):
            project.prepare(root,np.zeros((10000,1),dtype='float32'),24000,[Image.new('RGB',(32,64))],
                            np.zeros((64,32),dtype='float32'),self.plan['config'],'input')
        self.assertFalse(root.exists())

    def test_native_loader_near_white_is_a_valid_generation_mask(self):
        root=self.root.parent/'near-white'
        mask=np.zeros((64,32),dtype='float32');mask[10:55,5:27]=65280/65535
        plan=project.prepare(root,np.zeros((10000,1),dtype='float32'),24000,[Image.new('RGB',(32,64))],
                             mask,self.plan['config'],'input')
        value=np.asarray(Image.open(project.project_path(root,plan['id'])/'refs/person_mask.png'))
        self.assertEqual(int(value.max()),255)
        self.assertEqual(int(value.min()),0)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg needed')
class RealAssemblyTests(unittest.TestCase):
    def test_real_assembly_trims_context_and_preserves_legacy_first_clip(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'projects';pid='b'*32;directory=project.project_path(root,pid)
            for name in ('state','takes','audio'):(directory/name).mkdir(parents=True,exist_ok=True)
            frames=np.zeros((48,32,32,3),dtype='uint8');frames[:,:,:,1]=100;frames[:,:,:,2]=200
            frames[:,:,:,0]=np.arange(48,dtype='uint8')[:,None,None]*4
            raw=directory/'takes/source.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','rgb24','-s','32x32','-r','24','-i','pipe:0',
                            '-c:v','libx264','-crf','0','-pix_fmt','yuv420p',str(raw)],input=frames.tobytes(),check=True)
            sf.write(directory/'audio/source.wav',np.zeros(10000,dtype='float32'),24000)
            plan={'id':pid,'created':1,'mode':'speaking','duration':10/24,'segments':[
                {'index':0,'edit_frames':5,'job':{'status':'completed','video':str(raw)}},
                {'index':1,'edit_frames':5,'job':{'status':'completed','video':str(raw),'edit_start_frame':39}}]}
            project.write_plan(root,plan)
            final=runner.controller.assemble(root,pid)
            pixels=subprocess.check_output(['ffmpeg','-v','error','-i',final,'-f','rawvideo','-pix_fmt','rgb24','-'])
            value=np.frombuffer(pixels,dtype='uint8').reshape(-1,32,32,3)
            self.assertEqual(len(value),10)
            self.assertLess(abs(float(value[0,:,:,0].mean())-0),6)
            self.assertLess(abs(float(value[5,:,:,0].mean())-156),6)


if __name__=='__main__':unittest.main()
