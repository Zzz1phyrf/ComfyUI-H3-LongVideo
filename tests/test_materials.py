import copy
import importlib
import io
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from test_plugin import core, controller, nodes, sample_plan

materials = importlib.import_module('h3lv_test.materials')
expansion = importlib.import_module('h3lv_test.expansion')
TEXT = 'subject_definitions:\n<Subject 1> from <Picture 1>.\nsummary:\nOne shot.\nretention_analysis:\nKeep scene.\ndetailed_description:\n[Shot 1] Move.\noverall_soundscape:\nN/A\nnon_diegetic_music:\nN/A'

class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.names = []
        for i, size in enumerate([(16,8),(8,16),(12,12)]):
            b = io.BytesIO(); Image.new('RGB',size,(i*70,50,80)).save(b,format='PNG')
            self.names.append(core.store_reference(self.directory, 0, f'{i}.png', b.getvalue()))
        self.plan = sample_plan()
        self.plan.update(materials_version=1, default_refs=self.names[:2], default_material_note='图1人物 图2背景')
        self.plan['segments'][1].update(reference_source='custom', refs=self.names[2:], material_note='图1空镜', visual_type='environment')
        core.decorate(self.plan)

    def updates(self):
        return [dict(row) for row in self.plan['segments']]

    def test_inheritance_and_custom_invalidation(self):
        core.edit_plan(self.plan, self.updates(), self.directory, materials={'refs':self.names[:1], 'note':'图1人物及背景'})
        self.assertEqual(self.plan['changed_segments'], [0,2])
        self.assertEqual(materials.effective(self.plan,self.plan['segments'][1])['refs'],self.names[2:])
        self.assertFalse(self.plan['approved'])

    def test_custom_empty_is_not_inheritance(self):
        row = self.plan['segments'][0]; row.update(reference_source='custom', refs=[])
        with self.assertRaisesRegex(ValueError,'没有有效参考图'):
            materials.effective(self.plan,row,self.directory,require=True)

    def test_generation_preflight_reports_all_missing_segments(self):
        plan = copy.deepcopy(self.plan)
        plan['default_refs'] = []
        with self.assertRaisesRegex(ValueError, '第 1、3 段没有有效参考图'):
            controller.validate_generation_materials(plan, self.directory)
        controller.validate_generation_materials(plan, self.directory, [1])

    def test_missing_images_block_generation_without_changing_project_state(self):
        root = self.directory/'projects'
        plan = copy.deepcopy(self.plan)
        plan['default_refs'] = []
        for row in plan['segments']:
            row.update(reference_source='default', refs=[])
        plan['approved'] = True
        plan['approved_fingerprint'] = core.fingerprint(plan)
        core.write_plan(root, plan)
        with self.assertRaisesRegex(ValueError, '第 1、2、3 段没有有效参考图'):
            controller.start(root, plan['id'], {}, object())
        saved = core.read_plan(root, plan['id'])
        self.assertEqual(saved['run_status'], 'draft')
        self.assertFalse(core.state_file(
            core.project_path(root, plan['id']), 'queue_snapshot.json').exists())

    def test_packet_allows_empty_material_note(self):
        self.plan['default_material_note'] = ''
        packet = materials.packet(self.plan, self.plan['segments'][0], self.directory)
        self.assertEqual(packet['material_note'], '')
        self.assertEqual(len(packet['paths']), 2)

    def test_images_remain_ordered_and_individual_sizes(self):
        packet = materials.packet(self.plan,self.plan['segments'][0],self.directory)
        images = materials.images(packet)
        self.assertEqual(len(images),6)
        self.assertEqual(tuple(images[0].shape),(1,8,16,3))
        self.assertEqual(tuple(images[1].shape),(1,16,8,3))
        self.assertTrue(all(value is None for value in images[2:]))

    def test_controller_removes_unused_slots_and_non_lipsync_audio(self):
        prompt = {'1':{'class_type':'H3LVUnified','inputs':{}},'2':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{
            'ref_images.ref_image_0':['x',0],'ref_images.ref_image_5':['y',0],'ref_audios.ref_audio_0':['1',1],'prompt':'keep'}}}
        controller.apply_segment_references(prompt,self.plan,self.plan['segments'][1],self.directory)
        self.assertEqual(prompt['2']['inputs'],{'ref_images.ref_image_0':['1',5],'prompt':'keep'})
        core.validate_segment_brief(self.plan['segments'][1]['prompt'])
        atmosphere = self.plan['segments'][0]
        atmosphere['visual_type'] = 'atmosphere'
        prompt['2']['inputs']['ref_audios.ref_audio_0'] = ['1',1]
        controller.apply_segment_references(prompt,self.plan,atmosphere,self.directory)
        self.assertNotIn('ref_audios.ref_audio_0',prompt['2']['inputs'])

    def test_environment_silences_generation_voice_but_keeps_final_audio(self):
        import numpy as np
        root = self.directory/'projects'; directory = core.project_path(root,self.plan['id'])
        self.plan['approved'] = True
        self.plan['segments'][1]['final_prompt'] = 'user text exactly'
        core.write_plan(root,self.plan)
        fake = {'paths':[], 'brief':'test', 'material_note':'图1场景'}
        with patch.object(nodes,'data_root',return_value=root), patch('soundfile.read',return_value=(np.ones((1000,1),dtype=np.float32),100)), patch.object(materials,'packet',return_value=fake):
            result = nodes.LoadSegment().load(self.plan['id'],1)
        self.assertGreater(float(result[0]['waveform'].abs().sum()),0)
        self.assertEqual(float(result[1]['waveform'].abs().sum()),0)
        self.assertEqual(result[-2], 'user text exactly')
        self.assertEqual(result[-1], 24.0)

    def test_vision_cache_and_context_invalidation(self):
        packet = materials.packet(self.plan,self.plan['segments'][0],self.directory)
        packet['material_note'] = '@图1是人物，@图2是环境'
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://example.test/v1'}), patch.object(expansion,'call',return_value={'choices':[{'message':{'content':TEXT}}]}) as call:
            self.assertEqual(expansion.expand(packet,'vision','m','r'),TEXT)
            expansion.expand(packet,'vision','m','r')
            self.assertEqual(call.call_count,1)
            content = call.call_args.args[1]['messages'][1]['content']
            self.assertEqual([c['type'] for c in content],['text','image_url','image_url'])
            context = __import__('json').loads(content[0]['text'])
            self.assertEqual(context['material_note'], '<Picture 1>是人物，<Picture 2>是环境')
            self.assertEqual(context['generation_frames'], self.plan['segments'][0]['generation_frames'])
            self.assertEqual(context['audio_role'], 'vocal')
            self.assertEqual(context['visual_type'], 'performance')
            system = call.call_args.args[1]['messages'][0]['content']
            self.assertIn('atmosphere means a visible performer who remains closed-mouth', system)
            self.assertIn('environment means only the declared environment is visible', system)
            self.assertIn('generation_seconds', system)
            self.assertIn('do not quote, transcribe, translate, paraphrase or describe its wording', system)
            other = copy.deepcopy(packet); other['material_note'] = 'different roles'
            expansion.expand(other,'vision','m','r')
            self.assertEqual(call.call_count,2)
            other['hashes'].reverse()
            expansion.expand(other,'vision','m','r')
            self.assertEqual(call.call_count,3)

    def test_out_of_range_material_mention_is_rejected_before_api_call(self):
        packet = materials.packet(self.plan,self.plan['segments'][0],self.directory)
        packet['material_note'] = '@图3是环境'
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://example.test/v1'}), \
             patch.object(expansion,'call') as call:
            with self.assertRaisesRegex(ValueError, '不存在的 @图3'):
                expansion.expand(packet,'text','m','r')
            call.assert_not_called()

    def test_failed_request_not_cached_or_retried(self):
        packet = materials.packet(self.plan,self.plan['segments'][0],self.directory)
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://example.test/v1'}), patch.object(expansion,'call',side_effect=ValueError('API unavailable')) as call:
            with self.assertRaises(ValueError): expansion.expand(packet,'vision','m','r')
            self.assertEqual(call.call_count,1)
        self.assertFalse(Path(packet['cache_dir']).exists())

    def test_bad_picture_reference_rejected(self):
        with self.assertRaisesRegex(ValueError,'不存在的图片'):
            expansion.validate_prompt(TEXT.replace('<Picture 1>','<Picture 9>'),2)

    def test_missing_single_shot_label_is_repaired(self):
        missing_label = TEXT.replace('[Shot 1] ', '')
        result = expansion.validate_prompt(missing_label, 2)
        self.assertEqual(result.count('[Shot 1]'), 1)
        self.assertIn('detailed_description:\n[Shot 1]\nMove.', result)

    def test_summary_shot_label_is_removed_when_detail_has_single_shot(self):
        duplicated_in_summary = TEXT.replace('summary:\nOne shot.', 'summary:\n[Shot 1] One shot.')
        result = expansion.validate_prompt(duplicated_in_summary, 2)
        self.assertEqual(result.count('[Shot 1]'), 1)
        self.assertIn('summary:\nOne shot.', result)
        self.assertIn('detailed_description:\n[Shot 1] Move.', result)

    def test_single_shot_label_variations_are_canonicalized(self):
        cases = {
            'bold_compact_uppercase_summary': TEXT.replace(
                'summary:\nOne shot.', 'summary:\n**[SHOT1]** One shot.'),
            'label_after_detail_prose': TEXT.replace(
                'detailed_description:\n[Shot 1] Move.',
                'detailed_description:\nContinuous take. [shot 1] Move.'),
            'label_only_outside_detail': TEXT.replace(
                'summary:\nOne shot.', 'summary:\n[ Shot 1 ] One shot.').replace('[Shot 1] ', ''),
        }
        for name, prompt in cases.items():
            with self.subTest(name=name):
                result = expansion.validate_prompt(prompt, 2)
                self.assertEqual(result.count('[Shot 1]'), 1)
                self.assertIn('detailed_description:\n[Shot 1]', result)
                self.assertEqual(
                    re.findall(r'(?i)\[\s*shot\s*\d+\s*\]', result),
                    ['[Shot 1]'],
                )

    def test_redundant_single_shot_label_is_removed_from_every_other_section(self):
        for heading in expansion.HEADINGS:
            if heading == 'detailed_description':
                continue
            with self.subTest(heading=heading):
                prompt = TEXT.replace(f'{heading}:\n', f'{heading}:\n**[SHOT #1]**: ', 1)
                result = expansion.validate_prompt(prompt, 2)
                self.assertEqual(result.count('[Shot 1]'), 1)
                self.assertIn('detailed_description:\n[Shot 1]', result)
                self.assertNotIn('[SHOT #1]', result)

    def test_duplicate_single_shot_labels_are_rejected(self):
        duplicate = TEXT.replace('[Shot 1] Move.', '[Shot 1] Move.\n[Shot 1] Continue.')
        with self.assertRaisesRegex(ValueError, '\\[Shot 1\\]'):
            expansion.validate_prompt(duplicate, 2)

    def test_noncanonical_multi_shot_and_duplicate_detail_are_rejected(self):
        prompts = {
            'uppercase_second_shot': TEXT.replace('[Shot 1] Move.', '[SHOT 2] Move.'),
            'compact_duplicate_detail': TEXT.replace(
                '[Shot 1] Move.', '[Shot 1] Move.\n**[SHOT1]** Continue.'),
        }
        for name, prompt in prompts.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, '\\[Shot 1\\]'):
                expansion.validate_prompt(prompt, 2)

    def test_malformed_bracketed_shot_label_is_rejected(self):
        malformed = TEXT.replace('[Shot 1] Move.', '[Shot one] Move.')
        with self.assertRaisesRegex(ValueError, '\\[Shot 1\\]'):
            expansion.validate_prompt(malformed, 2)

    def test_cache_source_reports_exact_match_only(self):
        packet = materials.packet(self.plan,self.plan['segments'][0],self.directory)
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://example.test/v1'}):
            key = expansion.cache_key(packet,'vision','m','r',0)
            self.assertEqual(expansion.cache_source(packet,key,'vision'),'api')
            cache = Path(packet['cache_dir']); cache.mkdir(parents=True)
            (cache/(key+'.json')).write_text('{"text":"cached"}',encoding='utf-8')
            self.assertEqual(expansion.cache_source(packet,key,'vision'),'cache')
            packet.update(expanded_key=key, expanded_prompt=TEXT)
            self.assertEqual(expansion.cache_source(packet,key,'vision'),'saved')

    def test_secret_not_returned_and_host_change_clears_old_key(self):
        with patch.object(expansion,'settings_path',return_value=self.directory/'profile.json'):
            result = expansion.save_settings('https://one.test/v1','test-private-key')
            self.assertNotIn('test-private-key',str(result))
            result = expansion.save_settings('https://two.test/v1','')
            self.assertFalse(result['configured'])

    def test_invalid_image_rejected(self):
        with self.assertRaisesRegex(ValueError,'有效图片'):
            core.store_reference(self.directory,0,'fake.png',b'not an image')

    def test_environment_brief_survives_cut_changes(self):
        updates = self.updates(); updates[0]['end'] = 9
        core.edit_plan(self.plan, updates, self.directory)
        row = self.plan['segments'][1]
        self.assertIn('画面任务：空镜环境', row['prompt'])
        core.validate_segment_brief(row['prompt'])

    def test_atmosphere_silences_generation_voice_and_expansion_context(self):
        import numpy as np
        row = self.plan['segments'][0]
        row['visual_type'] = 'atmosphere'
        root = self.directory/'projects'; directory = core.project_path(root,self.plan['id'])
        self.plan['approved'] = True
        core.write_plan(root,self.plan)
        fake = {'paths':[], 'brief':'test', 'material_note':'图1人物'}
        with patch.object(nodes,'data_root',return_value=root), \
             patch('soundfile.read',return_value=(np.ones((1000,1),dtype=np.float32),100)), \
             patch.object(materials,'packet',return_value=fake):
            result = nodes.LoadSegment().load(self.plan['id'],0)
        self.assertGreater(float(result[0]['waveform'].abs().sum()),0)
        self.assertEqual(float(result[1]['waveform'].abs().sum()),0)

    def test_stale_prompt_override_is_not_reused(self):
        packet = materials.packet(self.plan,self.plan['segments'][0],self.directory)
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://example.test/v1'}), patch.object(expansion,'call',return_value={'choices':[{'message':{'content':TEXT}}]}) as call:
            packet['expanded_key'] = expansion.cache_key(packet,'vision','m','r',0)
            packet['expanded_prompt'] = TEXT.replace('Keep scene.','User edit.')
            self.assertIn('User edit.',expansion.expand(packet,'vision','m','r'))
            call.assert_not_called()
            packet['brief'] += ' changed'
            self.assertNotIn('User edit.',expansion.expand(packet,'vision','m','r'))
            call.assert_called_once()

    def test_markdown_labels_normalize_without_filling_missing_sections(self):
        wrapped = TEXT
        for name in expansion.HEADINGS:
            wrapped = wrapped.replace(name+':', '## **'+name+'**:')
        self.assertEqual(expansion.validate_prompt('```text\n'+wrapped+'\n```',1),TEXT)
        with self.assertRaises(ValueError): expansion.validate_prompt('summary: only summary',1)

    def test_qwen_non_thinking_and_empty_answer_rejection(self):
        packet = materials.packet(self.plan,self.plan['segments'][0],self.directory)
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://example.test/v1'}), patch.object(expansion,'call',return_value={'choices':[{'message':{'content':''},'finish_reason':'stop'}]}) as call:
            with self.assertRaisesRegex(ValueError,'没有返回正文'):
                expansion.expand(packet,'vision','qwen/qwen3.8-flash','r')
            self.assertFalse(call.call_args.args[1]['enable_thinking'])
            self.assertEqual(call.call_args.args[1]['reasoning'],{'effort':'none'})

    def test_pending_expansion_settings_refresh_preserves_other_nodes(self):
        snapshot = {'prompt':{'1':{'class_type':'H3LVPromptExpand','inputs':{'material':['5',5], 'model':'old','rule':'old','mode':'vision','revision':0}}, '2':{'class_type':'Sampler','inputs':{'steps':6}}}}
        current = copy.deepcopy(snapshot['prompt']);current['1']['inputs'].update(model='new',rule='new rule');current['2']['inputs']['steps']=99
        controller.refresh_expansion_settings(snapshot,current)
        self.assertEqual(snapshot['prompt']['1']['inputs']['model'],'new')
        self.assertEqual(snapshot['prompt']['2']['inputs']['steps'],6)
        current['1']['inputs']['material']=['6',5]
        with self.assertRaises(ValueError):controller.refresh_expansion_settings(snapshot,current)
