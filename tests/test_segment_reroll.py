"""Rerolls change submitted noise; retry/resume retains the persisted take seed."""
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

import numpy as np
from PIL import Image

import test_mv_panel_parity as mv_panel
from test_plugin import core, controller
import test_talking_regeneration as regen
from test_talking_rh import project, graph, runner, remote
from test_talking_flat_rh import config as flat_config
from test_talking_flat_local import expanded, flat


class MVRerollTests(unittest.IsolatedAsyncioTestCase):
    setUp = mv_panel.PanelParityTests.setUp
    invoke = mv_panel.PanelParityTests.invoke

    async def test_real_redo_route_and_local_queue_change_only_selected_seed(self):
        snapshot = {'loader_id':'1', 'video_id':'7', 'prompt': {
            '1':{'class_type':'H3LVUnified','inputs':{}},
            '261':{'class_type':'SelfLiftAvatarH3Sampler','inputs':{'seed':95207}},
            '7':{'class_type':'VHS_VideoCombine','inputs':{}}}}
        (self.directory/'state/queue_snapshot.json').write_text(json.dumps(snapshot))
        self.assertEqual((await self.invoke('retry', {'index':1})).status, 200)
        queued, histories = [], {}
        directory, pid = self.directory, self.plan['id']
        class Queue:
            def put(self, item):
                queued.append(copy.deepcopy(item[2]))
                file = directory/f'new-{len(queued)}.mp4'; file.write_bytes(b'new')
                histories[item[1]] = {'status':{'status_str':'success'}, 'outputs':{
                    '7':{'gifs':[{'type':'output','subfolder':pid,'filename':file.name}]}}}
            def get_history(self, prompt_id):return {prompt_id:histories[prompt_id]}
            def get_current_queue(self):return [], []
        async def validate(*_):return True, None, ['7'], {}
        server = types.SimpleNamespace(number=0, prompt_queue=Queue())
        with patch.dict(sys.modules, execution=types.SimpleNamespace(validate_prompt=validate),
                        folder_paths=types.SimpleNamespace(get_output_directory=lambda:str(self.root))), \
             patch.object(controller, 'assemble', return_value=str(directory/'new-final.mp4')):
            await controller.execute_project(self.root, pid, server)
            first = queued[-1]['261']['inputs']['seed']
            self.assertNotEqual(first, 95207)
            saved = core.read_plan(self.root, pid)
            self.assertEqual(saved['segments'][1]['job']['seeds']['261'], first)
            self.assertEqual(saved['segments'][0], self.plan['segments'][0])
            self.assertEqual(saved['segments'][2], self.plan['segments'][2])
            self.assertEqual((await self.invoke('retry', {'index':1})).status, 200)
            await controller.execute_project(self.root, pid, server)
        self.assertNotEqual(queued[-1]['261']['inputs']['seed'], first)
        self.assertEqual(len(queued), 2)
        self.assertEqual(snapshot['prompt']['261']['inputs']['seed'], 95207)

    async def test_failed_retry_does_not_request_another_seed(self):
        row = self.plan['segments'][1]
        row.update(seed_overrides={'261':3456})
        row['job'].update(status='failed', seeds={'261':3456})
        core.write_plan(self.root,self.plan)
        self.assertEqual((await self.invoke('retry',{'index':1})).status,200)
        saved=core.read_plan(self.root,self.plan['id'])['segments'][1]
        self.assertEqual(saved['seed_overrides'],{'261':3456})
        self.assertFalse(saved.get('reroll_pending'))

    async def test_explicit_reroll_of_existing_stale_take_requests_a_new_seed(self):
        row=self.plan['segments'][1]
        row.update(seed_overrides={'261':3456},needs_regeneration=True)
        core.write_plan(self.root,self.plan)
        self.assertEqual((await self.invoke('retry',{'index':1,'reroll':True})).status,200)
        self.assertTrue(core.read_plan(self.root,self.plan['id'])['segments'][1]['reroll_pending'])

    async def test_restore_old_take_restores_its_seed_without_generating(self):
        row=self.plan['segments'][1]
        previous=copy.deepcopy(row['job']);previous['seeds']={'261':42}
        row.update(seed_overrides={'261':3456},reroll_pending=True,takes=[previous])
        row['job'].update(seeds={'261':3456},video='new.mp4')
        core.write_plan(self.root,self.plan)
        self.assertEqual((await self.invoke('restore',{'index':1})).status,200)
        saved=core.read_plan(self.root,self.plan['id'])['segments'][1]
        self.assertEqual(saved['seed_overrides'],{'261':42})
        self.assertEqual(saved['job'],previous)
        self.assertFalse(saved.get('reroll_pending'))

    async def test_mv_rh_real_builder_and_download_resume_preserve_new_seed(self):
        mv=importlib.import_module('h3lv_test.mv_rh')
        nodes=importlib.import_module('h3lv_test.nodes')
        progress=importlib.import_module('h3lv_test.progress')
        await self.invoke('retry',{'index':1})
        p=core.read_plan(self.root,self.plan['id']);p['segments'][1].pop('job');core.write_plan(self.root,p)
        base={str(n):{'class_type':kind,'inputs':{}} for n,kind in
              [(10,'LoadAudio'),(11,'LoadAudio'),(20,'MiniMaxH3ReferenceToVideo'),(272,'CreateVideo'),(273,'SaveVideo')]}
        base['261']={'class_type':'SelfLiftAvatarH3Sampler','inputs':{'seed':95207}}
        snapshot={'remote_graph':base,'binding':{'original':'10','vocals':'11','reference':'20',
                  'creator':'272','output':'273','sampler':'261'},
                  'prompt_source':{'kind':'literal','text':'test'},'remote':{}}
        audio={'waveform':[types.SimpleNamespace(numpy=lambda:np.zeros((1,1000),np.float32))],'sample_rate':24000}
        loaded=[audio,audio,141,'prefix',{'visual_type':'performance','paths':[]}]+[None]*6+['']
        submitted=[]
        client=Mock(upload=Mock(return_value='uploaded.wav'))
        client.create.side_effect=lambda value:(submitted.append(copy.deepcopy(value)) or '12345')
        client.query.return_value={'status':'SUCCESS','results':[{'nodeId':'273','outputType':'mp4','url':'fixture'}]}
        client.download.side_effect=RuntimeError('download interrupted')
        with patch.object(nodes.LoadSegment,'load',return_value=loaded),patch.object(progress,'report'), \
             patch.object(mv,'validate_output',return_value={}), \
             patch.object(controller,'command',side_effect=lambda args:Path(args[-1]).write_bytes(b'preview')):
            with self.assertRaisesRegex(RuntimeError,'download interrupted'):
                await mv.segment(self.root,self.plan['id'],1,snapshot,client)
            saved=core.read_plan(self.root,self.plan['id']);row=saved['segments'][1]
            seed=submitted[0]['261']['inputs']['seed']
            self.assertNotEqual(seed,95207)
            self.assertEqual(row['seed_overrides'],{'261':seed})
            self.assertEqual(row['job']['seeds'],{'261':seed})
            intent=Path(row['job']['directory'])/'submitted_graph.json';before=intent.read_bytes()
            client.download.side_effect=lambda url,target:Path(target).write_bytes(b'video')
            await mv.segment(self.root,self.plan['id'],1,snapshot,client)
            self.assertEqual(client.create.call_count,1)
            self.assertEqual(intent.read_bytes(),before)
        self.assertEqual(base['261']['inputs']['seed'],95207)


class TalkingRerollTests(unittest.TestCase):
    def test_flat_local_and_rh_redo_submit_new_seed_and_resume_without_more_tasks(self):
        for backend in ('local','runninghub'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as temp:
                root=Path(temp)/'projects'; cfg=flat_config(); cfg['backend']=backend
                if backend=='local':
                    cfg.pop('rh_sampling')
                    cfg.update(execution_mode='canvas',canvas=flat.capture_canvas(expanded(),'5'))
                p=project.prepare(root,np.zeros((400001,1),np.float32),24000,
                    [Image.new('RGB',(32,32))],None,cfg,'test')
                p.update(approved=True,approved_fingerprint=project.fingerprint(p));project.write_plan(root,p)
                directory=project.project_path(root,p['id']);client=regen.Client()
                def assemble(*_):
                    file=directory/f'final-{len(client.created)}.mp4';file.write_bytes(b'final');return str(file)
                def run():runner.run_project(root,p['id'],client,validate=lambda *_:{},assemble=assemble,sleep=lambda _:None)
                run();before=project.read_plan(root,p['id']);count=len(client.created)
                old_seed=client.created[1]['270']['inputs'].get('noise_seed',client.created[1]['270']['inputs'].get('seed'))
                project.request_regeneration(root,p['id'],1);run()
                new=client.created[-1]['270']['inputs'];seed=new.get('noise_seed',new.get('seed'))
                self.assertIs(type(seed),int);self.assertNotEqual(seed,old_seed)
                current=project.read_plan(root,p['id'])
                self.assertEqual(current['segments'][1]['job']['seeds']['270'],seed)
                self.assertEqual(current['segments'][0],before['segments'][0])
                self.assertEqual(current['segments'][2:],before['segments'][2:])
                self.assertEqual(current['config']['seed'],cfg['seed'])
                self.assertEqual(len(client.created),count+1)
                run();self.assertEqual(len(client.created),count+1)
                project.request_regeneration(root,p['id'],1);run()
                newest=client.created[-1]['270']['inputs']
                self.assertNotEqual(newest.get('noise_seed',newest.get('seed')),seed)
                project.request_regeneration(root,p['id'],1);client.fail_download=True
                with self.assertRaises(remote.RemoteError):run()
                failed=project.read_plan(root,p['id'])['segments'][1]['job'];count=len(client.created)
                client.fail_download=False;run()
                self.assertEqual(len(client.created),count)
                self.assertEqual(project.read_plan(root,p['id'])['segments'][1]['job']['seeds'],failed['seeds'])


class TalkingRetryTests(unittest.IsolatedAsyncioTestCase):
    setUp=regen.RegenerationRouteTests.setUp
    approve=regen.RegenerationRouteTests.approve
    prepare_chain=regen.RegenerationRouteTests.prepare_chain
    assemble=regen.RegenerationRouteTests.assemble
    run_chain=regen.RegenerationRouteTests.run_chain
    post=regen.RegenerationRouteTests.post

    async def test_both_backends_failed_redo_retry_keeps_chosen_seed(self):
        for backend in ('local','RunningHub'):
            with self.subTest(backend=backend):
                self.prepare_chain(backend)
                await self.post('regenerate',{'index':1})
                self.client.provider_failure=True
                with self.assertRaises(ValueError):self.run_chain()
                failed=project.read_plan(self.root,self.pid)['segments'][1]['job']
                seed=self.client.created[-1]['270']['inputs']['seed']
                self.assertEqual(failed['seeds']['270'],seed)
                self.assertEqual((await self.post('retry',{'index':1}))[0],200)
                self.client.provider_failure=False;self.run_chain()
                self.assertEqual(self.client.created[-2]['270']['inputs']['seed'],seed)
                self.assertEqual(project.read_plan(self.root,self.pid)['config']['seed'],999)


class RHSeedOverrideTests(unittest.TestCase):
    def test_actual_rh_request_uses_graph_seed_for_both_sampler_types(self):
        for kind,field in [('SelfLiftAvatarH3Sampler','seed'),('SamplerCustom','noise_seed')]:
            with self.subTest(kind=kind),patch.object(remote,'api_key',return_value='fixture-key'):
                client=remote.Client({'base_url':'https://www.runninghub.ai','workflow_id':'123',
                                      'instance_type':'plus','seed':42,'seed_node':'270'})
                value={'270':{'class_type':kind,'inputs':{field:6789}}}
                with patch.object(client,'_post',return_value={'code':0,'data':{'taskId':'12345'}}) as submit:
                    client.create(value)
                payload=submit.call_args.args[1]
                self.assertEqual(payload['nodeInfoList'][0]['fieldValue'],6789)
                self.assertEqual(json.loads(payload['workflow'])['270']['inputs'][field],6789)

    def test_rng_collision_changes_seed_once_and_retry_reuses_it(self):
        seeds=importlib.import_module('h3lv_test.segment_seeds')
        value={'270':{'class_type':'SamplerCustom','inputs':{'noise_seed':42}}}
        row={'reroll_pending':True}
        with patch.object(seeds.secrets,'randbits',return_value=42) as random:
            chosen=seeds.apply_seed_overrides(value,row)
            self.assertEqual(chosen,{'270':43})
            self.assertFalse(row.get('reroll_pending'))
            second={'270':{'class_type':'SamplerCustom','inputs':{'noise_seed':42}}}
            self.assertEqual(seeds.apply_seed_overrides(second,row),chosen)
            random.assert_called_once()
        self.assertEqual(second['270']['inputs']['noise_seed'],43)
