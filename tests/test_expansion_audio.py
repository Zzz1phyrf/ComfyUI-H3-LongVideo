"""Workflow-owned audio references must not depend on optional model wording."""
import copy
import itertools
import json
from pathlib import Path
import unittest
import shutil
import sys
import types
from unittest.mock import patch

import test_materials as fixtures

expansion, materials, controller = fixtures.expansion, fixtures.materials, fixtures.controller


class AudioReferenceTests(unittest.TestCase):
    setUp = fixtures.MaterialTests.setUp

    def packet(self):
        return materials.packet(self.plan, self.plan['segments'][0], self.directory)

    def test_missing_audio_definition_or_summary_is_completed_from_workflow_contract(self):
        for definition, summary, retention, detail in itertools.product((False, True), repeat=4):
            with self.subTest(definition=definition, summary=summary, retention=retention, detail=detail):
                prompt = fixtures.MULTI_TEXT.replace('<Audio 1> is singing.', '').replace('follows <Audio 1>', 'shows the performer')
                if definition: prompt = prompt.replace('summary:', '<Audio 1> is the supplied vocal reference.\nsummary:')
                if summary: prompt = prompt.replace('retention_analysis:', 'The performer follows <Audio 1>.\nretention_analysis:')
                if retention: prompt = prompt.replace('Keep scene.', 'Keep scene. <Audio 1> supplies timing.')
                if detail: prompt = prompt.replace('[Shot 1] Move.', '[Shot 1] Move to <Audio 1>.')
                result = expansion.validate_result(prompt, self.packet())
                definitions, rest = result.split('summary:', 1)
                self.assertIn('<Audio 1>', definitions)
                self.assertIn('<Audio 1>', rest.split('retention_analysis:', 1)[0])
                expected = '[Shot 1] Move to <Audio 1>.' if detail else '[Shot 1] Move.'
                self.assertIn(expected, result)
                self.assertEqual(result.count('[Shot 1]'), 1)
                self.assertEqual(expansion.validate_result(result, self.packet()), result)

    def test_suspected_instrumental_tag_does_not_override_selected_performance_type(self):
        packet = self.packet()
        packet.update(audio_role='instrumental', audio_section='interlude')
        prompt = fixtures.MULTI_TEXT.replace('<Audio 1> is singing.', '').replace('follows <Audio 1>', 'shows the performer')
        self.assertIn('<Audio 1>', expansion.validate_result(prompt, packet))

    def test_unprovided_audio_two_is_rejected_without_renumbering(self):
        with self.assertRaisesRegex(ValueError, 'Audio 2'):
            expansion.validate_result(fixtures.MULTI_TEXT+'\nThe voice comes from <Audio 2>.', self.packet())

    def test_nonperformance_is_never_given_an_audio_reference(self):
        for kind in ('environment', 'atmosphere'):
            packet = self.packet(); packet['visual_type'] = kind
            prompt = fixtures.MULTI_TEXT.replace('<Audio 1> is singing.', '').replace('follows <Audio 1>', 'shows the scene')
            self.assertNotIn('<Audio', expansion.validate_result(prompt, packet))
            with self.assertRaisesRegex(ValueError, '误用了人声'):
                expansion.validate_result(fixtures.MULTI_TEXT, packet)

    def test_existing_invalid_response_is_repaired_without_another_api_request(self):
        packet = self.packet()
        answer = fixtures.MULTI_TEXT.replace('<Audio 1> is singing.', '').replace('follows <Audio 1>', 'shows the performer')
        with patch.object(expansion, 'public_settings', return_value={'base_url':'https://test.invalid/v1'}), \
             patch.object(expansion, 'call', side_effect=AssertionError('Do not buy the same expansion again')):
            key = expansion.cache_key(packet, 'vision', 'model', '', 0)
            cache = Path(packet['cache_dir']);cache.mkdir(parents=True)
            invalid = cache/(key+'.invalid.txt');invalid.write_text(answer, encoding='utf-8')
            result = expansion.PromptExpand().run(packet, 'vision', 'model', '')['result'][0]
            self.assertIn('<Audio 1>', result)
            self.assertEqual(json.loads((cache/(key+'.json')).read_text(encoding='utf-8'))['text'], result)
            self.assertEqual(invalid.read_text(encoding='utf-8'), answer)
            self.assertEqual(expansion.expand(packet, 'vision', 'model', ''), result)

    def test_actual_performance_graph_uses_current_segment_vocals_as_audio_one(self):
        graph = {'1':{'class_type':'H3LVUnified','inputs':{}},
                 '2':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{
                     'ref_audios.ref_audio_1':['old',0], 'prompt':'unchanged'}}}
        controller.apply_segment_references(graph, self.plan, self.plan['segments'][0], self.directory)
        inputs = graph['2']['inputs']
        self.assertEqual(inputs['ref_audios.ref_audio_0'], ['1',1])
        self.assertNotIn('ref_audios.ref_audio_1', inputs)
        self.assertEqual(inputs['prompt'], 'unchanged')


class ResumeAfterAudioFailureTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.MaterialTests.setUp

    async def test_retry_eighth_segment_preserves_first_seven_and_reuses_invalid_response(self):
        core = fixtures.core
        plan = self.plan
        plan.update(samples=18000, duration=180,
                    segments=[{'start_sample':i*1000,'end_sample':(i+1)*1000,'energy_db':-20,
                               'text':'vocal','reference_source':'default','visual_type':'performance'}
                              for i in range(18)])
        core.decorate(plan)
        for i in range(7):
            plan['segments'][i]['job'] = {'status':'completed','video':f'accepted-{i}.mp4','prompt_id':f'old-{i}'}
        previous = copy.deepcopy(plan['segments'][:7])
        plan.update(approved=True, approved_fingerprint=core.fingerprint(plan))
        root = self.directory/'projects'
        core.write_plan(root, plan)
        directory = core.project_path(root, plan['id'])
        shutil.copytree(self.directory/'refs', directory/'refs')
        snapshot = {'loader_id':'1','video_id':'7','output_contract_version':controller.OUTPUT_CONTRACT_VERSION,
                    'prompt':{'1':{'class_type':'H3LVUnified','inputs':{}},
                              '2':{'class_type':'MiniMaxH3ReferenceToVideo','inputs':{}},
                              '7':{'class_type':'VHS_VideoCombine','inputs':{}}}}
        core.state_file(directory, 'queue_snapshot.json').write_text(json.dumps(snapshot), encoding='utf-8')
        packet = materials.packet(plan, plan['segments'][7], directory)
        profile = {'base_url':'https://test.invalid/v1'}
        with patch.object(expansion, 'public_settings', return_value=profile):
            key = expansion.cache_key(packet, 'vision', 'model', '', 0)
        cache = Path(packet['cache_dir']);cache.mkdir(parents=True)
        (cache/(key+'.invalid.txt')).write_text(fixtures.MULTI_TEXT.replace('<Audio 1> is singing.', ''), encoding='utf-8')
        histories, submitted = {}, []
        class Queue:
            def put(queue, item):
                index = item[2]['1']['inputs']['segment_index']
                submitted.append(index)
                assert item[2]['2']['inputs']['ref_audios.ref_audio_0'] == ['1',1]
                prompt = expansion.PromptExpand().run(materials.packet(plan, plan['segments'][index], directory),
                                                      'vision','model','')['result'][0]
                assert '<Audio 1>' in prompt.split('summary:')[0]
                video = directory/f'{index}.mp4';video.write_bytes(b'video fixture')
                histories[item[1]] = {'status':{'status_str':'success'},'outputs':{'7':{'gifs':[
                    {'type':'output','subfolder':plan['id'],'filename':video.name}]}}}
                current = core.read_plan(root, plan['id']);current['pause_requested'] = True
                core.write_plan(root, current)
            def get_history(queue, prompt_id):return {prompt_id:histories[prompt_id]}
            def get_current_queue(queue):return [], []
        async def validate(*args):return True,None,['7'],{}
        server = types.SimpleNamespace(number=0,prompt_queue=Queue())
        with patch.dict(sys.modules, {'execution':types.SimpleNamespace(validate_prompt=validate),
                                     'folder_paths':types.SimpleNamespace(get_output_directory=lambda:str(root))}), \
             patch.object(expansion, 'public_settings', return_value=profile), \
             patch.object(expansion, 'call', side_effect=AssertionError('No paid expansion retry')):
            await controller.execute_project(root, plan['id'], server)
        saved = core.read_plan(root, plan['id'])
        self.assertEqual(submitted, [7])
        self.assertEqual(saved['segments'][:7], previous)
        self.assertEqual(saved['segments'][7]['job']['status'], 'completed', saved.get('error'))
        self.assertEqual(saved['run_status'], 'paused')


if __name__ == '__main__':
    unittest.main()
