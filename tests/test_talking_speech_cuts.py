"""Speech-pause planning is CPU-only and never calls a recognizer or provider."""
import copy
import math
import unittest
import numpy as np
from PIL import Image
import tempfile
from pathlib import Path

from test_talking_overlap import project,overlap,config


def speech(pauses=(),duration=26,scale=1):
    size=round(duration*24000);x=np.arange(size,dtype=np.float32)
    wave=(.18*np.sin(x*.071)+.07*np.sin(x*.11))[:,None]
    for a,b in pauses:wave[round(a*24000):round(b*24000)]=0
    return wave*scale


class SpeechCutTests(unittest.TestCase):
    def prepare(self,wave):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)/'projects'
        plan=project.prepare(root,wave,24000,[Image.new('RGB',(32,64),'gray')],None,config(),'input')
        return root,plan

    def test_preparation_chooses_clear_pauses_instead_of_fixed_window_stride(self):
        gaps=[(5.1,5.9),(11.4,12.2),(18.1,18.9)]
        root,plan=self.prepare(speech(gaps))
        cuts=[r['end_sample']/24000 for r in plan['segments'][:-1]]
        self.assertEqual(len(cuts),3)
        self.assertTrue(all(any(a<t<b for a,b in gaps) for t in cuts),cuts)
        self.assertEqual(plan['speech_segmentation']['fallback_count'],0)
        project.verify_clock(plan)
        for row in plan['segments']:self.assertTrue(4<=row['edit_frames']/24<=15)

    def test_gain_stereo_cancellation_and_short_tail_do_not_shift_pause_decisions(self):
        gaps=[(4.9,5.5),(10.8,11.4),(16.7,17.3)]
        wave=speech(gaps,duration=20.2)
        _,a=self.prepare(wave);_,b=self.prepare(wave*.002)
        _,c=self.prepare(np.concatenate([wave,-wave],axis=1))
        ends=lambda p:[r['end_sample'] for r in p['segments']]
        self.assertEqual(ends(a),ends(b));self.assertEqual(ends(a),ends(c))
        self.assertGreaterEqual(a['segments'][-1]['edit_frames'],96)

    def test_continuous_speech_uses_marked_duration_fallback_not_fake_pause_confidence(self):
        _,plan=self.prepare(speech(duration=33.17))
        self.assertGreater(plan['speech_segmentation']['fallback_count'],0)
        self.assertTrue(all(4<=r['edit_frames']/24<=15 for r in plan['segments']))
        self.assertEqual(sum(r['edit_frames'] for r in plan['segments']),math.ceil(plan['samples']/1000))

    def test_old_project_clock_and_user_manual_cuts_remain_authoritative(self):
        root,plan=self.prepare(speech([(5.1,5.9),(11.4,12.2),(18.1,18.9)]))
        manual=project.update_cuts(root,plan['id'],[6,12,19])
        project.verify_clock(manual);self.assertEqual(overlap.interval(manual,0),(0,144))
        legacy=copy.deepcopy(plan);legacy.pop('speech_segmentation',None)
        legacy['segments_clock']=overlap.clock(legacy['samples']);legacy['segments']=copy.deepcopy(legacy['segments_clock'])
        project.verify_clock(legacy)

    def test_motion_seam_selection_stays_inside_the_detected_pause(self):
        root,plan=self.prepare(speech([(5.1,5.9),(11.4,12.2),(18.1,18.9)]))
        directory=project.project_path(root,plan['id']);frames={}
        for row in plan['segments']:
            path=directory/f'fake-{row["index"]}.mp4';path.write_bytes(bytes([row['index']]))
            row['job']={'status':'completed','video':str(path),'video_sha256':project.digest(path)}
            frames[str(path)]=np.zeros((row['generation_frames'],8,8,3),np.uint8)
        resolved=overlap.resolve(root,plan,decode_frames=lambda path,*_:frames[str(path)])
        for cut,boundary in zip(resolved['overlap_cuts'][1:-1],plan['speech_segmentation']['boundaries']):
            self.assertTrue(boundary['low']<=cut<=boundary['high'])

    def test_motion_refinement_does_not_shorten_a_four_second_segment(self):
        root,plan=self.prepare(speech(duration=20))
        cuts=[120,216,336]
        plan['speech_segmentation']={'algorithm':'speech-pauses-v1','cuts':cuts,
            'boundaries':[{'frame':120,'kind':'pause','low':115,'high':125},
                          {'frame':216,'kind':'pause','low':192,'high':240},
                          {'frame':336,'kind':'pause','low':330,'high':342}],'fallback_count':0}
        plan['segments_clock']=overlap.manual_clock(plan['samples'],cuts)
        plan['segments']=copy.deepcopy(plan['segments_clock'])
        directory=project.project_path(root,plan['id']);frames={}
        for row in plan['segments']:
            path=directory/f'bounded-{row["index"]}.mp4';path.write_bytes(bytes([row['index']]))
            row['job']={'status':'completed','video':str(path),'video_sha256':project.digest(path)}
            frames[str(path)]=np.zeros((row['generation_frames'],8,8,3),np.uint8)
        resolved=overlap.resolve(root,plan,decode_frames=lambda path,*_:frames[str(path)])
        self.assertTrue(all(96<=b-a<=360 for a,b in zip(resolved['overlap_cuts'],resolved['overlap_cuts'][1:])),resolved['overlap_cuts'])

if __name__=='__main__':unittest.main()
