"""Replay explicit picture provenance written without H3 angle brackets."""
from pathlib import Path
import unittest
from unittest.mock import patch

import test_materials as fixtures

expansion=fixtures.expansion
BARE='''subject_definitions:
Picture 1 is the person reference, defined as <Subject 1>: one performer in an ivory gown. Picture 2 is the environment reference, defined as <Subject 2>: a garden pond and wooden railing. <Audio 1> supplies the vocal timing.
summary:
<Subject 1> from Picture 1 sings to <Audio 1> in the environment from Picture 2, opening with Picture 2's framing.
retention_analysis:
Picture 1 → <Subject 1>: preserve the performer and gown. Picture 2 → <Subject 2>: preserve the pond and railing.
detailed_description:
[Shot 1] <Subject 1> sings beside the pond railing as the camera slowly pushes in.
overall_soundscape:
N/A
non_diegetic_music:
N/A'''


class BareReferenceTests(unittest.TestCase):
    setUp=fixtures.MaterialTests.setUp

    def packet(self):
        value=fixtures.materials.packet(self.plan,self.plan['segments'][0],self.directory)
        value['material_note']='@图1是人物，@图2是环境。'
        return value

    def test_explicit_bare_sources_are_wrapped_without_rewriting_the_shot(self):
        expected=BARE.replace('Picture 1','<Picture 1>').replace('Picture 2','<Picture 2>')
        result=expansion.validate_result(BARE,self.packet())
        self.assertEqual(result,expected)
        self.assertEqual(expansion.validate_result(result,self.packet()),result)

    def test_actual_retry_reuses_failed_bare_response_without_an_api_request(self):
        packet=self.packet()
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://test.invalid/v1'}):
            key=expansion.cache_key(packet,'vision','model','',0)
            directory=Path(packet['cache_dir']);directory.mkdir(parents=True)
            invalid=directory/(key+'.invalid.txt');invalid.write_text(BARE,encoding='utf-8')
            with patch.object(expansion,'call',side_effect=AssertionError('No paid request for a formatting repair')):
                result=expansion.expand(packet,'vision','model','')
        self.assertIn('<Picture 2>',result)
        self.assertTrue((directory/(key+'.json')).is_file())
        self.assertEqual(invalid.read_text(encoding='utf-8'),BARE)

    def test_bare_reference_numbers_are_still_range_checked(self):
        with self.assertRaisesRegex(ValueError,'不存在的图片'):
            expansion.validate_result(BARE.replace('Picture 2','Picture 3'),self.packet())

    def test_bare_audio_and_subject_tags_keep_their_numbers(self):
        value=BARE.replace('<Subject 1>','Subject 1').replace('<Subject 2>','Subject 2').replace('<Audio 1>','Audio 1')
        result=expansion.validate_result(value,self.packet())
        self.assertIn('<Subject 2>',result);self.assertIn('<Audio 1>',result)
        with self.assertRaisesRegex(ValueError,'未接入的音频'):
            expansion.validate_result(value.replace('Audio 1','Audio 2'),self.packet())

    def test_filenames_do_not_supply_a_missing_source_definition(self):
        with self.assertRaisesRegex(ValueError,'Picture 2'):
            expansion.validate_result(BARE.replace('Picture 2','Picture 2.png'),self.packet())


if __name__=='__main__':unittest.main()
