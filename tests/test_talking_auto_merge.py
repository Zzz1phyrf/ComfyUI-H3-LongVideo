"""CPU-only recovery of completed takes; no model or provider task is started."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from test_talking_overlap import config, overlap, project, runner, canvas


class NoGeneration:
    def __getattr__(self, name):
        raise AssertionError('Completed takes must not call the provider: '+name)


class AutoMergeTests(unittest.TestCase):
    layout = 'projects'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/self.layout
        cfg = config(True)
        cfg.update(width=64, height=64)
        # Thirteen 6.5-second sections, with real CPU-generated video files.
        plan = project.prepare(self.root, np.zeros((2028000, 1), np.float32), 24000,
                               [Image.new('RGB', (64, 64), 'gray')], None, cfg, 'input')
        cuts = [156*i for i in range(1, 13)]
        rows = overlap.manual_clock(plan['samples'], cuts)
        plan.update(segments_clock=copy.deepcopy(rows), segments=rows, manual_cuts=cuts,
                    run_status='generated', approved=True)
        plan.pop('speech_segmentation', None)
        self.pid = plan['id']
        self.directory = project.project_path(self.root, self.pid)
        for row in rows:
            folder = self.directory/'takes'/f'{row["index"]:04d}'/'existing'
            folder.mkdir(parents=True)
            video = folder/'raw.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                            'color=c=gray:s=64x64:r=24', '-frames:v', str(row['generation_frames']),
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
            row['job'] = {'status':'completed', 'task_id':f'existing-{row["index"]}',
                          'video':str(video), 'video_sha256':project.digest(video),
                          'talking_profile':overlap.PROFILE}
        plan['approved_fingerprint'] = project.fingerprint(plan)
        project.write_plan(self.root, plan)
        self.jobs = copy.deepcopy(rows)

    def resume(self):
        runner.start(self.root, self.pid, client_factory=lambda _:NoGeneration())
        thread = runner.THREADS.get(self.pid)
        if thread:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive(), 'Automatic merge deadlocked on its own run lock')
        return project.read_plan(self.root, self.pid)

    def test_thirteen_completed_local_takes_auto_merge_under_the_run_lock(self):
        plan = self.resume()
        self.assertEqual(plan['run_status'], 'completed', plan.get('error'))
        self.assertTrue(Path(plan['final_video']).is_file())
        self.assertFalse(plan['final_stale'])
        self.assertEqual(plan['segments'], self.jobs)
        self.assertEqual(int(runner.controller.probe_video(plan['final_video'])['nb_read_frames']), 2028)
        final = plan['final_video']
        with patch.object(overlap, 'assemble', side_effect=AssertionError('No duplicate merge')):
            self.assertEqual(self.resume()['final_video'], final)

    def test_merge_failure_retains_all_takes_and_retry_never_generates(self):
        with patch.object(overlap, 'assemble', side_effect=RuntimeError('disk full')):
            failed = self.resume()
        self.assertEqual(failed['run_status'], 'failed')
        self.assertIn('disk full', failed['error'])
        self.assertEqual(failed['segments'], self.jobs)
        recovered = self.resume()
        self.assertEqual(recovered['run_status'], 'completed', recovered.get('error'))
        self.assertTrue(Path(recovered['final_video']).is_file())
        self.assertEqual(recovered['segments'], self.jobs)

    def test_pause_after_generation_defers_merge_until_continue(self):
        plan = project.read_plan(self.root, self.pid)
        plan['pause_requested'] = True
        project.write_plan(self.root, plan)
        with patch.object(overlap, 'assemble', side_effect=AssertionError('Paused merge')):
            runner.run_project(self.root, self.pid, NoGeneration())
        paused = project.read_plan(self.root, self.pid)
        self.assertEqual(paused['run_status'], 'paused')
        self.assertFalse(paused.get('final_video'))


if __name__ == '__main__':
    unittest.main()
