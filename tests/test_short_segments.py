import copy
import unittest
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

from test_plugin import core, sample_plan, nodes, controller


def updates(plan):
    return [{'end': row['end'], 'prompt': row['prompt']} for row in plan['segments']]


class ShortSegmentTests(unittest.TestCase):
    def test_split_under_four_seconds_keeps_old_take_and_total_clock(self):
        plan = sample_plan()
        plan['segments'][0]['job'] = {'status': 'completed', 'video': 'old.mp4'}
        result = core.edit_plan(plan, updates(plan), split_time=2.59)
        self.assertEqual([r['duration'] for r in result['segments'][:2]], [2.59, 7.41])
        result = core.edit_plan(result, updates(result), split_time=5.62)
        self.assertEqual([r['duration'] for r in result['segments'][:2]], [2.59, 3.03])
        self.assertEqual(sum(r['edit_frames'] for r in result['segments']), 720)
        self.assertEqual(result['segments'][0]['job']['video'], 'old.mp4')
        self.assertTrue(result['segments'][0]['needs_regeneration'])
        self.assertTrue(all(r['generation_frames'] >= 124 for r in result['segments']))
        self.assertEqual(len(plan['segments']), 3)

    def test_move_last_cut_forward_only_invalidates_adjacent_takes(self):
        plan = sample_plan()
        for i, row in enumerate(plan['segments']):
            row['job'] = {'status': 'completed', 'video': f'old{i}.mp4'}
        rows = updates(plan)
        rows[0]['end'], rows[1]['end'] = 12, 27
        core.edit_plan(plan, rows)
        self.assertEqual(plan['segments'][-1]['duration'], 3)
        self.assertEqual(sum(r['edit_frames'] for r in plan['segments']), 720)
        rows = updates(plan)
        rows[1]['end'] = 26.8
        core.edit_plan(plan, rows)
        self.assertEqual(plan['changed_segments'], [1, 2])
        self.assertEqual([r['job']['video'] for r in plan['segments']], ['old0.mp4', 'old1.mp4', 'old2.mp4'])

    def test_merge_two_short_segments_under_four(self):
        plan = core.edit_plan(sample_plan(), updates(sample_plan()), split_time=1)
        plan = core.edit_plan(plan, updates(plan), split_time=2)
        plan = core.edit_plan(plan, updates(plan), remove_cut=0)
        self.assertEqual(plan['segments'][0]['duration'], 2)
        self.assertEqual(len(plan['removed_segments']), 1)

    def test_zero_frames_and_over_fifteen_are_rejected(self):
        for cut in (0, .001, 16):
            plan = sample_plan()
            if cut == 16:
                rows = updates(plan)
                rows[0]['end'] = cut
                with self.assertRaises(ValueError):
                    core.edit_plan(plan, rows)
            else:
                before = copy.deepcopy(plan)
                with self.assertRaises(ValueError):
                    core.edit_plan(plan, updates(plan), split_time=cut)
                self.assertEqual(plan, before)

    def test_short_audio_is_padded_for_the_shared_local_and_rh_loader(self):
        plan = sample_plan()
        plan = core.edit_plan(plan, updates(plan), split_time=2.59)
        plan['approved'] = True
        with tempfile.TemporaryDirectory() as temp, patch.object(nodes, 'data_root', return_value=Path(temp)):
            directory = core.project_path(temp, plan['id'])
            (directory/'audio').mkdir(parents=True)
            for name in ('source.wav', 'vocals.wav'):
                sf.write(core.audio_file(directory, name), np.full((3000, 2), .25), 100, subtype='FLOAT')
            core.write_plan(temp, plan)
            result = nodes.LoadSegment().load(plan['id'], 0)
            self.assertEqual(result[2], 124)
            for audio in result[:2]:
                value = audio['waveform'].numpy()[0]
                self.assertEqual(value.shape, (2, 517))
                np.testing.assert_allclose(value[:, :259], .25)
                np.testing.assert_array_equal(value[:, 259:], 0)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_assembly_trims_padding_and_preserves_original_tail(self):
        plan = sample_plan()
        plan.update(sample_rate=24000, samples=134880, duration=5.62)
        plan['segments'] = [{'start_sample': 0, 'end_sample': 62160},
                            {'start_sample': 62160, 'end_sample': 134880}]
        core.decorate(plan)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'projects'
            directory = core.project_path(root, plan['id'])
            (directory/'audio').mkdir(parents=True)
            wave = .3*np.sin(np.arange(plan['samples'])*2*np.pi*440/24000)
            sf.write(core.audio_file(directory, 'source.wav'), np.column_stack((wave, wave)), 24000)
            for i, row in enumerate(plan['segments']):
                video = directory/f'take{i}.mp4'
                controller.command([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=c={"red" if i == 0 else "blue"}:s=64x64:r=24', '-frames:v', str(row['generation_frames']),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)])
                row['job'] = {'status': 'completed', 'video': str(video)}
            core.write_plan(root, plan)
            final = controller.assemble(root, plan['id'])
            second = controller.assemble(root, plan['id'])
            self.assertNotEqual(final, second)
            self.assertTrue(Path(final).is_file())
            info = controller.probe_video(second)
            self.assertEqual(int(info['nb_read_frames']), 135)
            self.assertAlmostEqual(float(info['duration']), 5.625, places=3)
            decoded = Path(temp)/'audio.wav'
            controller.command([shutil.which('ffmpeg'), '-v', 'error', '-i', second, '-vn', '-ar', '24000', str(decoded)])
            actual, sr = sf.read(decoded)
            self.assertGreaterEqual(len(actual), len(wave))
            self.assertGreater(np.corrcoef(actual[len(wave)-24000:len(wave), 0], wave[-24000:])[0, 1], .99)


if __name__ == '__main__':
    unittest.main()
