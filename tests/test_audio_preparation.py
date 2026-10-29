"""Shared audio preparation, cache integrity and model-free dry speech."""
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
import test_plugin as mv
from test_talking_public import node,public_canvas
from test_talking_overlap import project,canvas,graph,fixture,sampling
import torch
import types

audio=importlib.import_module('h3lv_test.audio_preparation')


class AudioPreparationTests(unittest.TestCase):
    def test_alignment_resamples_and_accepts_rounding_but_rejects_trimmed_audio(self):
        for samples in (44100,44101):
            value=audio.align_reference(np.ones((samples,1),np.float32),44100,48000,48000)
            self.assertEqual(value.shape,(48000,1));self.assertTrue(np.isfinite(value).all())
        with self.assertRaisesRegex(ValueError,'时长'):
            audio.align_reference(np.ones((43000,1),np.float32),44100,48000,48000)

    def test_direct_never_loads_separation_and_extraction_is_cached_once(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);(directory/'audio').mkdir()
            wave=np.linspace(-.1,.1,48000,dtype=np.float32)[:,None]
            sf.write(directory/'audio/source.wav',wave,48000,subtype='FLOAT')
            with patch.object(mv.nodes,'separate',side_effect=AssertionError('No model for dry speech')):
                direct=audio.cache_reference(directory,audio.DIRECT)
            np.testing.assert_array_equal(sf.read(directory/'audio/vocals.wav',dtype='float32',always_2d=True)[0],wave)
            self.assertEqual(direct['mode'],audio.DIRECT)
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp);(directory/'audio').mkdir()
            sf.write(directory/'audio/source.wav',wave,48000,subtype='FLOAT')
            with patch.object(mv.nodes,'separate',return_value=wave*.5) as separate:
                first=audio.cache_reference(directory,audio.EXTRACT)
                self.assertEqual(audio.cache_reference(directory,audio.EXTRACT),first)
            separate.assert_called_once()
            (directory/'audio/vocals.wav').write_bytes(b'corrupt')
            with patch.object(mv.nodes,'separate',side_effect=AssertionError('No silent regeneration')):
                with self.assertRaisesRegex(ValueError,'变化|损坏'):audio.cache_reference(directory,audio.EXTRACT)

    def test_mv_defaults_extract_and_talking_defaults_direct(self):
        mv_schema=mv.nodes.Unified.INPUT_TYPES()['optional']['audio_processing']
        talk_schema=node.TalkingAvatar.INPUT_TYPES()['required']['audio_processing']
        self.assertEqual(mv_schema[1]['default'],audio.EXTRACT)
        self.assertEqual(talk_schema[1]['default'],audio.DIRECT)

    def test_real_mv_analysis_uses_selected_mode_and_keeps_original_audio(self):
        original={'waveform':torch.linspace(-.1,.1,1000).reshape(1,1,-1),'sample_rate':100}
        wave=original['waveform'][0].numpy().T
        rules={'config':mv.director_rules.default_config(),'revision':'test'}
        for mode in (audio.DIRECT,audio.EXTRACT):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp, \
                 patch.object(mv.nodes,'data_root',return_value=Path(temp)), \
                 patch.object(mv.nodes,'rules_path',return_value=Path(temp)/'rules'), \
                 patch.object(mv.nodes.director_rules,'read_rules',return_value=rules), \
                 patch.object(mv.nodes,'run_asr',return_value={'segments':[],'words':[]}), \
                 patch.object(mv.nodes,'segmentation',return_value=([{'start_sample':0,'end_sample':1000,'energy_db':-30,'text':''}],{'sections':[],'rhythm':{}})), \
                 patch.object(mv.nodes,'separate',return_value=wave*.5) as separate:
                result=mv.nodes.Unified().process(original,'singing',15,11,'','',audio_processing=mode)
                plan=mv.core.read_plan(Path(temp),result['ui']['h3lv_project'][0])
                directory=mv.core.project_path(Path(temp),plan['id'])
                self.assertEqual(plan['audio_processing'],mode)
                self.assertEqual(separate.call_count,int(mode==audio.EXTRACT))
                saved=sf.read(directory/'audio/source.wav',dtype='float32',always_2d=True)[0]
                np.testing.assert_array_equal(saved,wave)

    def test_new_local_dry_project_uses_cached_reference_and_appends_audio_port(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict('sys.modules',folder_paths=types.SimpleNamespace(get_output_directory=lambda:temp)), \
             patch.object(canvas,'blocked',side_effect=lambda p:(p['id'],)+(None,)*(len(canvas.RETURN_TYPES)-1)):
            wave=torch.linspace(-.1,.1,430000).reshape(1,1,-1)
            result=node.TalkingAvatar().prepare(audio={'waveform':wave,'sample_rate':24000},
                reference_image=torch.zeros(1,64,32,3),width=768,height=1376,prompt=public_canvas(),unique_id='5')
            plan=project.read_plan(node.root(),result['result'][0]);directory=project.project_path(node.root(),plan['id'])
            self.assertEqual(plan['config']['audio_processing'],audio.DIRECT)
            self.assertEqual(node.TalkingAvatar.RETURN_NAMES[-1],'reference_audio')
            queued=graph.build(plan,plan['segments'][0],{'attempt':'a'*12})
            self.assertFalse(any(n['class_type']=='AudioSeparation' for n in queued.values()))
            self.assertEqual(queued['268']['inputs']['ref_audios.ref_audio_0'],['5',canvas.SLOT['reference_audio']])
            plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(node.root(),plan)
            with patch.dict('sys.modules',nodes=types.SimpleNamespace()):state=canvas.SegmentState().load(plan['id'],0,'a'*12)
            self.assertTrue(torch.equal(state[canvas.SLOT['reference_audio']]['waveform'],state[canvas.SLOT['condition_audio']]['waveform']))
            (directory/'audio/vocals.wav').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'变化|损坏'):project.verify_assets(node.root(),plan)

    def test_prepared_cloud_freezes_only_sampling_and_binds_uploaded_vocals(self):
        source=fixture();frozen=sampling.freeze(source,prepared_audio=True)
        self.assertTrue(frozen['prepared_audio'])
        self.assertFalse(any(n['class_type']=='AudioSeparation' for n in frozen['graph'].values()))
        from test_talking_overlap import config
        cfg=config();cfg.update(rh_sampling=frozen,audio_processing=audio.EXTRACT,audio_preparation_version=1)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'projects'
            talk_nodes=importlib.import_module('h3talk_test.nodes')
            wave=np.ones((430000,1),np.float32)*.1
            from PIL import Image
            with patch.object(talk_nodes,'separate',return_value=wave*.5):
                plan=project.prepare(root,wave,24000,[Image.new('RGB',(32,64))],None,cfg,'input')
            value=graph.build(plan,plan['segments'][0],{'audio':'original.wav','reference_audio':'vocals.wav','references':['person.png'],'attempt':'a'*12})
            self.assertFalse(any(n['class_type']=='AudioSeparation' for n in value.values()))
            reference=value['268']['inputs']['ref_audios.ref_audio_0']
            self.assertEqual(value[reference[0]]['inputs']['audio'],'vocals.wav')
            self.assertEqual(value['272']['inputs']['audio'],['9000',0])

    def test_real_runner_uploads_cached_reference_without_repeating_separation(self):
        from test_talking_overlap import config,runner,FakeClient,overlap
        from PIL import Image
        talk_nodes=importlib.import_module('h3talk_test.nodes')
        for mode in (audio.DIRECT,audio.EXTRACT):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                cfg=config();cfg.update(audio_processing=mode,audio_preparation_version=1,
                    rh_sampling=sampling.freeze(fixture(),prepared_audio=True))
                wave=np.ones((430000,1),np.float32)*.1;root=Path(temp)/'projects'
                with patch.object(talk_nodes,'separate',return_value=wave*.5) as separate:
                    plan=project.prepare(root,wave,24000,[Image.new('RGB',(32,64))],None,cfg,'input')
                    plan.update(approved=True,approved_fingerprint=project.fingerprint(plan));project.write_plan(root,plan)
                    client=FakeClient();uploads={}
                    def upload(path,**kwargs):
                        if Path(path).suffix=='.wav':uploads[Path(path).name]=sf.read(path,dtype='float32',always_2d=True)[0]
                        return str(path)
                    client.upload=upload
                    client.after_query=lambda _:runner.change(root,plan['id'],lambda p:p.update(pause_requested=True))
                    with patch.object(overlap,'resolve'):
                        runner.run_project(root,plan['id'],client,validate=lambda *args:{},sleep=lambda _:None)
                    self.assertEqual(separate.call_count,int(mode==audio.EXTRACT))
                self.assertEqual(len(client.created),1)
                self.assertFalse(any(n['class_type']=='AudioSeparation' for n in client.created[0].values()))
                if mode==audio.EXTRACT:np.testing.assert_array_equal(uploads['reference.wav'],uploads['condition.wav']*.5)
                else:self.assertNotIn('reference.wav',uploads)


if __name__=='__main__':unittest.main()
