"""Replay the reported two-picture response without a paid API call."""
import unittest
from unittest.mock import patch

import test_materials as fixtures
materials, expansion, TEXT = fixtures.materials, fixtures.expansion, fixtures.TEXT


REPORTED = '''subject_definitions:
<Subject 1> 是佩戴金色发簪、穿奶油色汉服的年轻女子。

summary:
[reference generation] 根据已声明人物和木亭场景生成一个连续镜头。

retention_analysis:
<Subject 1>：fully_preserved - 保留来自 <Picture 1> 的人物身份、头发和奶油色汉服。
<Picture 2>：fully_preserved - 保留木亭、茶具、荷塘与开场构图。

detailed_description:
写实摄影风格，光线柔和，木亭结构与人物比例自然。
[Shot 1] <Subject 1> 在 <Picture 2> 所示木亭中自然表演，保持人物服装与场景布局，镜头缓慢前移并稳定结束。
所有画面均不新增字幕、标题、歌词、水印或图形叠加；不转录参考图背景中的文字。

overall_soundscape:
N/A

non_diegetic_music:
N/A'''


class PictureReferenceTests(unittest.TestCase):
    setUp = fixtures.MaterialTests.setUp

    def packet(self):
        return materials.packet(self.plan, self.plan['segments'][0], self.directory)

    def test_reported_sources_in_retention_are_accepted_with_one_subject(self):
        with patch.object(expansion, 'public_settings', return_value={'base_url':'https://test.invalid/v1'}), \
             patch.object(expansion, 'call', return_value={'choices':[{'message':{'content':REPORTED}}]}) as request:
            result = expansion.expand(self.packet(), 'vision', 'model', '')
        self.assertIn('<Picture 1>', result)
        self.assertIn('<Picture 2>', result)
        self.assertNotIn('<Subject 2>', result)
        self.assertIn('<Picture 1>', result.split('summary:')[0])
        self.assertIn('<Picture 2>', result.split('summary:')[0])
        self.assertEqual(request.call_count, 1)
        content = request.call_args.args[1]['messages'][1]['content']
        self.assertEqual([item['type'] for item in content], ['text', 'image_url', 'image_url'])
        self.assertIn('"number": 2', content[0]['text'])
        self.assertEqual(expansion.validate_result(result, self.packet()), result)

    def test_truly_missing_second_picture_is_rejected_by_number(self):
        with self.assertRaisesRegex(ValueError, 'Picture 2'):
            expansion.validate_result(TEXT, self.packet())

    def test_two_views_of_one_person_keep_a_single_subject(self):
        prompt = fixtures.MULTI_TEXT.replace(
            '的场景、光影与构图参考，并使用 <Picture 2> 提供的环境布局。',
            '和 <Picture 2> 的同一人物参考，保留两张角度中的身份与服装。')
        self.assertEqual(expansion.validate_result(prompt, self.packet()), prompt)

    def test_missing_all_sources_still_rejects_ambiguous_multi_image_output(self):
        missing = REPORTED.replace('<Picture 1>', '第一张照片').replace('<Picture 2>', '第二张照片')
        with self.assertRaisesRegex(ValueError, '多图'):
            expansion.validate_result(missing, self.packet())

    def test_out_of_range_picture_remains_an_error(self):
        with self.assertRaisesRegex(ValueError, '不存在的图片'):
            expansion.validate_result(REPORTED.replace('<Picture 2>', '<Picture 3>'), self.packet())

    def test_unbound_reference_only_in_action_does_not_invent_a_source_role(self):
        prompt = TEXT.replace('[Shot 1] '+fixtures.SHOT_BODY, '[Shot 1] 在 <Picture 2> 所示环境中移动。')
        with self.assertRaisesRegex(ValueError, 'Picture 2'):
            expansion.validate_result(prompt, self.packet())


if __name__ == '__main__':
    unittest.main()
