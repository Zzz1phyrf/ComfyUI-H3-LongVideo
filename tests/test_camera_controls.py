from test_plugin import core, director_rules, sample_plan
import unittest

class CameraControlsTests(unittest.TestCase):
    def plan(self, widest='medium shot', activity='dynamic'):
        p = sample_plan()
        p.update(samples=9000, duration=90)
        p['director']['rule_config']['singing']['allowed_framings'] = ['medium close-up']
        p['director'].update(widest_framing=widest, camera_activity=activity)
        p['segments'] = [{'start_sample': i*1000, 'end_sample': (i+1)*1000, 'energy_db': -30+i%3*10, 'text': 'vocal'} for i in range(9)]
        return core.decorate(p)

    def test_node_medium_shot_overrides_legacy_single_size(self):
        p = self.plan()
        self.assertIn('medium shot', {r['camera_start'] for r in p['segments']} | {r['camera_end'] for r in p['segments']})

    def test_dynamic_has_visible_motion_and_plain_composition(self):
        for r in self.plan()['segments']:
            self.assertNotIn(r['camera_move_type'], ['micro_reframe', 'steady'])
            self.assertNotIn('眼线', r['prompt'])
            self.assertNotIn('人物居中', r['prompt'])
            self.assertNotIn('人物保持居中', r['prompt'])

    def test_full_shot_and_close_up_are_reachable(self):
        p = self.plan('full shot')
        sizes = {r['camera_start'] for r in p['segments']} | {r['camera_end'] for r in p['segments']}
        self.assertTrue({'close-up', 'full shot'} <= sizes)

    def test_dynamic_truck_is_not_limited_to_brief_drift(self):
        trucks = [r for r in self.plan()['segments'] if r['camera_move_family'] == 'lateral']
        self.assertTrue(trucks)
        for row in trucks:
            self.assertIn('持续平稳横移', row['prompt'])
            self.assertNotIn('短距离', row['prompt'])
            self.assertNotIn('stays short', row['camera_move'])

    def test_speaking_remains_fixed(self):
        p = self.plan('full shot')
        p['mode'] = 'speaking'
        core.decorate(p)
        self.assertTrue(all(r['camera_move_type'] == 'steady' and '全程固定机位' in r['prompt'] for r in p['segments']))



    def test_dolly_brief_has_bounded_final_framing(self):
        for family, start, end in [('dolly out', 'close-up', 'medium close-up'),
                                   ('dolly in', 'medium shot', 'medium close-up')]:
            row = {'camera_move_family': family}
            brief = core.segment_brief({'mode': 'singing'}, row, start, end, '', '')
            self.assertNotIn('人物逐渐变小', brief)
            self.assertNotIn('人物逐渐变大', brief)
            self.assertNotIn('运镜保持进行', brief)
            self.assertIn('最后一帧恰好到达中近景（胸部以上）', brief)
            self.assertIn('取景范围始终处于开场与终点景别之间', brief)

    def test_dolly_never_moves_beyond_available_framings(self):
        for sizes in [['close-up'], ['close-up', 'medium close-up', 'medium shot', 'full shot']]:
            for framing in sizes:
                for movement in ['dolly_in', 'dolly_out']:
                    rules = director_rules.default_config()['singing']
                    rules['energy_movements']['medium'] = [movement]
                    result = core._movement_for(framing, 'front', 'medium', 'moderate', sizes, [], rules)
                    if result[1] in ['dolly in', 'dolly out']:
                        delta = sizes.index(result[0])-sizes.index(framing)
                        self.assertEqual(delta, -1 if result[1] == 'dolly in' else 1)
                    else:
                        self.assertEqual(result[0], framing)
