import copy
import importlib
import json
import tempfile
import unittest
from unittest.mock import patch
import test_materials as fixtures
materials, expansion, TEXT = fixtures.materials, fixtures.expansion, fixtures.MULTI_TEXT
from test_plugin import core, sample_plan


class IntentTests(unittest.TestCase):
    setUp = fixtures.MaterialTests.setUp
    updates = fixtures.MaterialTests.updates
    def test_unified_request_overrides_inherited_note_and_legacy_action(self):
        row = self.plan['segments'][0]
        row.update(shot_intent='旧动作', material_note='旧说明', shot_request='@图1人物向左走')
        packet = materials.packet(self.plan, row, self.directory)
        self.assertEqual(packet['material_note'], '@图1人物向左走')
        self.assertEqual(packet['shot_intent'], '')
        self.assertEqual(packet['contract_version'], 3)
        self.assertEqual(packet['refs'], self.plan['default_refs'])
        row['shot_request'] = ''
        cleared = materials.packet(self.plan, row, self.directory)
        self.assertEqual(cleared['material_note'], '')
        self.assertEqual(cleared['shot_intent'], '')
        self.assertNotIn('旧说明', core.brief_text(row))

    def test_unified_edit_and_clear_invalidate_only_edited_segment(self):
        row = self.plan['segments'][1]
        row['job'] = {'status': 'completed', 'video': 'old.mp4'}
        before = core.fingerprint(self.plan)
        updates = self.updates(); updates[1]['shot_request'] = '男人起身'
        core.edit_plan(self.plan, updates, self.directory)
        self.assertEqual(self.plan['changed_segments'], [1])
        self.assertNotEqual(before, core.fingerprint(self.plan))
        self.assertEqual(row['job']['video'], 'old.mp4')
        packet = materials.packet(self.plan, row, self.directory)
        key = expansion.cache_key(packet, 'vision', 'model', 'style', 0, {'base_url':'https://test.invalid/v1'})
        updates = self.updates(); updates[1]['shot_request'] = ''
        core.edit_plan(self.plan, updates, self.directory)
        self.assertEqual(self.plan['changed_segments'], [1])
        cleared = materials.packet(self.plan, row, self.directory)
        self.assertNotEqual(key, expansion.cache_key(cleared, 'vision', 'model', 'style', 0, {'base_url':'https://test.invalid/v1'}))
        self.assertEqual(row['job']['video'], 'old.mp4')

    def test_unedited_old_project_stays_on_legacy_contract(self):
        before = core.fingerprint(self.plan)
        core.edit_plan(self.plan, self.updates(), self.directory)
        self.assertEqual(self.plan['changed_segments'], [])
        self.assertEqual(core.fingerprint(self.plan), before)
        self.assertNotIn('shot_request', self.plan['segments'][0])
        self.assertEqual(materials.packet(self.plan, self.plan['segments'][0], self.directory)['contract_version'], 2)

    def test_action_and_identity_reach_api_separately(self):
        row=self.plan['segments'][0]
        row['shot_intent']='@图1走出房间，镜头跟随'
        row['brief_source']='automatic'
        packet=materials.packet(self.plan,row,self.directory)
        answer=TEXT.replace('One shot.', 'One shot follows <Audio 1>.').replace(
            '<Subject 1> from <Picture 1>.','<Subject 1> from <Picture 1>. <Audio 1> is singing.')
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://test.invalid/v1'}), patch.object(
                expansion,'call',return_value={'choices':[{'message':{'content':answer}}]}) as call:
            expansion.expand(packet,'vision','model','style')
        payload=call.call_args.args[1]
        content=json.loads(payload['messages'][1]['content'][0]['text'])
        self.assertEqual(content['shot_intent'],'<Picture 1>走出房间，镜头跟随')
        self.assertEqual(content['brief_source'],'none')
        self.assertEqual(content['brief'],'')
        self.assertIn('material_note',content)
        key=expansion.cache_key(packet,'vision','model','style',0,{'base_url':'https://test.invalid/v1'})
        packet['shot_intent']='woman turns towards the door'
        self.assertNotEqual(key,expansion.cache_key(packet,'vision','model','style',0,{'base_url':'https://test.invalid/v1'}))

    def test_new_intent_invalidates_only_changed_segment_and_retains_take(self):
        row=self.plan['segments'][1]
        row['job']={'status':'completed','video':'old.mp4'}
        updates=self.updates();updates[1]['shot_intent']='男人放下钥匙'
        core.edit_plan(self.plan,updates,self.directory)
        self.assertEqual(self.plan['changed_segments'],[1])
        self.assertEqual(row['job']['video'],'old.mp4')
        self.assertTrue(row['needs_regeneration'])

    def test_contract_rejects_missing_audio_and_environment_audio(self):
        packet=materials.packet(self.plan,self.plan['segments'][0],self.directory)
        no_audio=TEXT.replace('<Audio 1> is singing.','').replace('follows <Audio 1>.','.')
        with self.assertRaisesRegex(ValueError,'Audio 1'): expansion.validate_result(no_audio,packet)
        packet['visual_type']='environment'
        with self.assertRaisesRegex(ValueError,'误用了人声'): expansion.validate_result(TEXT+' <Audio 1>',packet)

    def test_provenance_user_edit_and_type_preview(self):
        changes=self.updates(); changes[0]['prompt']='女人走出房间。'
        changes[0].pop('brief_source', None)
        core.edit_plan(self.plan,changes,self.directory)
        self.assertEqual(self.plan['segments'][0]['brief_source'],'user')
        packet=materials.packet(self.plan,self.plan['segments'][0],self.directory)
        self.assertEqual(packet['brief_source'],'none')
        self.assertEqual(packet['brief'],'')
        self.assertEqual(self.plan['segments'][0]['prompt'],'女人走出房间。')

    def test_plain_request_excludes_conflicting_briefs_in_actual_api_payload(self):
        row = self.plan['segments'][0]
        for mode in ('vision', 'text'):
            for source in ('automatic', 'user', 'legacy'):
                with self.subTest(mode=mode, source=source):
                    row.update(shot_request='女人起身向左走，镜头跟随',
                               prompt='CONFLICT: woman stays seated, static camera', brief_source=source)
                    original = copy.deepcopy(row)
                    packet = materials.packet(self.plan, row, self.directory)
                    self.assertEqual(packet['brief'], '')
                    self.assertEqual(row, original)
                    with patch.object(expansion, 'public_settings', return_value={'base_url':'https://test.invalid/v1'}), patch.object(
                            expansion, 'call', return_value={'choices':[{'message':{'content':TEXT}}]}) as call:
                        expansion.expand(packet, mode, 'model', 'style', revision=f'{mode}-{source}')
                    payload = call.call_args.args[1]
                    content = json.loads(payload['messages'][1]['content'][0]['text'])
                    self.assertEqual(content['material_note'], row['shot_request'])
                    self.assertEqual(content['brief'], '')
                    self.assertEqual(content['brief_source'], 'none')
                    self.assertNotIn('CONFLICT', json.dumps(payload))

    def test_empty_request_uses_fresh_type_specific_automatic_brief(self):
        row = self.plan['segments'][0]
        row.update(shot_request='   ', shot_intent='旧动作', prompt='HIDDEN old direction', brief_source='user')
        for kind in ('performance', 'atmosphere', 'environment'):
            row['visual_type'] = kind
            packet = materials.packet(self.plan, row, self.directory)
            self.assertEqual(packet['material_note'], '')
            self.assertEqual(packet['shot_intent'], '')
            self.assertEqual(packet['brief_source'], 'automatic')
            self.assertTrue(packet['brief'])
            self.assertNotIn('HIDDEN', packet['brief'])
            self.assertNotIn('旧动作', packet['brief'])
            self.assertEqual(packet['visual_type'], kind)
            if kind == 'environment': self.assertIn('空镜环境', packet['brief'])
        self.assertEqual(row['prompt'], 'HIDDEN old direction')

    def test_hidden_brief_cannot_change_request_cache_but_request_edit_and_clear_do(self):
        row = self.plan['segments'][0]
        row.update(shot_request='女人向左走', prompt='固定镜头', brief_source='user')
        def key():
            return expansion.cache_key(materials.packet(self.plan, row, self.directory),
                                       'vision', 'model', '', 0, {'base_url':'https://test.invalid/v1'})
        original = key()
        row['prompt'] = '向右摇镜'
        self.assertEqual(key(), original)
        row['shot_request'] = '女人坐下'
        self.assertNotEqual(key(), original)
        edited = key()
        row['shot_request'] = ''
        self.assertNotEqual(key(), edited)

    def test_manual_passthrough_never_returns_empty_for_nonempty_request(self):
        row = self.plan['segments'][0]
        row['shot_request'] = '女人起身向左走'
        packet = materials.packet(self.plan, row, self.directory)
        with patch.object(expansion, 'public_settings', return_value={'base_url':'https://test.invalid/v1'}), patch.object(expansion, 'call') as call:
            self.assertEqual(expansion.expand(packet, 'manual', 'model', ''), row['shot_request'])
            call.assert_not_called()

    def test_single_picture_source_is_unambiguous_but_multi_picture_is_not(self):
        packet=materials.packet(self.plan,self.plan['segments'][0],self.directory)
        missing=fixtures.TEXT.replace(' from <Picture 1>', '')
        with self.assertRaisesRegex(ValueError,'多图'): expansion.validate_result(missing,packet)
        packet['paths']=packet['paths'][:1]
        fixed=expansion.validate_result(missing,packet)
        self.assertIn('use <Picture 1> as their reference',fixed)
        self.assertIn('[Shot 1] Move.',fixed)


class ProgressTests(unittest.TestCase):
    def test_progress_does_not_affect_approval_and_survives_read(self):
        progress=importlib.import_module('h3lv_test.progress')
        with tempfile.TemporaryDirectory() as root:
            plan=sample_plan();core.write_plan(root,plan)
            key=core.fingerprint(plan)
            progress.report(root,plan['id'],'expansion',1)
            saved=core.read_plan(root,plan['id'])
            self.assertEqual(saved['progress']['index'],1)
            self.assertEqual(core.fingerprint(saved),key)
            progress.report(root,plan['id'],'expansion',1)
            self.assertEqual(core.read_plan(root,plan['id'])['progress'],saved['progress'])
