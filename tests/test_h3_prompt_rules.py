"""Chinese Ref2VA contracts and metadata gates; no model service is called."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_materials as fixtures

expansion = fixtures.expansion
rules = expansion.h3_prompt_rules


def audio_prompt():
    text = fixtures.TEXT.replace('是来自 <Picture 1> 的场景、光影与构图参考。',
                                 '是来自 <Picture 1> 的表演者，保留身份和服装。')
    text = text.replace('\n\nsummary:', '\n<Audio 1> 是 <Subject 1> (S1) 的已确认人声音频参考。\n\nsummary:')
    text = text.replace('[reference generation]', '[reference generation + audio reference]')
    text = text.replace('\n\ndetailed_description:', '\n<Audio 1>：reference - 只参考已确认人声，不复制波形。\n\ndetailed_description:')
    return text.replace(fixtures.SHOT_BODY, '<Subject 1> (S1) 根据 <Audio 1> 连续进行自然口型表演，保持声音对应的停顿和呼吸。')


class H3PromptRulesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.profile = {'base_url': 'https://fixture.test/v1', 'api_key': 'private-fixture'}
        self.material = dict(paths=['fixture.png'], hashes=['fixture'], material_note='@图1为场景',
            visual_type='performance', mode='singing', brief='沿用当前场景和运镜。', duration=10,
            generation_frames=242, generation_seconds=242/24, audio_role='uncertain',
            audio_section='vocal', audio_role_reason='检测存在不确定性', cache_dir=str(self.directory/'cache'))

    def test_chinese_prose_with_fixed_labels_and_equivalent_no_overlay_sentence(self):
        text = fixtures.TEXT.replace(rules.NO_OVERLAY, '每一帧均不添加字幕、歌词、水印或图形叠加，不转录参考图背景文字。')
        self.assertEqual(expansion.validate_prompt(text, 1, self.material), text)

    def test_missing_task_retention_or_subject_is_not_invented(self):
        cases = [fixtures.TEXT.replace('[reference generation]', ''),
                 fixtures.TEXT.replace('<Subject 1>：fully_preserved - 保留场景布局与材质。', '保留场景。'),
                 fixtures.TEXT.replace('<Subject 1> 是来自 <Picture 1> 的场景、光影与构图参考。', '场景参考 <Picture 1>。'),
                 fixtures.TEXT.replace('fully_preserved', 'reference'),
                 fixtures.TEXT.replace('fully_preserved', 'keep_scene')]
        for text in cases:
            with self.subTest(text=text[:70]), self.assertRaises(ValueError):
                expansion.validate_prompt(text, 1)

    def test_duplicate_retention_undefined_labels_and_wrong_subject_numbering_fail(self):
        line = '<Subject 1>：fully_preserved - 保留场景布局与材质。'
        cases = [fixtures.TEXT.replace(line, line+'\n'+line),
                 fixtures.TEXT.replace(fixtures.SUMMARY, fixtures.SUMMARY+'<Subject 2> 进入场景。'),
                 fixtures.TEXT.replace('<Subject 1>', '<Subject 2>'),
                 fixtures.TEXT.replace('<Picture 1>', '<Picture 0>'),
                 fixtures.TEXT.replace('<Picture 1>', '<Picture X>')]
        for text in cases:
            with self.subTest(text=text[:70]), self.assertRaises(ValueError):
                expansion.validate_prompt(text, 1)

    def test_chapter_spacing_and_style_opening_are_required(self):
        for text in (fixtures.TEXT.replace('\n\nsummary:', '\nsummary:'),
                     fixtures.TEXT.replace(fixtures.STYLE+'\n', ''),
                     fixtures.TEXT+'\n\nsummary:\n[reference generation] 重复章节。'):
            with self.subTest(text=text[:70]), self.assertRaises(ValueError):
                expansion.validate_prompt(text, 1)

    def test_english_descriptions_fail_even_with_valid_fixed_tokens(self):
        text = fixtures.TEXT.replace(fixtures.SUMMARY, '[reference generation] One continuous shot.')
        with self.assertRaisesRegex(ValueError, '中文'):
            expansion.validate_prompt(text, 1)

    def test_only_one_untimed_continuous_shot_and_no_base_mode_shell(self):
        cases = [fixtures.TEXT.replace('[Shot 1]', '[Shot 1] At 00:00.000,'),
                 fixtures.TEXT.replace('[Shot 1]', '[Shot 1] 转动。\n[Shot 2] At 00:04.000,'),
                 'For the target video, at 0.00 seconds into the target video.\n\n'+fixtures.TEXT]
        for text in cases:
            with self.subTest(text=text[:70]), self.assertRaises(ValueError):
                expansion.validate_prompt(text, 1)

    def test_n_a_sound_fields_cannot_be_replaced_by_new_audio(self):
        for field in ('overall_soundscape', 'non_diegetic_music'):
            text = fixtures.TEXT.replace(field+':\nN/A', field+':\n增加风声和钢琴配乐。')
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'N/A'):
                expansion.validate_prompt(text, 1)

    def test_actual_generation_duration_is_checked_before_any_provider_request(self):
        for seconds in (3.99, 15.01, float('nan'), True, None):
            material = {**self.material, 'generation_seconds': seconds}
            with self.subTest(seconds=seconds), patch.object(expansion, 'call') as provider:
                with self.assertRaisesRegex(ValueError, 'generation_seconds'):
                    expansion.expand(material, 'text', 'fixture', '规则', request_profile=self.profile)
                provider.assert_not_called()
        material = {**self.material, 'generation_seconds': 10}
        with self.assertRaisesRegex(ValueError, 'generation_frames / 24'):
            expansion.validate_prompt(fixtures.TEXT, 1, material)
        # The audio clip's original length must not replace the actual generation length.
        expansion.validate_prompt(fixtures.TEXT, 1, {**self.material, 'duration': 500})

    def test_audio_requires_confirmed_performance_slot_and_valid_tracking(self):
        text = audio_prompt()
        expansion.validate_prompt(text, 1)  # Pure text review does not claim verified media slots.
        valid = {**self.material, 'audio_reference_available': True, 'audio_reference_slot': 1}
        expansion.validate_prompt(text, 1, valid)
        for material in (self.material, {**valid, 'audio_reference_slot': 2},
                         {**valid, 'audio_reference_slot': True}, {**valid, 'visual_type': 'atmosphere'},
                         {**valid, 'visual_type': 'environment'}):
            with self.subTest(material=material), self.assertRaises(ValueError):
                expansion.validate_prompt(text, 1, material)
        for damaged in (text.replace(' + audio reference', ''), text.replace(' (S1)', ''),
                        text.replace('<Audio 1>', '<Audio 2>'), text.replace('：reference -', '：fully_copy -')):
            with self.subTest(damaged=damaged[:70]), self.assertRaises(ValueError):
                expansion.validate_prompt(damaged, 1, valid)

    def test_atmosphere_and_environment_reject_positive_vocals_but_allow_negative_instructions(self):
        for kind in ('atmosphere', 'environment'):
            material = {**self.material, 'visual_type': kind}
            for body in ('人物跟唱并做同步口型。', '人物开口说话。', '嘴部开合与节拍同步。'):
                with self.subTest(kind=kind, body=body), self.assertRaisesRegex(ValueError, '口型'):
                    expansion.validate_prompt(fixtures.TEXT.replace(fixtures.SHOT_BODY, body), 1, material)
            text = fixtures.TEXT.replace(fixtures.SHOT_BODY, '嘴唇始终闭合，不演唱、不说话，也不做口型。')
            expansion.validate_prompt(text, 1, material)

    def test_invalid_saved_and_disk_cache_are_revalidated_before_reuse(self):
        bad = fixtures.TEXT.replace('fully_preserved', 'bad_marker')
        key = expansion.cache_key(self.material, 'text', 'fixture', '规则', 0, profile=self.profile)
        cache = Path(self.material['cache_dir'])
        cache.mkdir()
        (cache/(key+'.json')).write_text(json.dumps({'text': bad}), encoding='utf-8')
        material = {**self.material, 'expanded_key': key, 'expanded_prompt': bad}
        self.assertEqual(expansion.cache_source(material, key, 'text'), 'api')
        with patch.object(expansion, 'call', return_value={'choices': [{'message': {'content': fixtures.TEXT}}]}) as provider:
            self.assertEqual(expansion.expand(material, 'text', 'fixture', '规则', request_profile=self.profile), fixtures.TEXT)
        provider.assert_called_once()
        self.assertEqual(json.loads((cache/(key+'.json')).read_text(encoding='utf-8'))['text'], fixtures.TEXT)

    def test_invalid_new_response_never_enters_success_cache(self):
        bad = fixtures.TEXT.replace('fully_preserved', 'bad_marker')
        with patch.object(expansion, 'call', return_value={'choices': [{'message': {'content': bad}}]}) as provider:
            with self.assertRaises(ValueError):
                expansion.expand(self.material, 'text', 'fixture', '规则', request_profile=self.profile)
        provider.assert_called_once()
        self.assertFalse(list(Path(self.material['cache_dir']).glob('*.json')))

    def test_manual_raw_text_stays_unvalidated_and_does_not_call_provider(self):
        raw = '手写提示词逐字输出，不要求六章节。'
        material = {**self.material, 'brief': raw, 'generation_seconds': 18}
        material['material_note'] = ''
        material['shot_intent'] = ''
        with patch.object(expansion, 'call', side_effect=AssertionError('Manual called a provider')):
            self.assertEqual(expansion.expand(material, 'manual', 'fixture', '规则', request_profile=self.profile), raw)

    def test_confirmed_audio_metadata_changes_cache_identity(self):
        first = expansion.cache_key(self.material, 'text', 'fixture', '规则', 0, profile=self.profile)
        material = {**self.material, 'audio_reference_available': True, 'audio_reference_slot': 1}
        self.assertNotEqual(first, expansion.cache_key(material, 'text', 'fixture', '规则', 0, profile=self.profile))

    def test_density_warning_is_preserved_as_advice_not_a_hard_error(self):
        report = rules.validate_prompt(fixtures.TEXT, 'ref2va', self.material['generation_seconds'])
        self.assertTrue(report.ok)
        self.assertTrue(any('density' in warning for warning in report.warnings))
        expansion.validate_prompt(fixtures.TEXT, 1, self.material)


if __name__ == '__main__':
    unittest.main()
