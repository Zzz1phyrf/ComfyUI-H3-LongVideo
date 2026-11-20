"""Audio availability declarations must not hide a missing-asset instruction."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_materials import expansion, TEXT

diagnostics = importlib.import_module('h3lv_test.diagnostics')


class ExpansionAudioReferenceTests(unittest.TestCase):
    def setUp(self):
        # Expected validation failures must not enter the user's error log.
        logger = patch.object(diagnostics, 'record_error', return_value=True)
        logger.start(); self.addCleanup(logger.stop)

    def packet(self, visual_type='atmosphere', cache_dir='unused'):
        return dict(paths=['fixture.png'], hashes=['fixture'], contract_version=2,
            material_note='@图1是人物', visual_type=visual_type, mode='singing',
            brief='Keep the approved shot.', duration=6, generation_frames=158,
            generation_seconds=158/24, audio_role='vocal', audio_section='vocal',
            audio_role_reason='fixture', cache_dir=cache_dir)

    def body(self, sentence, heading='summary'):
        sections = expansion.prompt_sections(TEXT)
        sections[heading] += '\n' + sentence
        return expansion.join_sections(sections)

    def test_explicit_absence_is_normalized_in_each_visual_type(self):
        sentences = [
            'There is no <Audio 1>.',
            'No <Audio 1> is provided.',
            '<Audio 1> is not available.',
            'No <Audio 1> is defined because this is an atmosphere segment and the on-screen performer must remain non-vocal.',
            'The segment is an atmosphere performance, so no <Audio 1> exists and the performer never opens her mouth to sing or speak.',
            'No <Audio 1> is defined because the visual_type is environment and no performer is present.',
            'An environment-only, continuous shot with no performer; there is no <Audio 1>.',
            '未提供<Audio 1>。',
            '本段没有接入<Audio 1>。',
        ]
        for visual_type in ('atmosphere', 'environment'):
            for sentence in sentences:
                with self.subTest(visual_type=visual_type, sentence=sentence):
                    result = expansion.validate_result(self.body(sentence), self.packet(visual_type))
                    self.assertNotIn('<Audio', result)
                    self.assertIn('<Subject 1> from <Picture 1>.', result)
                    self.assertIn('[Shot 1] Move.', result)

    def test_denials_in_any_section_are_checked_before_sound_normalization(self):
        for heading in expansion.HEADINGS:
            with self.subTest(heading=heading):
                result = expansion.validate_result(self.body('There is no <Audio 1>.', heading), self.packet())
                self.assertNotIn('<Audio', result)

    def test_scoped_missing_asset_declarations_keep_the_visual_direction(self):
        examples = (
            ('No <Audio 1> reference exists for this segment because the performers remain closed-mouth throughout.',
             'No vocal reference exists for this segment because the performers remain closed-mouth throughout.'),
            ('There is no <Audio 1> track in this shot.', 'There is no vocal reference in this shot.'),
            ('<Audio 1> is not available for this segment.', 'vocal reference is not available for this segment.'),
            ('No <Audio 1> input is provided because no performer is present.',
             'No vocal reference is provided because no performer is present.'),
        )
        for source, expected in examples:
            with self.subTest(source=source):
                result = expansion.validate_result(self.body(source), self.packet())
                self.assertIn(expected, result)
                self.assertNotIn('<Audio', result)

    def test_scoped_denial_cannot_hide_a_use_or_unverified_reason(self):
        denial = 'No <Audio 1> reference exists for this segment'
        for suffix in (' because the performer follows its timing.',
                       ' because <Audio 2> supplies the vocals.',
                       ' unless a performer sings.',
                       '. Use it for lip sync.',
                       '. It is used for lip sync.',
                       ' because the performers remain closed-mouth throughout. Follow <Audio 1>.'):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, '人声参考'):
                expansion.validate_result(self.body(denial + suffix), self.packet())

    def test_absence_allows_explicit_visual_reference_instructions(self):
        for visual_type in ('atmosphere', 'environment'):
            for asset in ('reference image', 'reference picture', 'reference photo', 'reference frame'):
                sentence = 'No <Audio 1> is provided. The shot uses the '+asset+' for the background.'
                with self.subTest(visual_type=visual_type, asset=asset):
                    result = expansion.validate_result(self.body(sentence), self.packet(visual_type))
                    self.assertNotIn('<Audio', result)
                    self.assertIn('The shot uses the '+asset+' for the background.', result)

    def test_vocal_motion_cannot_refer_back_to_unavailable_audio(self):
        sentences = (
            'No <Audio 1> is provided. Mouth motion comes from its rhythm.',
            'No <Audio 1> is provided. Her lips move to its beat.',
            'No <Audio 1> is provided. Lip sync is driven by it.',
            '未提供<Audio 1>。口型来自它的节奏。',
            '未提供<Audio 1>。嘴唇按照其节奏运动。',
        )
        for visual_type in ('atmosphere', 'environment'):
            for sentence in sentences:
                with self.subTest(visual_type=visual_type, sentence=sentence), self.assertRaisesRegex(ValueError, '人声参考'):
                    expansion.validate_result(self.body(sentence), self.packet(visual_type))

    def test_real_mixed_conditional_and_ambiguous_references_stay_rejected(self):
        sentences = [
            'The performer follows <Audio 1> for lip synchronization.',
            'No <Audio 1> is provided. Follow <Audio 1> for singing.',
            'No <Audio 1> is provided, but follow it for singing.',
            'No <Audio 1> is provided; follow it for singing.',
            'No <Audio 1> is provided and use it for lip sync.',
            'There is no <Audio 1>, but synchronize the lips to it.',
            'If there is no <Audio 1>, use the fallback.',
            'Do not say there is no <Audio 1>.',
            'Do not ignore <Audio 1>.',
            'No <Audio 1> is provided unless the performer sings.',
            'No <Audio 1> is provided because the singer follows its rhythm.',
            'No <Audio 1> is defined as a scene; it is the voice reference.',
            'The supplied assets include no <Audio 1>, which drives mouth motion.',
            'No <Audio 1> is provided. Use that audio for lip sync.',
            'No <Audio 1> is provided. The performer follows its timing.',
            'No <Audio 1> is provided. Sing along to it.',
            'No <Audio 1> is provided. It drives the mouth movements.',
            'No <Audio 1> is provided. The lips are aligned to its beat.',
            'No <Audio 1> is provided. Mouth motion is synchronized with it.',
            '未提供<Audio 1>。根据它的节奏演唱。',
            'No <Audio 1> is provided. Follow <Audio 2>.',
            '未提供<Audio 1>，但使用它驱动口型。',
        ]
        for sentence in sentences:
            with self.subTest(sentence=sentence), self.assertRaisesRegex(ValueError, '人声参考'):
                expansion.validate_result(self.body(sentence), self.packet())

    def test_positive_missing_reference_in_every_section_is_rejected(self):
        for heading in expansion.HEADINGS:
            with self.subTest(heading=heading), self.assertRaisesRegex(ValueError, '人声参考'):
                expansion.validate_result(self.body('Follow <Audio 1>.', heading), self.packet())

    def test_performance_keeps_supplied_audio_and_rejects_other_numbers(self):
        packet = self.packet('performance')
        valid = self.body('The performer follows <Audio 1>.')
        result = expansion.validate_result(valid, packet)
        self.assertIn('The performer follows <Audio 1>.', result)
        denied_extra = expansion.validate_result(self.body('No <Audio 2> is provided.'), packet)
        self.assertNotIn('<Audio 2>', denied_extra)
        self.assertIn('<Audio 1>', denied_extra)
        for sentence in ('Follow <Audio 2>.', 'No <Audio 2> is provided. Follow it for singing.'):
            with self.subTest(sentence=sentence), self.assertRaisesRegex(ValueError, '未接入的音频'):
                expansion.validate_result(self.body(sentence), packet)

    def test_reference_spellings_cannot_bypass_normalization_or_rejection(self):
        for token in ('<audio_1>', '[AUDIO #1]', '＜Audio １＞', '&lt;Audio 1&gt;', 'Audio 1'):
            with self.subTest(token=token):
                result = expansion.validate_result(self.body('There is no '+token+'.'), self.packet())
                self.assertNotIn('<Audio', result)
                with self.assertRaisesRegex(ValueError, '人声参考'):
                    expansion.validate_result(self.body('Follow '+token+'.'), self.packet())

    def test_saved_cache_and_invalid_cache_recover_without_an_api_request(self):
        for source in ('saved', 'json', 'invalid'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                packet = self.packet(cache_dir=tmp)
                raw = self.body('There is no <Audio 1>.')
                profile = {'base_url': 'https://fixture.invalid/v1'}
                with patch.object(expansion, 'public_settings', return_value=profile), \
                     patch.object(expansion, 'call', side_effect=AssertionError('No paid API request')) as call:
                    key = expansion.cache_key(packet, 'text', 'fixture', 'fixture', 0)
                    if source == 'saved':
                        packet.update(expanded_key=key, expanded_prompt=raw)
                    elif source == 'json':
                        (Path(tmp)/(key+'.json')).write_text(json.dumps({'text':raw}), encoding='utf-8')
                    else:
                        (Path(tmp)/(key+'.invalid.txt')).write_text(raw, encoding='utf-8')
                    self.assertIn(expansion.cache_source(packet,key,'text'), ('saved','cache'))
                    result = expansion.PromptExpand().run(packet,'text','fixture','fixture')['result'][0]
                    self.assertNotIn('<Audio', result)
                    call.assert_not_called()

    def test_fresh_api_result_is_checked_and_invalid_use_is_not_cached(self):
        for sentence, valid in [('There is no <Audio 1>.', True), ('Follow <Audio 1>.', False)]:
            with self.subTest(sentence=sentence), tempfile.TemporaryDirectory() as tmp:
                packet = self.packet(cache_dir=tmp)
                response = {'choices':[{'message':{'content':self.body(sentence)}}]}
                with patch.object(expansion, 'public_settings', return_value={'base_url':'https://fixture.invalid/v1'}), \
                     patch.object(expansion, 'call', return_value=response) as call:
                    if valid:
                        result = expansion.PromptExpand().run(packet,'text','fixture','fixture')['result'][0]
                        self.assertNotIn('<Audio', result)
                        expansion.PromptExpand().run(packet,'text','fixture','fixture')
                    else:
                        with self.assertRaisesRegex(ValueError, '人声参考'):
                            expansion.PromptExpand().run(packet,'text','fixture','fixture')
                        self.assertFalse(list(Path(tmp).glob('*.json')))
                        self.assertEqual(len(list(Path(tmp).glob('*.invalid.txt'))), 1)
                    self.assertEqual(call.call_count, 1)


if __name__ == '__main__':
    unittest.main()
