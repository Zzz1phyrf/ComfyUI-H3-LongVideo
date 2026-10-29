"""Target duration must affect segmentation, even without clean phrase gaps."""
import unittest
import numpy as np
from test_plugin import core


class TargetTests(unittest.TestCase):
    def test_five_second_target_is_not_ignored_for_ten_second_phrases(self):
        sr=1000
        audio=np.full((30000,1),.4,np.float32)
        transcript={'segments':[{'start':i,'end':i+10,'text':'singing phrase'} for i in (0,10,20)],'words':[]}
        short=core.segmentation(audio,sr,transcript,'singing',15,5)
        long=core.segmentation(audio,sr,transcript,'singing',15,11)
        self.assertGreater(len(short),len(long))
        self.assertTrue(all(4 <= (r['end_sample']-r['start_sample'])/sr <= 6 for r in short))
        self.assertEqual(sum(r['end_sample']-r['start_sample'] for r in short),len(audio))

    def test_silence_and_sustained_notes_cover_tail_without_short_segments(self):
        sr = 100
        for samples in (70, 401, 700, 1050, 1300, 2997, 5995):
            for level in (0, .4):
                with self.subTest(samples=samples, level=level):
                    rows = core.segmentation(np.full((samples, 1), level), sr, {}, 'singing', 15, 5)
                    self.assertEqual(rows[0]['start_sample'], 0)
                    self.assertEqual(rows[-1]['end_sample'], samples)
                    self.assertTrue(all(a['end_sample'] == b['start_sample'] for a, b in zip(rows, rows[1:])))
                    durations = [(r['end_sample']-r['start_sample'])/sr for r in rows]
                    self.assertTrue(all(d >= 4 for d in durations) if samples >= 400 else len(rows) == 1)
                    # A 7-second source cannot become two >=4-second clips.
                    self.assertTrue(all(d <= 6 for d in durations) if samples != 700 else durations == [7])

    def test_user_maximum_and_fractional_sample_clock_are_respected(self):
        rows = core.segmentation(np.zeros((3011, 1)), 101, {}, 'singing', 5, 5)
        self.assertTrue(all(404 <= r['end_sample']-r['start_sample'] <= 505 for r in rows))
        self.assertEqual(rows[-1]['end_sample'], 3011)

    def test_impossible_duration_range_reports_actionable_error(self):
        with self.assertRaisesRegex(ValueError, '增大最长时长'):
            core.segmentation(np.zeros((600, 1)), 100, {}, 'singing', 5, 5)

    def test_forced_cuts_keep_word_overlap_warnings(self):
        transcript = {'words': [{'start': 0, 'end': 20, 'word': 'sustained'}]}
        rows = core.segmentation(np.full((2000, 1), .4), 100, transcript, 'singing', 15, 5)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r['boundary_kind'] == 'forced' for r in rows[:-1]))
        self.assertTrue(all(any('时间戳重叠' in w for w in r['warnings']) for r in rows[:-1]))
