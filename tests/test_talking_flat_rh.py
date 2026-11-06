"""CPU-only flat strategy checks; provider calls are mocked."""
import copy
import importlib
import json
from pathlib import Path
import tempfile
import subprocess
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from test_talking_rh import project, graph, runner, FakeClient

flat = importlib.import_module('h3talk_test.talking_rh.flat')
overlap = importlib.import_module('h3talk_test.talking_rh.overlap')
prompts = importlib.import_module('h3talk_test.talking_rh.prompts')
node = importlib.import_module('h3talk_test.talking_rh')
HERE = Path(__file__).parent


def source():
    return json.loads((HERE/'fixtures/flat_rh_api.json').read_text(encoding='utf-8'))


def config():
    return dict(base_url='https://www.runninghub.ai', workflow_id='123', instance_type='plus',
                width=768, height=1376, seed=1013, first_seconds=13., segment_seconds=5.667,
                talking_profile=flat.PROFILE, fixed_prompt=flat.DEFAULT_PROMPT,
                person_mask_mode='none', template_sha256=project.digest(graph.TEMPLATE),
                rh_sampling=flat.freeze(source()))


class FlatTests(unittest.TestCase):
    def test_reference_geometry_symmetric_crop_and_configurable_size(self):
        rng=np.random.default_rng(123)
        for size,target,box in [((941,1672),(768,1376),(4,0,937,1672)),
                                ((1024,1536),(768,1376),(83,0,941,1536)),
                                ((768,1376),(768,1376),(0,0,768,1376)),
                                ((101,301),(96,160),(0,66,101,235))]:
            picture=Image.fromarray(rng.integers(0,256,(size[1],size[0],3),dtype=np.uint8))
            expected=picture.crop(box).resize(target,Image.Resampling.LANCZOS)
            self.assertTrue(np.array_equal(np.array(flat.reference_image(picture,target)),np.array(expected)))
        self.assertEqual(flat.reference_image(Image.new('RGB',(100,150)),(640,1152)).size,(640,1152))

    def test_new_assets_normalized_but_unmarked_legacy_assets_unchanged(self):
        picture=Image.fromarray(np.random.default_rng(7).integers(0,256,(1672,941,3),dtype=np.uint8))
        pixels=np.array(picture).copy()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'projects'
            for version in (None,flat.GEOMETRY):
                cfg=config()
                if version:cfg['reference_geometry']=version
                p=project.prepare(root,np.zeros((141000,1),np.float32),24000,[picture],None,cfg,'test')
                project.verify_assets(root,p)
                folder=project.project_path(root,p['id'])/'refs';asset=p['assets'][0]
                saved=Image.open(folder/asset['name'])
                expected=picture if version is None else flat.reference_image(picture,(768,1376))
                self.assertTrue(np.array_equal(np.array(saved),np.array(expected)))
                if version:self.assertTrue(np.array_equal(np.array(Image.open(folder/asset['original']['name'])),pixels))
                else:self.assertNotIn('original',asset)
            self.assertTrue(np.array_equal(np.array(picture),pixels))

    def test_actual_preflight_route_uses_flat_contract_without_generation(self):
        routes=importlib.import_module('h3talk_test.talking_rh.routes')
        with patch('h3talk_test.talking_rh.remote.Client') as client:
            client.return_value.workflow.return_value=source()
            result=routes.check_rh({'region':'海外版','workflow_id':'123','instance_type':'plus','generation_strategy':flat.MODE})
            self.assertEqual(result['generation_strategy'],flat.MODE)
            self.assertFalse(result['submitted'])
            client.return_value.create.assert_not_called();client.return_value.upload.assert_not_called()
    def test_accepted_male_female_clock(self):
        for samples,cuts,lengths in [(1288192,[226,367,508,666,807,914,1072],[226,141,141,158,141,107,158,226]),
                                      (1046013,[192,350,508,632,756,897],[192,158,158,124,124,141,158])]:
            rows=flat.clock(samples,cuts)
            self.assertEqual([r['generation_frames'] for r in rows],lengths)
            self.assertEqual(rows[-1]['end_sample'],samples)
            self.assertEqual(sum(r['edit_frames'] for r in rows),(samples+999)//1000)
            self.assertTrue(all(r['edit_start_frame']==0 and r['start_sample']==r['audio_start_sample'] for r in rows))

    def test_grid_validation_short_tail_and_silent_segmentation(self):
        for samples in [1000,95000,96000,106001,107000,107001,345000,346000,1046013]:
            result=flat.segment(np.zeros((samples,1),np.float32))
            rows=flat.clock(samples,result['cuts'])
            self.assertEqual(rows[-1]['end_sample'],samples)
        for cuts in [[150],[107,107],[124,107],[-1]]:
            with self.assertRaises(ValueError):flat.clock(300000,cuts)

    def test_frozen_cloud_rejects_different_tail_and_wrong_audio(self):
        for change in ('last_frame','guide_audio'):
            s=source()
            if change=='last_frame':s['268']['inputs']['last_frame']=['9001',1]
            else:s['26000']['inputs']['audio']=['9001',0]
            with self.assertRaises(ValueError):flat.freeze(s)

    def test_graph_every_segment_independent_and_audio_frozen(self):
        cfg=config();plan={'id':'a'*32,'config':cfg}
        for row in flat.clock(400001,[141,282]):
            result=graph.build(plan,row,{'audio':'condition.wav','references':['reference.png'],'attempt':'test'})
            self.assertEqual(result['268']['inputs']['first_frame'],result['268']['inputs']['last_frame'])
            self.assertEqual(result['268']['class_type'],'MiniMaxH3ImageToVideo')
            self.assertEqual(result['246']['inputs']['video_latent'],['9101',0])
            self.assertEqual(result['249']['inputs']['value'],0)
            self.assertEqual(result['270']['inputs']['noise_seed'],1013)
            self.assertEqual(result['26000']['inputs']['audio'],['9000',0])
            self.assertFalse(any(n['class_type'] in ('LoadLatent','SaveLatent','ReplaceVideoLatentFrames','MiniMaxH3ReferenceToVideo') for n in result.values()))

    def test_prompt_evidence_and_retiming(self):
        cfg=config();row=flat.clock(200000,[])[0]
        text=prompts.effective({'config':cfg},row)
        self.assertIn(flat.DEFAULT_PROMPT,text)
        self.assertNotIn('jaw',text)
        self.assertEqual(prompts.graph_text({'1':{'class_type':'MiniMaxH3ImageToVideo','inputs':{'prompt':text}}}),text)
        self.assertIn('7.29-second',flat.prompt(text,175))
        self.assertEqual(flat.prompt(text,row['generation_frames']),text)

    def test_node_default_remains_legacy_and_local_requires_visible_canvas(self):
        options=node.TalkingAvatar.INPUT_TYPES()['optional']['generation_strategy']
        self.assertEqual(options[1]['default'],flat.LEGACY)
        import torch
        audio={'waveform':torch.zeros((1,1,24000)),'sample_rate':24000}
        with self.assertRaisesRegex(ValueError,'展开画布'):
            node.TalkingAvatar().prepare(audio,torch.zeros((1,32,32,3)),width=768,height=1376,generation_strategy=flat.MODE)

    def test_project_cut_prompt_redo_and_video_only_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'projects'
            p=project.prepare(root,np.zeros((400001,1),np.float32),24000,[Image.new('RGB',(32,32))],None,config(),'test')
            project.verify_assets(root,p)
            self.assertFalse(p['config'].get('continuity'))
            p=overlap.update_cuts(root,p['id'],[141/24,282/24])
            self.assertEqual(p['segments'][1]['audio_start_sample'],141000)
            self.assertFalse(p['approved'])
            for row in p['segments']:
                self.assertEqual(overlap.interval(p,row['index']),(row['start_sample']//1000,row['start_sample']//1000+row['edit_frames']))
            before=copy.deepcopy(p)
            with self.assertRaises(ValueError):overlap.update_cuts(root,p['id'],[6.])
            self.assertEqual(project.read_plan(root,p['id']),before)

    def test_public_node_prepare_resume_and_strategy_switch_guard(self):
        import torch
        with tempfile.TemporaryDirectory() as temp, patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:temp)), patch.object(node,'Client') as client:
            client.return_value.workflow.return_value=source()
            args=dict(audio={'waveform':torch.zeros(1,1,400001),'sample_rate':24000},reference_image=torch.zeros(1,32,32,3),
                      backend='RunningHub',workflow_id='123',width=768,height=1376,fixed_prompt=flat.DEFAULT_PROMPT,generation_strategy=flat.MODE)
            pid=node.TalkingAvatar().prepare(**args)['result'][0]
            plan=project.read_plan(node.root(),pid)
            self.assertEqual(plan['config']['talking_profile'],flat.PROFILE)
            self.assertEqual(plan['config']['seed'],1013)
            self.assertNotIn('continuity',plan['config'])
            self.assertEqual(plan['config']['reference_geometry'],flat.GEOMETRY)
            ref=project.project_path(node.root(),pid)/'refs'/plan['assets'][0]['name']
            with Image.open(ref) as picture:self.assertEqual(picture.size,(768,1376))
            self.assertEqual(node.TalkingAvatar().prepare(**args,project_id=pid)['result'][0],pid)
            old_name={**args,'generation_strategy':'原图首尾锚定（RH测试）'}
            self.assertEqual(node.TalkingAvatar().prepare(**old_name,project_id=pid)['result'][0],pid)
            self.assertEqual(project.read_plan(node.root(),pid),plan)
            self.assertEqual(client.return_value.workflow.call_count,1)
            client.return_value.create.assert_not_called()
            with self.assertRaisesRegex(ValueError,'新建项目'):
                node.TalkingAvatar().prepare(**{**args,'generation_strategy':flat.LEGACY},project_id=pid)

    def test_runner_resume_and_single_segment_redo_do_not_chain(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'projects'
            plan=project.prepare(root,np.zeros((400001,1),np.float32),24000,[Image.new('RGB',(32,32))],None,config(),'test')
            plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(root,plan)
            directory=project.project_path(root,plan['id']);client=FakeClient()
            def merge(*_):
                path=directory/'final.mp4';path.write_bytes(b'assembled');return str(path)
            def run():
                runner.run_project(root,plan['id'],client,validate=lambda *_:{},assemble=merge,sleep=lambda _:None)
                return project.read_plan(root,plan['id'])
            old=run();count=len(client.created)
            self.assertEqual(count,len(plan['segments']))
            run();self.assertEqual(len(client.created),count)
            project.request_regeneration(root,plan['id'],1)
            new=run();self.assertEqual(len(client.created),count+1)
            self.assertEqual(old['segments'][0]['job'],new['segments'][0]['job'])
            for row in new['segments']:
                self.assertEqual(row['job']['talking_profile'],flat.PROFILE)
                project.verify_saved_job(directory,row['job'])

    def test_cpu_assembly_exact_frame_clock_and_complete_source_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'projects';samples=294013
            wave=np.sin(np.arange(samples,dtype=np.float32)*.01)[:,None]*.1
            plan=project.prepare(root,wave,24000,[Image.new('RGB',(32,32))],None,config(),'test')
            directory=project.project_path(root,plan['id'])
            for row in plan['segments']:
                path=directory/f'raw{row["index"]}.mp4'
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=gray:s=48x86:r=24','-frames:v',str(row['generation_frames']),'-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
                row['job']={'status':'completed','video':str(path),'video_sha256':project.digest(path),'talking_profile':flat.PROFILE}
            project.write_plan(root,plan)
            final=overlap.assemble(root,plan['id'])
            self.assertEqual(int(runner.controller.probe_video(final)['nb_read_frames']),295)
            times=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time','-of','json',final]))
            pts=np.array([float(f['best_effort_timestamp_time']) for f in times['frames']])
            self.assertTrue(np.allclose(pts,np.arange(295)/24,atol=.000002), 'Final video must retain an exact24fps clock across joins')
            pcm=subprocess.check_output(['ffmpeg','-v','error','-i',final,'-vn','-ac','1','-ar','24000','-f','f32le','pipe:1'])
            self.assertGreaterEqual(len(pcm)//4,samples)
            self.assertGreater(np.corrcoef(np.frombuffer(pcm,np.float32)[:samples],wave[:,0])[0,1],.98)


if __name__=='__main__':unittest.main()
