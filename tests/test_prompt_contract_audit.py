"""Adversarial formatting and cache inputs at the real expansion boundary."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_materials as fixtures

expansion = fixtures.expansion


class PromptContractAuditTests(unittest.TestCase):
    setUp = fixtures.MaterialTests.setUp

    def packet(self):
        return fixtures.materials.packet(self.plan, self.plan['segments'][0], self.directory)

    def test_heading_case_numbering_and_markdown_are_presentation(self):
        prompt = fixtures.MULTI_TEXT
        for index, heading in enumerate(expansion.HEADINGS, 1):
            prompt = prompt.replace(heading+':', f'## {index}. **{heading.upper()}**：')
        result = expansion.validate_result(prompt, self.packet())
        self.assertEqual(result, fixtures.MULTI_TEXT)

    def test_case_and_spacing_do_not_bypass_picture_range_check(self):
        for tag in ('<picture 3>', '<PICTURE 3 >', '<Picture3>'):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, '不存在的图片'):
                expansion.validate_prompt(fixtures.MULTI_TEXT+'\n'+tag, 2)

    def test_duplicate_fields_and_empty_visual_description_are_rejected(self):
        for prompt in (fixtures.MULTI_TEXT.replace('summary:', 'summary:\nDuplicate.\nsummary:'),
                       fixtures.MULTI_TEXT.replace('[Shot 1] Move.', '[Shot 1]')):
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                expansion.validate_result(prompt, self.packet())

    def test_inline_field_words_do_not_split_reference_definitions(self):
        prompt = fixtures.MULTI_TEXT.replace('<Subject 1> from <Picture 1>.',
            '<Subject 1> from <Picture 1>. Its visual summary: the scene comes from <Picture 2>.')
        self.assertIn('Its visual summary:', expansion.validate_result(prompt, self.packet()))

    def test_workflow_owned_sound_sections_are_filled_and_fixed_to_na(self):
        for prompt in (fixtures.MULTI_TEXT.split('overall_soundscape:')[0],
                       fixtures.MULTI_TEXT.replace('overall_soundscape:\nN/A\n',''),
                       fixtures.MULTI_TEXT.replace('non_diegetic_music:\nN/A',''),
                       fixtures.MULTI_TEXT.replace('overall_soundscape:\nN/A', 'overall_soundscape:\nThunder.')
                                         .replace('non_diegetic_music:\nN/A', 'non_diegetic_music:\nOrchestra.')):
            with self.subTest(prompt=prompt):
                result = expansion.validate_result(prompt, self.packet())
                self.assertTrue(result.endswith('overall_soundscape:\nN/A\nnon_diegetic_music:\nN/A'))
                self.assertIn('[Shot 1] Move.', result)
                self.assertEqual(expansion.validate_result(result, self.packet()), result)

    def test_corrupt_cache_allows_exactly_one_explicit_new_request(self):
        packet = self.packet()
        with patch.object(expansion, 'public_settings', return_value={'base_url':'https://test.invalid/v1'}):
            key = expansion.cache_key(packet, 'vision', 'model', '', 0)
            cache = Path(packet['cache_dir']);cache.mkdir(parents=True)
            path = cache/(key+'.json')
            for damaged in ('{broken', '{"text":42}', '{"wrong":"old"}', '{"text":"```"}'):
                with self.subTest(damaged=damaged), patch.object(expansion, 'call', return_value={
                    'choices':[{'message':{'content':fixtures.MULTI_TEXT}}]}) as request:
                    path.write_text(damaged, encoding='utf-8')
                    self.assertEqual(expansion.expand(packet, 'vision', 'model', ''), fixtures.MULTI_TEXT)
                    request.assert_called_once()

    def test_failed_request_keeps_corrupt_cache_evidence_and_does_not_retry(self):
        packet = self.packet()
        with patch.object(expansion, 'public_settings', return_value={'base_url':'https://test.invalid/v1'}):
            key = expansion.cache_key(packet, 'vision', 'model', '', 0)
            cache = Path(packet['cache_dir']);cache.mkdir(parents=True)
            path = cache/(key+'.json');path.write_text('{broken', encoding='utf-8')
            with patch.object(expansion, 'call', side_effect=ValueError('API offline')) as request:
                with self.assertRaisesRegex(ValueError, 'API offline'):
                    expansion.expand(packet, 'vision', 'model', '')
                request.assert_called_once()
            self.assertEqual(path.read_text(encoding='utf-8'), '{broken')


if __name__ == '__main__':
    unittest.main()
