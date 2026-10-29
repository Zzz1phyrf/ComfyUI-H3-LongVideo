"""CPU and mocked-provider regression; never submit real generation."""
import copy
import importlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import soundfile as sf
import torch

from test_talking_rh import project,graph,runner,FakeClient,remote

overlap=importlib.import_module('h3talk_test.talking_rh.overlap')
canvas=importlib.import_module('h3talk_test.talking_rh.canvas')
sampling=importlib.import_module('h3talk_test.talking_rh.sampling')
HERE=Path(__file__).parent
def fixture(local=False):
    return json.loads((HERE/'fixtures'/('talking_overlap_canvas_api.json' if local else 'talking_overlap_remote_api.json')).read_text(encoding='utf-8'))
def config(local=False):
    value={'base_url':'https://www.runninghub.ai','workflow_id':'123','instance_type':'ultra',
           'megapixels':1.,'seed':9260,'first_seconds':13.,'segment_seconds':5.667,
           'template_sha256':project.digest(graph.TEMPLATE),'person_mask_mode':'none',
           'talking_profile':overlap.PROFILE,'fixed_prompt':overlap.DEFAULT_PROMPT.strip()}
    if local:value.update(backend='local',execution_mode='canvas',canvas=canvas.capture(fixture(True),'5',independent=True))
    else:value['rh_sampling']=sampling.freeze(fixture())
    return value
def frames(start=0):
    a=np.zeros((175,32,24,3),np.uint8)
    for i in range(175):a[i,18:26,((i+start)//2)%12:((i+start)//2)%12+8]=150
    return a


def legacy_prepare(*args,**kwargs):
    """Retained fixed-window project fixture, intentionally predating pause planning."""
    plan=project.prepare(*args,**kwargs);plan.pop('speech_segmentation',None)
    plan['segments_clock']=overlap.clock(plan['samples']);plan['segments']=copy.deepcopy(plan['segments_clock'])
    project.write_plan(args[0],plan)
    return plan


class WindowTests(unittest.TestCase):
    def test_complete_sample_clock_short_near_duplicate_tail_and_long(self):
        for samples in (1,1000,1001,174999,175000,175001,294000,295000,430000,980992,1046013):
            with self.subTest(samples=samples):
                rows=overlap.clock(samples)
                self.assertEqual(rows[0]['start_sample'],0)
                self.assertEqual(rows[-1]['end_sample'],samples)
                self.assertEqual(sum(r['edit_frames'] for r in rows),math.ceil(samples/1000))
                self.assertEqual([r['end_sample'] for r in rows[:-1]],[r['start_sample'] for r in rows[1:]])
                for row in rows:
                    self.assertEqual(row['generation_frames'],175)
                    self.assertGreater(row['edit_frames'],0)
                    wave=np.arange(samples,dtype=np.float32)[:,None]
                    window=project.condition_audio(wave,row)
                    self.assertEqual(len(window),175000)
                    start=row['audio_start_sample'];count=min(samples-start,len(window))
                    np.testing.assert_array_equal(window[:count],wave[start:start+count])
                if samples>175000:self.assertEqual(rows[-1]['window_end_frame'],math.ceil(samples/1000))

    def test_activity_tracks_translated_subject_without_fixed_hand_roi(self):
        a=frames();b=frames(119)
        first=overlap.choose(a,b,0,119)
        second=overlap.choose(np.roll(a,11,axis=1),np.roll(b,11,axis=1),0,119)
        self.assertEqual(first['frame'],second['frame'])
        self.assertAlmostEqual(first['score'],second['score'])
        self.assertGreater(first['activity_coverage'],0)

    def test_uncertain_motion_is_reported_and_cut_bounds_are_obeyed(self):
        value=overlap.choose(np.zeros((175,8,8,3)),np.zeros((175,8,8,3)),0,119,150,155)
        self.assertEqual(value['frame'],151)
        self.assertTrue(value['warnings'])
        with self.assertRaises(ValueError):overlap.choose(frames(),frames(),0,174)


class GraphTests(unittest.TestCase):
    def test_actual_rh_submission_keeps_k_double_lora_audio_and_no_continuation(self):
        cfg=config();plan={'id':'a'*32,'config':cfg}
        row=overlap.clock(430000)[2]
        value=graph.build(plan,row,{'audio':'new.wav','references':['original.png'],'attempt':'a'*12})
        self.assertEqual(canvas.output_ids(cfg),{'video':'273'})
        kinds={n['class_type'] for n in value.values()}
        self.assertFalse(kinds & {'LoadLatent','SaveLatent','ReplaceVideoLatentFrames','SelfLiftAvatarH3Sampler','VRGDG_MiniMaxH3ApplyLatentGuide'})
        self.assertEqual(value['270']['inputs']['noise_seed'],9260)
        self.assertEqual(value['268']['inputs']['prompt'],cfg['fixed_prompt'])
        self.assertEqual(value['246']['inputs']['video_latent'],['9101',0])
        self.assertEqual(value['250']['inputs']['mask'],['249',0])
        self.assertEqual(value['249']['inputs']['value'],0)
        self.assertEqual([n['inputs']['steps'] for n in value.values() if n['class_type']=='BasicScheduler'],[6])
        self.assertEqual(sorted(n['inputs']['strength_model'] for n in value.values() if n['class_type']=='LoraLoaderModelOnly'),[.6,.8])
        self.assertNotIn('ref_images.ref_image_1',value['268']['inputs'])

    def test_local_canvas_preserves_visible_sampler_and_models(self):
        raw=fixture(True);raw['10008']['inputs']['steps']=8
        self.assertFalse(any(n['class_type']=='LazySwitchKJ' for n in raw.values()))
        raw['11001']['inputs']['strength_model']=.4
        saved=canvas.capture(raw,'5',independent=True);canvas.verify_ref2va(saved)
        plan={'id':'a'*32,'config':{'canvas':saved,'execution_mode':'canvas','talking_profile':overlap.PROFILE,'person_mask_mode':'none'},'assets':[{}]}
        value=graph.build(plan,{'index':2},{'attempt':'a'*12})
        self.assertEqual(value['10008']['inputs']['steps'],8)
        self.assertEqual(value['11001']['inputs']['strength_model'],.4)
        self.assertEqual(value['5']['inputs']['segment_index'],2)
        self.assertEqual(canvas.output_ids(plan['config']),{'video':'273'})

    def test_local_clock_audio_condition_and_sampler_bypasses_are_rejected(self):
        for key,field,value in [('249','value',1),('272','fps',30),('268','length',175),
                ('268','prompt','silently fixed'),('248','audio',['1',0]),('270','noise_seed',-1),
                ('122','vae',['10004',0]),('246','video_latent',['250',0])]:
            with self.subTest(key=key,field=field):
                raw=fixture(True);raw[key]['inputs'][field]=value
                with self.assertRaises(ValueError):canvas.capture(raw,'5',independent=True)

    def test_example_links_and_profile_are_explicit(self):
        for prefix in ('13','14','15'):
            value=json.loads(next((HERE.parent/'examples').glob(prefix+'_*.json')).read_text(encoding='utf-8'))
            nodes={n['id']:n for n in value['nodes']}
            for ident,src,slot,dst,target,kind in value['links']:
                self.assertEqual(nodes[dst]['inputs'][target]['link'],ident)
                self.assertIn(ident,nodes[src]['outputs'][slot]['links'])
            if prefix!='15':
                self.assertEqual(nodes[5]['type'],'H3LVTalkingAvatar')
                self.assertEqual(len(nodes[5]['outputs']),9)
                self.assertEqual([i['name'] for i in nodes[5]['inputs'] if i['type']=='IMAGE'],['reference_image'])
                self.assertFalse(any(i['name'].startswith('reference_') and i['name']!='reference_image' for i in nodes[5]['inputs']))
            else:self.assertFalse(any(n['type'].startswith('H3LV') for n in nodes.values()))
            if prefix=='13':
                self.assertFalse(any(n['type']=='LazySwitchKJ' for n in nodes.values()))
                self.assertTrue(all(output['links'] for output in nodes[5]['outputs'] if output['name']!='project_id'))


class NodeTests(unittest.TestCase):
    def test_external_controls_are_frozen_without_loading_managed_models(self):
        from test_talking_local import node
        raw=fixture(True)
        raw['10008']['inputs']['steps']=8
        raw['11001']['inputs']['strength_model']=.45
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:tmp)),patch.object(node.canvas,'blocked',side_effect=lambda plan:(plan['id'],)),patch.object(node,'validate_models') as managed,patch.object(node,'Client') as remote_client:
            args=dict(audio={'waveform':torch.zeros(1,1,24000),'sample_rate':24000},
                reference_image=torch.zeros(1,64,32,3),backend='本地',execution_mode='画布',
                continuation_mode='独立窗口（自然动作）',megapixels=.8,seed=1234,
                fixed_prompt='External reference composition and natural gestures.',prompt=raw,unique_id='5')
            pid=node.TalkingRH().prepare(**args)['result'][0]
            plan=project.read_plan(node.root(),pid)
            self.assertEqual(plan['config']['seed'],1234)
            self.assertEqual(plan['config']['megapixels'],.8)
            self.assertEqual(plan['config']['fixed_prompt'],args['fixed_prompt'])
            self.assertNotIn('models',plan['config'])
            queued=graph.build(plan,plan['segments'][0],{'attempt':'a'*12})
            self.assertEqual(queued['10008']['inputs']['steps'],8)
            self.assertEqual(queued['11001']['inputs']['strength_model'],.45)
            with self.assertRaisesRegex(ValueError,'新建项目'):
                node.TalkingRH().prepare(**{**args,'project_id':pid,'seed':4321})
            managed.assert_not_called();remote_client.assert_not_called()

    def test_real_prepare_freezes_new_profile_and_resume_does_not_fetch_or_generate(self):
        from test_talking_local import node
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:tmp)),patch.object(node,'Client') as client:
            client.return_value.workflow.return_value=fixture()
            args={'audio':{'waveform':torch.zeros(1,1,430013),'sample_rate':24000},
                  'reference_image':torch.zeros(1,64,32,3),'backend':'RunningHub',
                  'workflow_id':'123','seed':9260,'continuation_mode':'独立窗口（自然动作）'}
            pid=node.TalkingRH().prepare(**args)['result'][0]
            plan=project.read_plan(node.root(),pid)
            self.assertEqual(plan['config']['talking_profile'],overlap.PROFILE)
            self.assertEqual(plan['config']['instance_type'],'ultra');self.assertFalse(plan['approved'])
            self.assertEqual(node.TalkingRH().prepare(**args,project_id=pid)['result'][0],pid)
            self.assertEqual(client.return_value.workflow.call_count,1);client.return_value.create.assert_not_called()
            for change in ({'fixed_prompt':'changed framing'},{'continuation_mode':'旧版latent续接'}):
                with self.assertRaisesRegex(ValueError,'新建项目'):
                    node.TalkingRH().prepare(**{**args,**change,'project_id':pid})

    def test_later_local_window_loads_original_reference_and_source_without_previous_latent(self):
        from test_talking_local import node
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'projects';wave=np.arange(430013,dtype=np.float32)[:,None]/430013
            plan=project.prepare(root,wave,24000,[Image.new('RGB',(32,64),'gray')],None,config(True),'input')
            plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(root,plan)
            index=len(plan['segments'])-1;self.assertGreater(index,0)
            with patch.object(node,'root',return_value=root),patch.dict('sys.modules',nodes=types.SimpleNamespace(),
                    folder_paths=types.SimpleNamespace(get_output_directory=lambda:str(root.parent))):
                state=canvas.SegmentState().load(plan['id'],index,'a'*12)
            self.assertIsNone(state[canvas.SLOT['previous_latent']]);self.assertFalse(state[canvas.SLOT['has_previous']])
            self.assertFalse(state[canvas.SLOT['use_endpoint']])
            self.assertEqual(state[canvas.SLOT['fixed_prompt']],plan['config']['fixed_prompt'])
            actual=state[canvas.SLOT['condition_audio']]['waveform'].numpy()[0].T
            np.testing.assert_array_equal(actual,project.condition_audio(wave,plan['segments'][index]))
            self.assertEqual(state[canvas.SLOT['generation_frames']],plan['segments'][index]['generation_frames'])

    def test_rh_request_uses_selected_instance_and_actual_native_k_seed(self):
        with patch.dict('os.environ',{'RUNNINGHUB_API_KEY':'offline-only'}):client=remote.Client(config())
        value=graph.build({'id':'a'*32,'config':config()},overlap.clock(200000)[0],
                          {'audio':'a.wav','references':['r.png'],'attempt':'a'*12})
        with patch.object(client,'_post',return_value={'code':0,'data':{'taskId':'77'}}) as post:
            client.create(value)
        payload=post.call_args.args[1]
        self.assertEqual(payload['instanceType'],'ultra')
        self.assertEqual(payload['nodeInfoList'],[{'nodeId':'270','fieldName':'noise_seed','fieldValue':9260}])


class Client(FakeClient):
    def query(self,task_id):
        if self.after_query:self.after_query(task_id)
        return {'status':'SUCCESS','results':[{'nodeId':'273','outputType':'mp4','url':'https://example.test/'+task_id}]}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'projects'
        self.plan=legacy_prepare(self.root,np.zeros((430000,1),np.float32),24000,
            [Image.new('RGB',(32,64),'gray')],None,config(),'input')
        self.pid=self.plan['id'];self.directory=project.project_path(self.root,self.pid)
        self.approve();self.client=Client()

    def approve(self):
        p=project.read_plan(self.root,self.pid);p.update(approved=True,approved_fingerprint=project.fingerprint(p));project.write_plan(self.root,p)

    def run_project(self,assemble=None):
        resolve=overlap.resolve
        def merge(*_):
            final=self.directory/f'final-{len(list(self.directory.glob("final-*.mp4")))}.mp4'
            final.write_bytes(b'assembled test video');return str(final)
        with patch.object(overlap,'resolve',side_effect=lambda root,plan:resolve(root,plan,decode_frames=lambda *_:frames())):
            runner.run_project(self.root,self.pid,self.client,validate=lambda *_:{},assemble=assemble or merge,sleep=lambda _:None)
        return project.read_plan(self.root,self.pid)

    def test_manual_drag_invalidates_only_changed_audio_intervals_and_keeps_old_media(self):
        old=self.run_project();cuts=old['overlap_cuts'][1:-1]
        changed=[(cuts[0]+1)/24,*[f/24 for f in cuts[1:]]]
        updated=project.update_cuts(self.root,self.pid,changed)
        project.verify_clock(updated)
        self.assertNotIn('job',updated['segments'][0]);self.assertNotIn('job',updated['segments'][1])
        self.assertEqual(updated['segments'][2]['job'],old['segments'][2]['job'])
        self.assertFalse(updated['approved']);self.assertTrue(updated['final_stale'])
        self.assertEqual(overlap.interval(updated,0),(0,cuts[0]+1))
        self.assertTrue(all(Path(row['job']['video']).exists() for row in old['segments']))
        self.assertEqual(len(self.client.created),3)

    def test_manual_remove_and_add_cut_remaps_untouched_take_and_keeps_first_prompt(self):
        old=self.run_project();old['segments'][0]['prompt_draft']={'text':'First segment text','sha256':'draft'}
        project.write_plan(self.root,old)
        cut=old['overlap_cuts'][2]
        merged=project.update_cuts(self.root,self.pid,[cut/24])
        self.assertEqual(len(merged['segments']),2)
        self.assertEqual(merged['segments'][0]['prompt_draft']['text'],'First segment text')
        self.assertEqual(merged['segments'][1]['job'],old['segments'][2]['job'])
        self.assertGreater(merged['segments'][0]['generation_frames'],175)
        split=project.update_cuts(self.root,self.pid,[6,cut/24])
        project.verify_clock(split)
        self.assertEqual(len(split['segments']),3)
        self.assertEqual(split['segments'][2]['job'],old['segments'][2]['job'])
        self.assertEqual(split['segments'][1]['prompt_draft']['text'],'First segment text')
        self.assertTrue(list((self.directory/'state').glob('before-cuts-*.json')))

    def test_manual_cuts_validate_before_mutation_and_noop_keeps_approved_results(self):
        old=self.run_project();before=(self.directory/'state/segments.json').read_bytes()
        for cuts in ([float('nan')],[1],[16],[],[6,6]):
            with self.subTest(cuts=cuts),self.assertRaises(ValueError):project.update_cuts(self.root,self.pid,cuts)
            self.assertEqual((self.directory/'state/segments.json').read_bytes(),before)
        same=project.update_cuts(self.root,self.pid,[f/24 for f in old['overlap_cuts'][1:-1]])
        self.assertEqual(same,old)

    def test_video_only_tasks_preview_intervals_and_resume_without_generation(self):
        p=self.run_project();self.assertEqual(len(self.client.created),3)
        self.assertEqual(p['run_status'],'completed');self.assertTrue(Path(p['final_video']).is_file())
        self.assertEqual(len(p['overlap_cuts']),4)
        for i,row in enumerate(p['segments']):
            self.assertNotIn('latent',row['job']);project.verify_saved_job(self.directory,row['job'])
            self.assertEqual(list(overlap.interval(p,i)),p['overlap_cuts'][i:i+2])
        self.run_project();self.assertEqual(len(self.client.created),3)

    def test_single_window_redo_keeps_other_takes_and_restores_seams_after_failure(self):
        old=self.run_project();final=self.directory/'old.mp4';final.write_bytes(b'old')
        old.update(final_video=str(final),run_status='completed');project.write_plan(self.root,old)
        redo=project.request_regeneration(self.root,self.pid,1)
        self.assertEqual(redo['segments'][0]['job'],old['segments'][0]['job'])
        self.assertEqual(redo['segments'][2]['job'],old['segments'][2]['job'])
        self.assertEqual(redo['overlap_seams'],{});self.assertTrue(redo['final_stale'])
        self.assertEqual(final.read_bytes(),b'old')
        redo['segments'][1]['job']={'status':'failed'};project.write_plan(self.root,redo)
        restored=project.restore_regeneration(self.root,self.pid)
        self.assertEqual(restored['overlap_seams'],old['overlap_seams'])
        self.assertEqual(restored['overlap_cuts'],old['overlap_cuts'])
        self.assertEqual(restored['final_video'],str(final))

    def test_redo_only_submits_one_new_task_and_keeps_unaffected_seam_cache(self):
        old=self.run_project();project.request_regeneration(self.root,self.pid,0)
        current=self.run_project();self.assertEqual(len(self.client.created),4)
        self.assertEqual(old['overlap_seams']['2'],current['overlap_seams']['2'])
        self.assertNotEqual(old['overlap_seams']['1']['identity'],current['overlap_seams']['1']['identity'])
        self.assertEqual(old['segments'][2]['job'],current['segments'][2]['job'])

    def test_download_interruption_reuses_known_task_id(self):
        self.client.fail_download=True
        with self.assertRaises(remote.RemoteError):self.run_project()
        task=project.read_plan(self.root,self.pid)['segments'][0]['job']['task_id']
        self.client.fail_download=False;p=self.run_project()
        self.assertEqual(len(self.client.created),3);self.assertEqual(p['segments'][0]['job']['task_id'],task)

    def test_unknown_creation_blocks_duplicate_charge(self):
        self.client.fail_create=True
        with self.assertRaises(remote.RemoteError):self.run_project()
        self.client.fail_create=False
        with self.assertRaises(ValueError):self.run_project()
        self.assertEqual(len(self.client.created),1)

    def test_approval_and_clock_tampering_block_submission(self):
        p=project.read_plan(self.root,self.pid);p['approved']=False;project.write_plan(self.root,p)
        with self.assertRaises(ValueError):self.run_project()
        self.approve();p=project.read_plan(self.root,self.pid);p['segments'][1]['audio_start_sample']+=1000;project.write_plan(self.root,p)
        with self.assertRaises(ValueError):self.run_project()
        self.assertFalse(self.client.created)

    def test_nearly_duplicate_tail_windows_still_choose_ordered_cuts(self):
        for samples in (175001,295000,431000):
            p=project.read_plan(self.root,self.pid);p['samples']=samples
            p['segments_clock']=overlap.clock(samples);p['segments']=copy.deepcopy(p['segments_clock'])
            for r in p['segments']:
                path=self.directory/f'{samples}_{r["index"]}.mp4';path.write_bytes(str(r['index']).encode())
                r['job']={'status':'completed','video':str(path),'video_sha256':project.digest(path)}
            current=overlap.resolve(self.root,p,decode_frames=lambda *_:frames())
            self.assertTrue(all(a<b for a,b in zip(current['overlap_cuts'],current['overlap_cuts'][1:])))

    def test_corrupt_completed_video_blocks_preview_and_merge(self):
        p=self.run_project();Path(p['segments'][0]['job']['video']).write_bytes(b'corrupted')
        with self.assertRaises(ValueError):overlap.preview(self.root,self.pid,0,raw=True)
        with self.assertRaises(ValueError):canvas.assemble_project(self.root,self.pid)

    def test_new_local_task_output_contract_has_no_latent_and_auto_merges(self):
        p=project.read_plan(self.root,self.pid);p['config']=config(True);project.write_plan(self.root,p);self.approve()
        class Local(Client):
            def reserve(self):return 'local-'+str(len(self.created))
            def create(self,value,task_id):self.created.append(value);return task_id
        self.client=Local();p=self.run_project()
        self.assertEqual(p['run_status'],'completed');self.assertEqual(len(self.client.created),3)
        self.assertTrue(all(g['5']['class_type']=='H3LVTalkingSegmentState' for g in self.client.created))

    def test_merge_failure_keeps_old_result_and_restorable_transaction(self):
        old=self.run_project();final=self.directory/'old.mp4';final.write_bytes(b'old')
        old.update(final_video=str(final),run_status='completed');project.write_plan(self.root,old)
        project.request_regeneration(self.root,self.pid,1)
        with self.assertRaisesRegex(RuntimeError,'disk full'):
            self.run_project(assemble=lambda *_:(_ for _ in ()).throw(RuntimeError('disk full')))
        p=project.read_plan(self.root,self.pid)
        self.assertEqual(p['final_video'],str(final));self.assertTrue(p['final_stale']);self.assertIn('regeneration',p)
        self.assertEqual(project.restore_regeneration(self.root,self.pid)['final_video'],str(final))

    def test_legacy_completed_job_still_requires_latent(self):
        path=self.directory/'raw.mp4';path.write_bytes(b'raw')
        with self.assertRaisesRegex(ValueError,'latent'):
            project.verify_saved_job(self.directory,{'video':str(path),'video_sha256':project.digest(path)})


class MediaTests(unittest.TestCase):
    def test_manual_segments_use_variable_lengths_and_keep_the_user_cut_during_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'projects';samples=430013
            wave=np.sin(np.arange(samples,dtype=np.float32)*.013)[:,None]*.1
            plan=project.prepare(root,wave,24000,[Image.new('RGB',(32,64),'gray')],None,config(),'input')
            plan=project.update_cuts(root,plan['id'],[8.0]);directory=project.project_path(root,plan['id'])
            for row in plan['segments']:
                path=directory/f'manual{row["index"]}.mp4'
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=gray:s=48x86:r=24',
                    '-frames:v',str(row['generation_frames']),'-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
                row['job']={'status':'completed','video':str(path),'video_sha256':project.digest(path),'talking_profile':overlap.PROFILE}
                submitted=graph.build(plan,row,{'audio':'speech.wav','references':['portrait.png'],'attempt':'a'*12})
                self.assertEqual(submitted['268']['inputs']['length'],row['generation_frames'])
            plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(root,plan)
            resolved=overlap.resolve(root,plan)
            self.assertEqual(resolved['overlap_cuts'],[0,192,431]);self.assertEqual(overlap.interval(resolved,1),(192,431))
            with patch.object(remote.Client,'create',side_effect=AssertionError('CPU edit/merge must not generate')):
                final=canvas.assemble_project(root,plan['id'])
            self.assertEqual(int(runner.controller.probe_video(final)['nb_read_frames']),431)
            pcm=subprocess.check_output(['ffmpeg','-v','error','-i',final,'-vn','-ac','1','-ar','24000','-f','f32le','pipe:1'])
            self.assertGreater(np.corrcoef(np.frombuffer(pcm,np.float32)[:samples],wave[:,0])[0,1],.98)

    def test_cpu_window_preview_and_full_audio_tail_assembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'projects';samples=294013
            wave=np.sin(np.arange(samples,dtype=np.float32)*.01)[:,None]*.1
            plan=legacy_prepare(root,wave,24000,[Image.new('RGB',(32,64),'gray')],None,config(),'input')
            directory=project.project_path(root,plan['id'])
            for row in plan['segments']:
                path=directory/f'fixture{row["index"]}.mp4'
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=gray:s=48x86:r=24',
                    '-frames:v','175','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
                row['job']={'status':'completed','video':str(path),'video_sha256':project.digest(path),'talking_profile':overlap.PROFILE}
            plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(root,plan)
            plan=overlap.resolve(root,plan)
            for i in range(len(plan['segments'])):
                raw=canvas.segment_preview(root,plan['id'],i,raw=True);used=canvas.segment_preview(root,plan['id'],i)
                self.assertNotEqual(raw,used)
                self.assertEqual(int(runner.controller.probe_video(used)['nb_read_frames']),overlap.interval(plan,i)[1]-overlap.interval(plan,i)[0])
            with patch.object(remote.Client,'create',side_effect=AssertionError('merge must not generate')):
                final=canvas.assemble_project(root,plan['id'])
            self.assertEqual(int(runner.controller.probe_video(final)['nb_read_frames']),295)
            info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','a','-show_entries','stream=duration','-of','json',final]))
            self.assertGreaterEqual(float(info['streams'][0]['duration']),samples/24000)
            pcm=subprocess.check_output(['ffmpeg','-v','error','-i',final,'-vn','-ac','1','-ar','24000','-f','f32le','pipe:1'])
            decoded=np.frombuffer(pcm,np.float32)
            self.assertGreaterEqual(len(decoded),samples)
            self.assertGreater(np.corrcoef(decoded[:samples],wave[:,0])[0,1],.98)
            self.assertEqual(canvas.assemble_project(root,plan['id']),final)
            self.assertEqual(project.read_plan(root,plan['id'])['run_status'],'completed')


if __name__=='__main__':unittest.main()
