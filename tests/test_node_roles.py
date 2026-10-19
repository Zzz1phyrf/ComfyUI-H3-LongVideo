"""Public node roles remain distinct while saved node IDs stay compatible."""
import unittest
from unittest.mock import patch

from test_plugin import nodes, controller
from test_talking_rh import project


class NodeRoleTests(unittest.TestCase):
    def test_public_names_and_singing_only_contract(self):
        self.assertEqual(nodes.NODE_DISPLAY_NAME_MAPPINGS['H3LVUnified'], 'MiniMax H3 · MV Generator')
        self.assertEqual(nodes.NODE_DISPLAY_NAME_MAPPINGS['H3LVPromptExpand'], 'MiniMax H3 · MV Prompt Expander')
        self.assertEqual(nodes.NODE_DISPLAY_NAME_MAPPINGS['H3LVTalkingRH'], 'MiniMax H3 · Talking Avatar')
        self.assertEqual(nodes.Unified.INPUT_TYPES()['required']['mode'][0], ['singing'])

    def test_legacy_spoken_mode_stops_before_analysis_or_generation(self):
        with patch.object(nodes, 'audio_array', side_effect=AssertionError('must not analyze')):
            with self.assertRaisesRegex(ValueError, 'Talking Avatar'):
                nodes.Analyze().analyze(None, 'speaking', 15, 11, '', '')
            with self.assertRaisesRegex(ValueError, 'Talking Avatar'):
                nodes.Unified().process(None, 'speaking', 15, 11, '', '')
        with patch.object(controller, 'read_plan', return_value={'mode':'speaking'}):
            with self.assertRaisesRegex(ValueError, 'Talking Avatar'):
                controller.start(None, 'legacy-spoken-project', {}, None)

    def test_mv_expander_rejects_spoken_packet_before_paid_api(self):
        with patch('h3lv_test.expansion.expand', side_effect=AssertionError('must not call API')):
            with self.assertRaisesRegex(ValueError, 'Talking Avatar'):
                nodes.PromptExpand().run({'mode':'speaking'}, 'vision', 'model', '')

    def test_point_eight_megapixel_clock_and_assets_supported(self):
        self.assertEqual(project.dimensions(.8), (672,1216))
        self.assertEqual(project.dimensions(1.), (768,1376))


if __name__ == '__main__':
    unittest.main()
