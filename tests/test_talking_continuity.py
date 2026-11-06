"""Native continuity contracts, independent of any paid generation service."""
import copy
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from safetensors.numpy import save_file

from test_talking_overlap import config, project, graph, canvas, overlap

continuity = importlib.import_module('h3talk_test.talking_rh.continuity')


class ContinuityTests(unittest.TestCase):
    def test_all_audio_phases_and_full_15_second_edit_range(self):
        longest = 0
        for phase in range(17):
            for duration in (96, 132, 187, 360):
                cuts = [180+phase,180+phase+duration]
                samples = (cuts[-1]+100)*1000-137
                rows = continuity.clock(samples,cuts)
                self.assertEqual(rows[-1]['end_sample'],samples)
                self.assertEqual(sum(r['edit_frames'] for r in rows),(samples+999)//1000)
                for i,row in enumerate(rows):
                    longest=max(longest,row['generation_frames'])
                    self.assertEqual(row['window_start_frame']%17,0)
                    self.assertGreaterEqual(row['window_end_frame']-row['end_sample']/1000,17)
                    self.assertEqual((row['generation_frames']-5)%17,0)
                    if i:
                        previous=rows[i-1]
                        self.assertEqual(row['continuity_prefix_index'],5*((row['window_start_frame']-previous['window_start_frame'])//17))
                        self.assertLessEqual(row['continuity_prefix_index']+continuity.TOKENS,project.latent_frames(previous['generation_frames']))
                        self.assertTrue(40<=row['edit_start_frame']<=56)
                wave=np.arange(samples,dtype=np.float32)[:,None]
                for row in rows:
                    value=project.condition_audio(wave,row);start=row['audio_start_sample']
                    count=min(len(value),len(wave)-start)
                    np.testing.assert_array_equal(value[:count],wave[start:start+count])
        self.assertEqual(longest,447)

    def test_shared_local_and_remote_graph_preserve_models_seed_audio_and_save_real_latent(self):
        for local in (False,True):
            cfg=config(local);original=copy.deepcopy(cfg)
            cfg['continuity']=continuity.freeze(cfg)
            plan={'id':'a'*32,'config':cfg,'assets':[{}],'samples':510000,
                  'speech_segmentation':{'boundaries':[{'frame':187,'kind':'pause'},{'frame':342,'kind':'pause'}]}}
            rows=continuity.clock_for(plan,[187,342])
            for row in rows:
                value=graph.build(plan,row,{'audio':'current.wav','references':['ref.png'],'attempt':'a'*12,'previous_latent':'actual.latent'})
                ids=cfg['continuity']['ids']
                self.assertEqual(canvas.output_ids(cfg)['latent'],ids['save'])
                self.assertEqual(value[ids['save']]['class_type'],'SaveLatent')
                self.assertEqual(sum(n['class_type']=='SaveVideo' for n in value.values()),1)
                self.assertEqual(value['270']['inputs']['noise_seed'],['5',canvas.SLOT['seed']] if local else 9260)
                self.assertEqual(value['249']['inputs']['value'],0)
                self.assertEqual(value['246']['inputs']['audio_latent'],['250',0])
                self.assertEqual(value[ids['end']]['inputs']['frame_idx'],-1)
                self.assertEqual(value[ids['end']]['inputs']['image'],['5',canvas.SLOT['reference_image']] if local else ['9001',0])
                source=original['canvas']['prompt'] if local else original['rh_sampling']['graph']
                for kind,fields in [('UNETLoader',('unet_name',)),('LoraLoaderModelOnly',('lora_name','strength_model'))]:
                    settings=lambda g:sorted(tuple(n['inputs'][f] for f in fields) for n in g.values() if n['class_type']==kind)
                    self.assertEqual(settings(value),settings(source))
                if row['index']:
                    self.assertEqual(value[ids['cut']]['inputs']['index'],row['continuity_prefix_index'])
                    self.assertEqual(value[ids['mask']]['inputs']['existing_mask_mode'],'add')
                    self.assertEqual(value[ids['prefix']]['inputs']['existing_mask_mode'],'subtract')
                    self.assertEqual(value[ids['prefix']]['inputs']['video_latent'],[ids['mask'],0])
                else:self.assertIn(ids['start'],value)

    def test_ids_avoid_user_canvas_collisions(self):
        cfg=config(True)
        cfg['canvas']['prompt']['18000']={'class_type':'SomeUserNode','inputs':{}}
        self.assertNotIn('18000',continuity.freeze(cfg)['ids'].values())

    def test_long_guide_chain_reaches_sampler_without_rewiring_itself(self):
        for local in (False,True):
            cfg=config(local);cfg['continuity']=continuity.freeze(cfg)
            plan={'id':'a'*32,'config':cfg,'assets':[{}],'samples':1288192,
                  'speech_segmentation':{'boundaries':[{'frame':f,'kind':'pause'}
                    for f in (187,342,510,666,802,976,1136)]}}
            for row in continuity.clock_for(plan,[360,720,1080]):
                value=graph.build(plan,row,{'attempt':'a'*12,'audio':'a.wav',
                    'references':['ref.png'],'previous_latent':'previous.latent'})
                key=value['270']['inputs']['positive'][0];seen=set();frames=[]
                while value[key]['class_type']=='MiniMaxH3AddGuide':
                    self.assertNotIn(key,seen);seen.add(key)
                    guide=value[key]['inputs'];frames.append(guide['frame_idx'])
                    self.assertEqual(guide['latent'],['268',1])
                    self.assertEqual(guide['vae'],value['268']['inputs']['vae'])
                    if row['index'] and guide['frame_idx']==0:
                        decoder=value[guide['image'][0]]
                        self.assertEqual(decoder['class_type'],'VAEDecode')
                        self.assertEqual(decoder['inputs']['samples'],[cfg['continuity']['ids']['cut'],0])
                    else:self.assertEqual(guide['image'],value['268']['inputs']['ref_images.ref_image_0'])
                    key=guide['positive'][0]
                self.assertEqual(key,'268')
                expected=[0]+[a['frame'] for a in row['visual_anchors']]+[-1]
                self.assertEqual(frames[::-1],expected)
                self.assertEqual(value['249']['inputs']['value'],0)
                self.assertEqual(value['246']['inputs']['audio_latent'],['250',0])

    def test_long_guides_choose_actual_pauses_and_keep_user_15_second_cuts(self):
        cfg=config();cfg['continuity']=continuity.freeze(cfg)
        cfg['continuity']={'version':continuity.GUIDE_VERSION,
                           'ids':{key:value for key,value in cfg['continuity']['ids'].items() if key in continuity.GUIDE_ROLES},
                           'guide_interval_frames':204}
        plan={'config':cfg,'samples':1288192,'speech_segmentation':{'boundaries':[
            {'frame':f,'kind':'pause'} for f in (187,342,510,666,802,976,1136)]}}
        rows=continuity.clock_for(plan,[360,720,1080])
        self.assertEqual([r['generation_frames'] for r in rows],[379,447,430,277])
        self.assertEqual([a['frame'] for a in rows[0]['visual_anchors']],[187])
        self.assertEqual([a['frame'] for a in rows[1]['visual_anchors']],[204,360])
        self.assertTrue(all(a['kind']=='pause' for r in rows for a in r['visual_anchors']))
        self.assertEqual([r['edit_frames'] for r in rows[:3]],[360]*3)
        for phase in range(17):
            plan['samples']=(180+phase+360+100)*1000
            plan['speech_segmentation']['boundaries']=[]
            for row in continuity.clock_for(plan,[180+phase,180+phase+360]):
                self.assertLessEqual(len(row['visual_anchors']),2)
                left=continuity.CONTEXT_FRAMES if row['index'] else 0
                for a in row['visual_anchors']:
                    self.assertTrue(102<=a['frame']-left<=204)
                    self.assertEqual(a['kind'],'interval_limit');left=a['frame']
                self.assertLessEqual(row['generation_frames']-1-left,204)

    def test_frozen_v1_projects_keep_exact_clock_and_no_middle_guides(self):
        cfg=config();cfg['continuity']=continuity.freeze(cfg)
        cfg['continuity']['version']=continuity.LEGACY_VERSION
        cfg['continuity']['ids']={k:v for k,v in cfg['continuity']['ids'].items() if k in continuity.LEGACY_ROLES}
        plan={'id':'a'*32,'config':cfg,'samples':720000}
        rows=continuity.clock_for(plan,[360])
        self.assertEqual(rows,continuity.clock(720000,[360]))
        value=graph.build(plan,rows[0],{'attempt':'a'*12,'audio':'a.wav','references':['ref.png']})
        self.assertEqual(sum(n['class_type']=='MiniMaxH3AddGuide' for n in value.values()),2)

    def test_prefix_verification_accepts_float_roundtrip_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            a=Path(folder)/'a.latent';b=Path(folder)/'b.latent'
            original=np.random.default_rng(1).normal(size=(1,24,60,2,2)).astype('float32')
            expected=original[:,:,20:37].copy()
            save_file({'latent_tensor':original},a)
            expected[0,0,0,0,0]+=2e-7
            save_file({'latent_tensor':expected},b)
            proof=continuity.validate_prefix(a,b,20)
            self.assertTrue(proof['numerically_preserved']);self.assertFalse(proof['bit_exact'])
            for bad in (0.01,np.nan):
                expected[0,0,0,0,0]=bad
                save_file({'latent_tensor':expected},b)
                with self.assertRaises(ValueError):continuity.validate_prefix(a,b,20)

    def test_no_flag_keeps_legacy_windows_and_output_contract(self):
        cfg=config()
        self.assertFalse(continuity.enabled({'config':cfg}))
        self.assertEqual(canvas.output_ids(cfg),{'video':'273'})
        rows=overlap.manual_clock(510000,[187,342])
        self.assertEqual(rows[1]['window_start_frame'],170)

    def test_chain_resume_redo_restore_and_cut_dependencies(self):
        from PIL import Image
        from test_talking_rh import runner, FakeClient

        class Client(FakeClient):
            def query(self,task_id):
                g=self.created[int(task_id)-1001]
                return {'status':'SUCCESS','results':[
                    {'nodeId':k,'outputType':'latent' if n['class_type']=='SaveLatent' else 'mp4','url':task_id+'/'+k}
                    for k,n in g.items() if n['class_type'] in ('SaveLatent','SaveVideo')]}

            def download(self,url,target):
                if self.fail_download:raise RuntimeError('interrupted download')
                if target.exists():return
                g=self.created[int(url.split('/')[0])-1001]
                if target.suffix=='.latent':
                    count=project.latent_frames(g['268']['inputs']['length'])
                    save_file({'latent_tensor':np.zeros((1,24,count,2,2),np.float32)},target)
                else:target.write_bytes(url.encode())

        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'projects';cfg=config();cfg['continuity']=continuity.freeze(cfg)
            plan=project.prepare(root,np.zeros((510000,1),np.float32),24000,[Image.new('RGB',(32,64))],None,cfg,'input')
            project.update_cuts(root,plan['id'],[187/24,342/24]);plan=project.read_plan(root,plan['id'])
            plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(root,plan)
            client=Client();pid=plan['id'];directory=project.project_path(root,pid)
            def assemble(*_):
                target=directory/'final.mp4';target.write_bytes(b'final');return str(target)
            def run():
                with patch.object(overlap,'resolve',side_effect=lambda _,p:p):
                    runner.run_project(root,pid,client,validate=lambda *_:{},assemble=assemble,sleep=lambda _:None)
            run();before=project.read_plan(root,pid);run();self.assertEqual(len(client.created),3)
            plan=project.request_regeneration(root,pid,1)
            self.assertEqual(plan['regeneration']['stop'],3)
            self.assertEqual(plan['segments'][0],before['segments'][0])
            self.assertTrue(all('job' not in row for row in plan['segments'][1:]))
            restored=project.restore_regeneration(root,pid)
            self.assertEqual([r['job'] for r in restored['segments']],[r['job'] for r in before['segments']])
            project.request_regeneration(root,pid,1);client.fail_download=True
            with self.assertRaises(RuntimeError):run()
            paid=project.read_plan(root,pid)['segments'][1]['job']['task_id']
            client.fail_download=False;run()
            after=project.read_plan(root,pid)
            self.assertEqual(after['segments'][1]['job']['task_id'],paid)
            self.assertEqual(len(client.created),5)
            ids=cfg['continuity']['ids']
            self.assertEqual(client.created[-1][ids['load']]['inputs']['latent'],'uploaded_'+after['segments'][1]['job']['latent_sha256'])
            changed=project.update_cuts(root,pid,[187/24,350/24])
            self.assertEqual(changed['segments'][0]['job'],after['segments'][0]['job'])
            self.assertTrue(all('job' not in r for r in changed['segments'][1:]))


if __name__=='__main__':unittest.main()
