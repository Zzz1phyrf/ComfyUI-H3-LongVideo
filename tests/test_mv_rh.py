"""Cloud MV control regressions; real-generation evidence lives outside tests."""
import asyncio
import copy
import importlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

import test_plugin as support

core, controller, nodes = support.core, support.controller, support.nodes
remote = importlib.import_module('h3lv_test.mv_rh')
transport = importlib.import_module('h3lv_test.talking_rh.remote')


def graph():
    def n(kind, **inputs):
        return {'class_type':kind,'inputs':inputs}
    return {'136':n('MiniMaxH3ReferenceToVideo', vae=['119',0],audio_vae=['120',0], **{'ref_audios.ref_audio_0':['9001',0]}),
        '119':n('VAELoader',vae_name='video'), '120':n('VAELoader',vae_name='audio'),
        '261':n('SelfLiftAvatarH3Sampler',latent_image=['246',0],positive=['136',0],negative=['264',0],vae=['119',0],seed=123),
        '264':n('ConditioningZeroOut',conditioning=['136',0]),
        '245':n('LTXVSeparateAVLatent',av_latent=['136',1]),
        '246':n('LTXVConcatAVLatent',audio_latent=['250',0],video_latent=['245',0]),
        '250':n('SetLatentNoiseMask',samples=['248',0],mask=['249',0]),
        '249':n('SolidMask',value=0), '248':n('LTXVAudioVAEEncode',audio=['9001',0],audio_vae=['120',0]),
        '9000':n('LoadAudio',audio='old_mix.wav'), '9001':n('LoadAudio',audio='old_vocals.wav'),
        '122':n('VAEDecode',samples=['251',0],vae=['119',0]),'251':n('LTXVSeparateAVLatent',av_latent=['261',0]),
        '272':n('CreateVideo',audio=['9000',0],images=['122',0],fps=24),
        '273':n('SaveVideo',video=['272',0],filename_prefix='old')}


def payload():
    return {'prompt':{'1':{'class_type':'H3LVUnified','inputs':{'backend':'RunningHub',
        'rh_workflow_id':'123','rh_region':'海外版','rh_instance_type':'ultra'}},
        '2':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{'prompt':['1',11]}}},
        'loader_id':'1','video_id':''}


class BindingTests(unittest.TestCase):
    def test_configuration_check_only_reads_template(self):
        with patch.object(remote,'Client') as factory:
            client = factory.return_value;client.workflow.return_value = graph()
            result = remote.check_configuration(payload())
            self.assertEqual(result,{'checked':True,'workflow_id':'123','seed':123,'prompt_source':'segment_prompt'})
            client.workflow.assert_called_once();client.create.assert_not_called();client.upload.assert_not_called()
            bad = graph();bad['261']['inputs']['vae'] = ['120',0];client.workflow.return_value = bad
            with self.assertRaisesRegex(ValueError,'VAE'):remote.check_configuration(payload())
            client.create.assert_not_called()

    def test_cloud_example_links_satisfy_runtime_binding(self):
        workflow = json.loads(next((support.ROOT/'examples').glob('08*')).read_text(encoding='utf-8'))
        links = {link[0]:link for link in workflow['links']}
        api = {}
        for item in workflow['nodes']:
            values = {i['name']:[str(links[i['link']][1]),links[i['link']][2]]
                      for i in item.get('inputs',[]) if i.get('link') is not None}
            if item['type'] == 'SelfLiftAvatarH3Sampler':values['seed'] = item['widgets_values'][0]
            if item['type'] == 'SolidMask':values['value'] = item['widgets_values'][0]
            api[str(item['id'])] = {'class_type':item['type'],'inputs':values}
        self.assertEqual(remote.binding(api)['sampler'],'261')

    def test_rejects_disconnected_ref2va_or_wrong_vae_before_upload(self):
        changes = [('261','positive',['264',0]), ('245','av_latent',['136',0]),
                   ('261','vae',['120',0]), ('122','vae',['120',0]),
                   ('248','audio_vae',['119',0]), ('264','conditioning',['missing',0])]
        for key,field,value in changes:
            g = graph();g[key]['inputs'][field] = value
            with self.subTest(node=key,field=field), self.assertRaises(ValueError):
                remote.binding(g)

    def test_shipped_local_and_cloud_sampler_use_video_vae(self):
        for prefix in ('07','08'):
            workflow = json.loads(next((support.ROOT/'examples').glob(prefix+'*')).read_text(encoding='utf-8'))
            by_id = {n['id']:n for n in workflow['nodes']}
            links = {link[0]:link for link in workflow['links']}
            def source(key,field):
                socket = next(i for i in by_id[key]['inputs'] if i['name']==field)
                return links[socket['link']][1:3]
            with self.subTest(example=prefix):
                self.assertEqual(source(261,'vae'),source(136,'vae'))
                self.assertEqual(source(122,'vae'),source(136,'vae'))
                self.assertEqual(source(248,'audio_vae'),source(136,'audio_vae'))

    def test_contract_preserves_original_vocals_and_zero_mask(self):
        value = remote.binding(graph())
        self.assertEqual((value['original'],value['vocals'],value['seed']),('9000','9001',123))
        for change in ('mask','same_audio','k_sampler','wrong_decode'):
            g = graph()
            if change == 'mask':g['249']['inputs']['value'] = 1
            if change == 'same_audio':g['272']['inputs']['audio'] = ['9001',0]
            if change == 'k_sampler':g['extra'] = {'class_type':'KSampler','inputs':{}}
            if change == 'wrong_decode':g['251']['inputs']['av_latent'] = ['136',0]
            with self.subTest(change=change), self.assertRaises(ValueError):remote.binding(g)

    def test_capture_follows_actual_prompt_wiring(self):
        value = payload()
        self.assertEqual(remote.capture(value)['prompt_source'],{'kind':'segment_prompt'})
        value['prompt']['2']['inputs']['prompt'] = ['3',0]
        value['prompt']['3'] = {'class_type':'PreviewAny','inputs':{'source':['4',0]}}
        value['prompt']['4'] = {'class_type':'H3LVPromptExpand','inputs':{
            'material':['1',4],'mode':'text','model':'test','rule':'rule','revision':3}}
        self.assertEqual(remote.capture(value)['prompt_source']['settings']['revision'],3)
        value['prompt']['4']['inputs']['material'] = ['other',4]
        with self.assertRaises(ValueError):remote.capture(value)

    def test_fingerprint_ignores_segment_inputs_but_tracks_seed(self):
        a = remote.capture(payload());a['remote_graph'] = graph()
        b = copy.deepcopy(a);b['remote_graph']['9000']['inputs']['audio'] = 'new.wav'
        self.assertEqual(remote.graph_fingerprint(a),remote.graph_fingerprint(b))
        b['remote_graph']['261']['inputs']['seed'] = 456
        self.assertNotEqual(remote.graph_fingerprint(a),remote.graph_fingerprint(b))

    def test_unresolved_paid_task_cannot_be_invalidated(self):
        row = {'job':{'backend':'runninghub','status':'creating_unknown','task_id':''}}
        with self.assertRaisesRegex(ValueError,'未解决'):core.request_regeneration(row,'edit')
        self.assertEqual(row['job']['status'],'creating_unknown')

    def test_existing_talking_transport_keeps_default_seed_and_mv_can_select_261(self):
        cfg = remote.config(payload()['prompt']['1']['inputs'])
        cfg.update(seed=123,seed_node='261')
        with patch.dict('os.environ',{'RUNNINGHUB_API_KEY':'fake'}):client = transport.Client(cfg)
        with patch.object(client,'_post',return_value={'code':0,'data':{'taskId':'789'}}) as post:
            self.assertEqual(client.create(graph()),'789')
        self.assertEqual(post.call_args.args[1]['nodeInfoList'][0]['nodeId'],'261')
        self.assertEqual(client.config['seed'],123)

    def test_node_old_call_and_outputs_remain_compatible(self):
        fields = nodes.Unified.INPUT_TYPES()
        self.assertEqual(fields['optional']['backend'][0],['本地','RunningHub'])
        self.assertEqual(nodes.Unified.RETURN_NAMES,nodes.LoadSegment.RETURN_NAMES)

    def test_initial_analysis_returns_all_declared_output_slots(self):
        with patch.object(nodes.Analyze,'analyze',return_value={'ui':{'h3lv_project':['new']}}):
            result = nodes.Unified().process({'sample_rate':24},'singing',15,11,'','')
        self.assertEqual(len(result['result']),len(nodes.Unified.RETURN_TYPES))
        self.assertEqual(result['result'][-2:],('',24.0))

    def test_rh_only_widgets_do_not_invalidate_legacy_local_takes(self):
        a = {'loader_id':'1','video_id':'7','prompt':{
            '1':{'class_type':'H3LVUnified','inputs':{'project_id':'a','segment_index':0}},
            '7':{'class_type':'VHS_VideoCombine','inputs':{'frame_rate':24}}}}
        b = copy.deepcopy(a)
        b['prompt']['1']['inputs'].update(backend='本地',rh_workflow_id='123',rh_region='海外版',rh_instance_type='ultra')
        self.assertEqual(controller.generation_graph_fingerprint(a),controller.generation_graph_fingerprint(b))

    def test_dual_backend_canvas_keeps_explicit_sampling_decode_and_converted_widgets(self):
        workflow = json.loads((support.ROOT/'examples/07_H3MV_本地与RH.json').read_text(encoding='utf-8'))
        nodes = {n['id']:n for n in workflow['nodes']}
        self.assertEqual(nodes[266]['widgets_values'][0],'singing')
        self.assertEqual(nodes[266]['widgets_values'][9],'本地')
        self.assertEqual(nodes[261]['type'],'SelfLiftAvatarH3Sampler')
        self.assertEqual(nodes[261]['widgets_values'][1],'fixed')
        self.assertEqual(nodes[249]['widgets_values'][0],0)
        self.assertEqual(nodes[122]['type'],'VAEDecode')
        self.assertEqual(nodes[273]['type'],'SaveVideo')
        for id,names in [(136,('prompt','length')),(273,('filename_prefix',))]:
            for name in names:
                value = next(i for i in nodes[id]['inputs'] if i['name']==name)
                self.assertIsNotNone(value['link'])
                self.assertEqual(value['widget']['name'],name)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.root = Path(self.temp.name)
        self.plan = support.sample_plan()
        self.plan['approved'] = True;self.plan['approved_fingerprint'] = core.fingerprint(self.plan)
        core.write_plan(self.root,self.plan)
        self.directory = core.project_path(self.root,self.plan['id'])
        self.snapshot = remote.capture(payload());self.snapshot.update(remote_graph=graph(),binding=remote.binding(graph()))
        self.snapshot['generation_graph_fingerprint'] = remote.graph_fingerprint(self.snapshot)
        remote.save(self.directory/'state/queue_snapshot.json',self.snapshot)
        self.server = types.SimpleNamespace(send_sync=MagicMock(),prompt_queue=MagicMock())
        self.system = patch.dict('sys.modules',{'execution':types.SimpleNamespace(),
            'folder_paths':types.SimpleNamespace(get_output_directory=lambda:str(self.root))})
        self.system.start()

    def tearDown(self):
        self.system.stop();controller.TASKS.clear();self.temp.cleanup()

    async def test_changed_cloud_graph_during_single_redo_marks_other_takes_stale(self):
        p = core.read_plan(self.root,self.plan['id']);p['run_only_segment'] = 1
        p['final_video'] = 'accepted_final.mp4'
        for i,row in enumerate(p['segments']):
            row['job'] = {'status':'completed','backend':'runninghub','video':f'old{i}.mp4'}
        core.write_plan(self.root,p)
        changed = graph();changed['261']['inputs']['seed'] += 1
        client = MagicMock();client.workflow.return_value = changed
        await remote.prepare(self.root,self.plan['id'],self.snapshot,client)
        result = core.read_plan(self.root,self.plan['id'])
        self.assertTrue(all(row.get('needs_regeneration') for row in result['segments']))
        self.assertTrue(result['final_stale'])
        self.assertEqual(result['run_only_segment'],1)
        self.assertEqual([r['job']['video'] for r in result['segments']],['old0.mp4','old1.mp4','old2.mp4'])

    async def test_single_redo_cannot_skip_another_unresolved_cloud_task(self):
        p = core.read_plan(self.root,self.plan['id'])
        p['segments'][0]['job'] = {'backend':'runninghub','status':'submitted','task_id':'555'}
        core.write_plan(self.root,p)
        request = payload();request['only_segment_index'] = 1
        with patch.object(controller.asyncio,'create_task',side_effect=lambda co:(co.close(),MagicMock())[1]) as launch:
            with self.assertRaisesRegex(ValueError,'未解决'):
                remote.start(self.root,self.plan['id'],request,self.server)
        launch.assert_not_called()

    async def test_generation_cannot_start_during_manual_assembly(self):
        p = core.read_plan(self.root,self.plan['id']);p['run_status']='merging';core.write_plan(self.root,p)
        with patch.object(controller.asyncio,'create_task') as launch:
            with self.assertRaisesRegex(ValueError,'合并'):remote.start(self.root,self.plan['id'],payload(),self.server)
        launch.assert_not_called()

    async def test_assembly_rejects_stale_completed_takes_before_encoding(self):
        p = core.read_plan(self.root,self.plan['id'])
        for row in p['segments']:row['job']={'status':'completed','video':'old.mp4'}
        p['segments'][1]['needs_regeneration']=True;core.write_plan(self.root,p)
        with patch.object(controller.shutil,'which',return_value='ffmpeg'),patch.object(controller,'command') as command:
            with self.assertRaisesRegex(ValueError,'需要生成'):controller.assemble(self.root,self.plan['id'])
        command.assert_not_called()

    async def test_resume_keeps_single_redo_scope(self):
        p = core.read_plan(self.root,self.plan['id']);p['run_only_segment'] = 1
        p['segments'][1]['job'] = {'backend':'runninghub','status':'submitted','task_id':'555'}
        core.write_plan(self.root,p)
        # Native Run after a restart carries no only_segment_index.
        with patch.object(controller.asyncio,'create_task',side_effect=lambda co:(co.close(),MagicMock())[1]):
            remote.start(self.root,self.plan['id'],payload(),self.server)
        self.assertEqual(core.read_plan(self.root,self.plan['id']).get('run_only_segment'),1)

    async def test_download_failure_resumes_same_task_and_retains_redo_scope(self):
        p = core.read_plan(self.root,self.plan['id']);p['run_only_segment'] = 1
        core.write_plan(self.root,p)
        client = MagicMock();client.workflow.return_value = graph();client.create.return_value = '555'
        client.query.return_value = {'status':'SUCCESS','results':[{'nodeId':'273','outputType':'mp4','url':'https://fixture.invalid/video'}]}
        client.download.side_effect = transport.RemoteError('download interrupted')
        with patch.object(remote,'Client',return_value=client),patch.object(remote,'build_segment',return_value=(graph(),'prompt')):
            await controller.execute_project(self.root,self.plan['id'],self.server)
            p = core.read_plan(self.root,self.plan['id'])
            self.assertEqual(p['run_only_segment'],1)
            self.assertEqual(p['segments'][1]['job']['task_id'],'555')
            client.download.side_effect = lambda url,path:Path(path).write_bytes(b'fixture')
            client.workflow.side_effect = AssertionError('Pending task must use frozen graph')
            def preview(command):Path(command[-1]).write_bytes(b'preview')
            with patch.object(remote,'validate_output',return_value={'graph_verified':True}),patch.object(controller,'command',side_effect=preview):
                remote.start(self.root,self.plan['id'],payload(),self.server)
                await controller.TASKS[self.plan['id']]
        p = core.read_plan(self.root,self.plan['id'])
        self.assertEqual(p['run_status'],'paused')
        self.assertEqual(p['segments'][1]['job']['status'],'completed')
        self.assertTrue(Path(p['segments'][1]['job']['preview_video']).is_file())
        self.assertNotIn('job',p['segments'][0]);self.assertNotIn('job',p['segments'][2])
        client.create.assert_called_once();self.assertEqual(client.query.call_count,2)

    async def test_full_offline_flow_builds_real_slices_previews_redo_and_merge(self):
        import numpy as np
        import soundfile as sf
        from PIL import Image
        p = core.read_plan(self.root,self.plan['id'])
        refs = core.reference_directory(self.directory);refs.mkdir(exist_ok=True,parents=True)
        for name in ('one.png','two.png'):Image.new('RGB',(8,8)).save(refs/name)
        p.update(materials_version=1,default_refs=['one.png','two.png'],default_material_note='')
        for i,row in enumerate(p['segments']):
            row.update(final_prompt=f'Approved segment {i}',reference_source='default',visual_type='performance')
        p['segments'][1].update(reference_source='custom',refs=['two.png'],visual_type='environment')
        p['approved_fingerprint'] = core.fingerprint(p);core.write_plan(self.root,p)
        mix = np.linspace(-.8,.8,p['samples'],dtype='float32')[:,None]
        for name,values in [('source.wav',mix),('vocals.wav',mix*.5)]:
            path = core.audio_file(self.directory,name);path.parent.mkdir(exist_ok=True,parents=True)
            sf.write(path,values,p['sample_rate'],subtype='FLOAT')
        created = [];uploads = {};client = MagicMock();client.config = {};client.workflow.return_value = graph()
        def upload(path):
            path = Path(path);name = path.parent.name+'/'+path.name
            uploads[name] = sf.read(path,dtype='float32')[0] if path.suffix=='.wav' else path.read_bytes()
            return name
        def create(value):created.append(copy.deepcopy(value));return str(100+len(created))
        client.upload.side_effect = upload;client.create.side_effect = create
        client.query.return_value = {'status':'SUCCESS','results':[{'nodeId':'273','outputType':'mp4','url':'https://fixture.invalid/video'}]}
        client.download.side_effect = lambda url,path:Path(path).write_bytes(b'raw fixture')
        def probe(path):
            submitted = json.loads(Path(path).with_name('submitted_graph.json').read_text(encoding='utf-8'))
            return {'width':64,'height':64,'r_frame_rate':'24/1','nb_read_frames':submitted['136']['inputs']['length']}
        def command(args):
            if args[0]=='ffprobe':
                submitted = Path(args[-1]).with_name('submitted_graph.json').read_text(encoding='utf-8')
                return json.dumps({'format':{'tags':{'prompt':submitted}}})
            Path(args[-1]).write_bytes(b'preview fixture');return ''
        finals = []
        def merge(root,pid):
            current = core.read_plan(root,pid)
            self.assertTrue(all(r['job']['status']=='completed' and not r.get('needs_regeneration') for r in current['segments']))
            path = self.directory/f'final{len(finals)}.mp4';path.write_bytes(b'final fixture');finals.append(str(path));return str(path)
        with patch.object(nodes,'data_root',return_value=self.root),patch.object(remote,'Client',return_value=client),\
                patch.object(controller,'probe_video',side_effect=probe),patch.object(controller,'command',side_effect=command),\
                patch.object(controller,'assemble',side_effect=merge):
            remote.start(self.root,self.plan['id'],payload(),self.server);await controller.TASKS[self.plan['id']]
            complete = core.read_plan(self.root,self.plan['id'])
            self.assertEqual(complete['run_status'],'completed',complete.get('error'))
            self.assertEqual(len(created),3)
            for i,value in enumerate(created):
                row = p['segments'][i];ref = value['136']['inputs']
                self.assertEqual(ref['prompt'],f'Approved segment {i}')
                self.assertEqual(ref['length'],row['generation_frames']);remote.binding(value,vocal_reference=i!=1)
                self.assertEqual(len([n for n in value.values() if n['class_type']=='LoadImage']),1 if i==1 else 2)
                original = uploads[value['9000']['inputs']['audio']];vocals = uploads[value['9001']['inputs']['audio']]
                count = row['end_sample']-row['start_sample']
                np.testing.assert_allclose(original[:count],mix[row['start_sample']:row['end_sample'],0],atol=1/32768)
                self.assertEqual(len(original),nodes.math_ceil_samples(row['generation_frames'],p['sample_rate']))
                self.assertTrue(np.all(original[count:]==0))
                if i==1:self.assertTrue(np.all(vocals==0))
                else:np.testing.assert_allclose(vocals[:count],mix[row['start_sample']:row['end_sample'],0]*.5,atol=1/32768)
                self.assertTrue(Path(complete['segments'][i]['job']['preview_video']).is_file())
            old = copy.deepcopy(complete);core.request_regeneration(complete['segments'][1],'redo');complete['final_stale']=True
            core.write_plan(self.root,complete)
            request = payload();request['only_segment_index']=1
            remote.start(self.root,self.plan['id'],request,self.server);await controller.TASKS[self.plan['id']]
            redone = core.read_plan(self.root,self.plan['id'])
            self.assertEqual(redone['run_status'],'paused');self.assertEqual(len(created),4)
            for i in (0,2):self.assertEqual(redone['segments'][i]['job'],old['segments'][i]['job'])
            self.assertTrue(Path(old['segments'][1]['job']['video']).is_file())
            self.assertEqual(redone['final_video'],old['final_video'])
            remote.start(self.root,self.plan['id'],payload(),self.server);await controller.TASKS[self.plan['id']]
            final = core.read_plan(self.root,self.plan['id'])
            self.assertEqual(len(created),4);self.assertEqual(len(finals),2)
            self.assertIn(old['final_video'],final['final_versions']);self.assertFalse(final['final_stale'])
        self.server.prompt_queue.put.assert_not_called()

    async def test_controller_uses_cloud_segments_and_real_shared_assembly(self):
        calls = []
        async def segment(root,pid,index,snapshot,client):
            calls.append(index);p = core.read_plan(root,pid)
            video = self.directory/f'{index}.mp4';video.write_bytes(b'video')
            p['segments'][index]['job'] = {'backend':'runninghub','status':'submitted','task_id':str(100+index)}
            core.write_plan(root,p)
            return str(video),'actual prompt'
        with patch.object(remote,'Client'),patch.object(remote,'prepare'),patch.object(remote,'segment',side_effect=segment), \
                patch.object(controller,'assemble',return_value=str(self.directory/'final.mp4')) as merge:
            await controller.execute_project(self.root,self.plan['id'],self.server)
        p = core.read_plan(self.root,self.plan['id'])
        self.assertEqual(calls,[0,1,2]);self.assertEqual(p['run_status'],'completed')
        self.assertTrue(all(row['job']['final_prompt']=='actual prompt' for row in p['segments']))
        merge.assert_called_once();self.server.prompt_queue.put.assert_not_called()

    async def test_unknown_creation_is_saved_and_resume_never_recreates(self):
        client = MagicMock();client.create.side_effect = transport.RemoteError('unknown')
        def build(*args):return graph(),'prompt'
        with patch.object(remote,'build_segment',side_effect=build):
            with self.assertRaises(transport.RemoteError):
                await remote.segment(self.root,self.plan['id'],0,self.snapshot,client)
        self.assertEqual(core.read_plan(self.root,self.plan['id'])['segments'][0]['job']['status'],'creating_unknown')
        with self.assertRaisesRegex(ValueError,'创建结果未知'):
            await remote.segment(self.root,self.plan['id'],0,self.snapshot,client)
        self.assertEqual(client.create.call_count,1)

    async def test_resume_queries_known_task_without_create_or_local_validation(self):
        folder = self.directory/'takes';folder.mkdir()
        remote.save(folder/'submitted_graph.json',graph())
        p = core.read_plan(self.root,self.plan['id'])
        p['segments'][0]['job'] = {'backend':'runninghub','status':'submitted','task_id':'555',
            'directory':str(folder),'final_prompt':'cached'}
        core.write_plan(self.root,p)
        client = MagicMock();client.query.return_value = {'status':'FAILED'}
        with self.assertRaisesRegex(ValueError,'RH任务失败'):
            await remote.segment(self.root,self.plan['id'],0,self.snapshot,client)
        client.query.assert_called_once_with('555');client.create.assert_not_called()
        self.assertEqual(core.read_plan(self.root,self.plan['id'])['segments'][0]['job']['status'],'failed')

    async def test_definite_failed_replacement_preserves_old_accepted_take(self):
        p = core.read_plan(self.root,self.plan['id']);p['run_only_segment'] = 0
        p['segments'][0].update(needs_regeneration=True,job={'status':'completed','video':str(self.directory/'old.mp4')})
        core.write_plan(self.root,p)
        async def fail(root,pid,index,*args):
            p = core.read_plan(root,pid);p['segments'][index]['job'] = {'backend':'runninghub','status':'failed','task_id':'666'}
            core.write_plan(root,p);raise ValueError('failed')
        with patch.object(remote,'Client'),patch.object(remote,'prepare'),patch.object(remote,'segment',side_effect=fail):
            await controller.execute_project(self.root,self.plan['id'],self.server)
        row = core.read_plan(self.root,self.plan['id'])['segments'][0]
        self.assertEqual(row['job']['video'],str(self.directory/'old.mp4'))
        self.assertTrue(row['needs_regeneration']);self.assertEqual(row['failed_attempts'][0]['task_id'],'666')

    async def test_pause_before_next_segment_does_not_submit(self):
        p = core.read_plan(self.root,self.plan['id']);p['pause_requested'] = True;core.write_plan(self.root,p)
        with patch.object(remote,'Client'),patch.object(remote,'prepare'),patch.object(remote,'segment') as generate:
            await controller.execute_project(self.root,self.plan['id'],self.server)
        generate.assert_not_called()
        self.assertEqual(core.read_plan(self.root,self.plan['id'])['run_status'],'paused')

    async def test_local_resume_recomputes_legacy_cached_graph_fingerprint(self):
        p = core.read_plan(self.root,self.plan['id'])
        for i,row in enumerate(p['segments']):
            row['job'] = {'status':'completed','video':str(self.directory/f'old{i}.mp4'),'prompt_id':str(i)}
        core.write_plan(self.root,p)
        snapshot = {'loader_id':'1','video_id':'7','prompt':{
            '1':{'class_type':'H3LVUnified','inputs':{'backend':'本地','rh_workflow_id':''}},
            '7':{'class_type':'VHS_VideoCombine','inputs':{'frame_rate':24}}},
            'generation_graph_fingerprint':'previous fingerprint algorithm'}
        remote.save(self.directory/'state/queue_snapshot.json',snapshot)
        with patch.object(controller.asyncio,'create_task',side_effect=lambda coroutine:(coroutine.close(),MagicMock())[1]):
            controller.start(self.root,self.plan['id'],snapshot,self.server)
        result = core.read_plan(self.root,self.plan['id'])
        self.assertTrue(all(not row.get('needs_regeneration') for row in result['segments']))
        self.assertEqual([row['job']['prompt_id'] for row in result['segments']],['0','1','2'])


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_route_reads_only_and_returns_binding_errors(self):
        from aiohttp import web
        table = web.RouteTableDef()
        server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=table)))
        with patch.dict('sys.modules',{'server':server}):support.routes.register_routes()
        handler = next(r.handler for r in table if r.method=='POST' and r.path=='/h3lv/rh-check')
        request = types.SimpleNamespace(json=AsyncMock(return_value=payload()),match_info={})
        with patch.object(remote,'Client') as factory:
            client = factory.return_value;client.workflow.return_value = graph()
            response = await handler(request)
            self.assertEqual(response.status,200);self.assertTrue(json.loads(response.text)['checked'])
            client.workflow.return_value['261']['inputs']['vae'] = ['120',0]
            response = await handler(request)
            self.assertEqual(response.status,400);self.assertIn('VAE',json.loads(response.text)['error'])
            client.create.assert_not_called();client.upload.assert_not_called();client.download.assert_not_called()


if __name__ == '__main__':unittest.main()
