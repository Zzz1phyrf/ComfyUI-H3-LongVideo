"""Local and RH submit the same declared reference roles."""
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import Mock, patch

import torch
import test_materials as fixtures
import test_mv_rh as cloud


class BackendParityTests(unittest.TestCase):
    setUp = fixtures.MaterialTests.setUp

    def test_cloud_reference_count_follows_visual_type_and_keeps_manual_prompt(self):
        for kind in ('performance', 'environment', 'atmosphere'):
            with self.subTest(kind=kind):
                material = fixtures.materials.packet(self.plan, self.plan['segments'][0], self.directory)
                material['visual_type'] = kind
                wave = {'waveform':torch.zeros(1,1,16000),'sample_rate':16000}
                loaded = (wave,wave,141,'prefix',material,*([None]*6),'manual',24)
                original = cloud.graph()
                snapshot = {'remote_graph':original,'binding':cloud.remote.binding(original),
                            'prompt_source':{'kind':'literal','text':'manual prompt stays exact'}}
                folder = self.directory/kind;folder.mkdir()
                client = Mock();client.upload.side_effect = lambda path:Path(path).name
                with patch.object(cloud.nodes.LoadSegment, 'load', return_value=loaded), \
                     patch('h3lv_test.progress.report'):
                    graph, text = cloud.remote.build_segment(self.directory,self.plan['id'],0,snapshot,client,folder)
                audio = {key:value for key,value in graph['136']['inputs'].items() if key.startswith('ref_audios.')}
                self.assertEqual(audio, {'ref_audios.ref_audio_0':['9001',0]} if kind=='performance' else {})
                self.assertEqual(text, 'manual prompt stays exact')
                self.assertEqual(original, snapshot['remote_graph'])
                self.assertEqual(graph['249']['inputs']['value'], 0)
                cloud.remote.binding(graph,vocal_reference=kind=='performance')

    def test_new_and_historical_cloud_submissions_keep_their_exact_audio_contract(self):
        for has_audio in (True,False):
            with self.subTest(historical=has_audio),tempfile.TemporaryDirectory() as folder:
                graph=cloud.graph();mapping=cloud.remote.binding(graph)
                if not has_audio:graph['136']['inputs'].pop('ref_audios.ref_audio_0')
                path=Path(folder)/'raw.mp4';path.write_bytes(b'existing paid take')
                metadata=json.dumps({'format':{'tags':{'prompt':json.dumps(graph)}}})
                with patch.object(cloud.controller,'probe_video',return_value={'r_frame_rate':'24/1','nb_read_frames':141,'width':64,'height':64}), \
                     patch.object(cloud.controller,'command',return_value=metadata):
                    result=cloud.remote.validate_output(path,graph,{'generation_frames':141},mapping)
                self.assertTrue(result['graph_verified'])


if __name__ == '__main__':
    unittest.main()
