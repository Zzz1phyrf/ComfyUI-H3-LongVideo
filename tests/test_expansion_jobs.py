"""Batch scheduler tests: all paid providers are replaced by local fixtures."""
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import test_materials as fixtures

expansion = fixtures.expansion
jobs = importlib.import_module('h3lv_test.expansion_jobs')
cli = importlib.import_module('h3lv_test.codex_cli')
PROFILE = {'base_url': 'https://fixture.test/v1', 'api_key': 'private-batch-fixture',
           'timeout_seconds': 300, 'extra_body': {'temperature': .4}}
SETTINGS = dict(mode='text', model='fixture-model', rule='fixture rule', revision=0, provider='api')
ANSWER = {'choices': [{'message': {'content': fixtures.TEXT}}]}


class ExpansionJobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.events = []
        self.addCleanup(self.drain)

    def drain(self):
        for event in self.events:
            event.set()
        deadline = time.monotonic()+5
        while jobs._RUNNING and time.monotonic() < deadline:
            for runtime in list(jobs._RUNNING.values()):
                runtime['cancel'].set()
            time.sleep(.01)
        self.assertFalse(jobs._RUNNING, 'Batch workers did not stop')

    def event(self):
        event = threading.Event()
        self.events.append(event)
        return event

    def packet(self, number, **updates):
        return dict(paths=['local-fixture.png'], hashes=[str(number)], material_note='@图1是人物',
            visual_type='atmosphere', mode='singing', brief=f'Approved brief {number}', duration=10,
            generation_frames=242, generation_seconds=242/24, audio_role='instrumental',
            audio_section='interlude', audio_role_reason='fixture',
            cache_dir=str(self.directory/'cache'), **updates)

    def items(self, count):
        return [dict(index=index, material=self.packet(index), protected=False) for index in range(count)]

    def start(self, items=None, project_id='project-a', directory=None, **kwargs):
        return jobs.start(directory or self.directory/project_id, project_id, 7,
                          self.items(4) if items is None else items, kwargs.pop('settings', SETTINGS),
                          request_profile=kwargs.pop('request_profile', PROFILE), **kwargs)

    def wait(self, job, directory=None):
        deadline = time.monotonic()+5
        while jobs.active(job['project_id']) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(jobs.active(job['project_id']), 'Batch did not finish')
        return jobs.get(directory or self.directory/job['project_id'], job['job_id'])

    def assert_four_slots_available(self):
        release, four_started = self.event(), self.event()
        lock, calls = threading.Lock(), 0
        def provider(*args, **kwargs):
            nonlocal calls
            with lock:
                calls += 1
                if calls == 4:
                    four_started.set()
            release.wait(3)
            return ANSWER
        identity = str(uuid.uuid4())
        items = [dict(index=index, material=self.packet(identity+str(index))) for index in range(4)]
        with patch.object(expansion, 'call', side_effect=provider):
            job = self.start(items, project_id=identity, concurrency=4)
            try:
                self.assertTrue(four_started.wait(2), 'A previous request leaked a shared provider slot')
            finally:
                release.set()
            self.assertEqual(self.wait(job)['counts'], {'succeeded': 4})

    def test_default_batch_still_uses_two_workers(self):
        lock, release, two_started = threading.Lock(), self.event(), self.event()
        running = maximum = calls = 0
        def provider(*args, **kwargs):
            nonlocal running, maximum, calls
            with lock:
                running += 1
                calls += 1
                maximum = max(maximum, running)
                if running == 2:
                    two_started.set()
            release.wait(3)
            with lock:
                running -= 1
            return ANSWER
        with patch.object(expansion, 'call', side_effect=provider):
            job = self.start()
            try:
                self.assertTrue(two_started.wait(2))
                self.assertEqual(job['concurrency'], 2)
                time.sleep(.05)
                self.assertEqual(calls, 2)
            finally:
                release.set()
            final = self.wait(job)
        self.assertEqual(maximum, 2)
        self.assertEqual(calls, 4)
        self.assertEqual(final['counts'], {'succeeded': 4})
        self.assertEqual(final['state'], 'succeeded')

    def test_selected_three_and_four_workers_reach_the_requested_concurrency(self):
        for concurrency in (3, 4):
            with self.subTest(concurrency=concurrency):
                lock, release, ready = threading.Lock(), self.event(), self.event()
                running = maximum = calls = 0
                def provider(*args, **kwargs):
                    nonlocal running, maximum, calls
                    with lock:
                        running += 1
                        calls += 1
                        maximum = max(maximum, running)
                        if running == concurrency:
                            ready.set()
                    release.wait(3)
                    with lock:
                        running -= 1
                    return ANSWER
                items = [dict(index=index, material=self.packet(f'{concurrency}-{index}')) for index in range(6)]
                with patch.object(expansion, 'call', side_effect=provider):
                    job = self.start(items, project_id=f'concurrency-{concurrency}', concurrency=concurrency)
                    try:
                        self.assertTrue(ready.wait(2), 'Requested batch concurrency was not reached')
                        time.sleep(.05)
                        self.assertEqual(calls, concurrency)
                    finally:
                        release.set()
                    final = self.wait(job)
                self.assertEqual(maximum, concurrency)
                self.assertEqual(calls, 6)
                self.assertEqual(final['counts'], {'succeeded': 6})

    def test_same_key_waiter_never_occupies_a_second_slot_and_is_released(self):
        entered, unrelated, release = self.event(), self.event(), self.event()
        calls = []
        lock = threading.Lock()
        def provider(path, payload, **kwargs):
            brief = json.loads(payload['messages'][1]['content'][0]['text'])['brief']
            with lock:
                calls.append(brief)
                if 'unrelated' not in brief:
                    entered.set()
                elif sum('unrelated' in item for item in calls) == 3:
                    unrelated.set()
            release.wait(3)
            return ANSWER
        first, second = self.packet(0), self.packet(0)
        second['cache_dir'] = str(self.directory/'other-cache')
        with patch.object(expansion, 'call', side_effect=provider):
            job = self.start([dict(index=0, material=first)])
            self.assertTrue(entered.wait(2))
            other = self.start([dict(index=0, material=second)], project_id='same-key-project')
            key = expansion.cache_key(first, profile=PROFILE, **SETTINGS)
            deadline = time.monotonic()+2
            while time.monotonic() < deadline:
                with expansion._KEYS_LOCK:
                    waiting = expansion._KEY_LOCKS.get(key, {}).get('users', 0) == 2
                if waiting:
                    break
                time.sleep(.01)
            self.assertTrue(waiting, 'Second project did not reach the same-key wait')
            with ThreadPoolExecutor(3) as executor:
                pending = [executor.submit(expansion.expand, self.packet(f'unrelated-{index}'), 'text', 'm', 'r',
                                           request_profile=PROFILE) for index in range(3)]
                try:
                    self.assertTrue(unrelated.wait(2), 'Same-key waiter consumed a provider slot')
                finally:
                    release.set()
                for request in pending:
                    request.result(timeout=3)
            final, other_final = self.wait(job), self.wait(other)
        self.assertEqual(len(calls), 4)
        self.assertEqual({final['items'][0]['status'], other_final['items'][0]['status']}, {'succeeded', 'cached'})
        self.assertTrue(list((self.directory/'other-cache').glob('*.json')))
        self.assertFalse(expansion._KEY_LOCKS)

    def test_api_cli_single_and_batches_share_four_slots_across_projects(self):
        lock = threading.Lock()
        release, three_started, four_started = self.event(), self.event(), self.event()
        running = maximum = api_calls = cli_calls = 0
        def enter(kind):
            nonlocal running, maximum, api_calls, cli_calls
            with lock:
                running += 1
                maximum = max(maximum, running)
                if kind == 'api':
                    api_calls += 1
                else:
                    cli_calls += 1
                if running == 3:
                    three_started.set()
                if running == 4:
                    four_started.set()
            release.wait(3)
            with lock:
                running -= 1
        def api(*args, **kwargs):
            enter('api')
            return ANSWER
        def codex(*args, **kwargs):
            enter('cli')
            return fixtures.TEXT
        with patch.object(expansion, '_codex_cancelled', side_effect=AssertionError('Global interrupt touched')), \
             patch.object(expansion, 'call', side_effect=api), patch.object(cli, 'generate', side_effect=codex):
            api_job = self.start()
            cli_job = self.start(project_id='cli-project', settings={**SETTINGS, 'provider': 'codex_cli'},
                                 request_profile={'cli_identity': {'config_hash': 'fixture'}}, concurrency=1)
            with ThreadPoolExecutor(2) as executor:
                try:
                    self.assertTrue(three_started.wait(2))
                    cli_single = executor.submit(expansion.expand, self.packet('cli-single'), 'text', 'm', 'r',
                        provider='codex_cli', request_profile={'cli_identity': {'config_hash': 'fixture'}},
                        cancelled=lambda: False)
                    self.assertTrue(four_started.wait(2))
                    api_single = executor.submit(expansion.expand, self.packet('api-single'), 'text', 'm', 'r',
                        request_profile=PROFILE)
                    key = expansion.cache_key(self.packet('api-single'), 'text', 'm', 'r', 0, profile=PROFILE)
                    deadline = time.monotonic()+2
                    while time.monotonic() < deadline:
                        with expansion._KEYS_LOCK:
                            waiting = key in expansion._KEY_LOCKS
                        if waiting:
                            break
                        time.sleep(.01)
                    self.assertTrue(waiting, 'Extra single request did not reach the shared quota')
                    time.sleep(.05)
                    self.assertEqual(api_calls+cli_calls, 4)
                finally:
                    release.set()
                self.assertEqual(cli_single.result(timeout=3), fixtures.TEXT)
                self.assertEqual(api_single.result(timeout=3), fixtures.TEXT)
            api_final, cli_final = self.wait(api_job), self.wait(cli_job)
        self.assertEqual((api_calls, cli_calls), (5, 5))
        self.assertEqual(maximum, 4)
        self.assertEqual(api_final['status'], 'succeeded')
        self.assertEqual(cli_final['status'], 'succeeded')

    def test_failed_requests_release_all_four_provider_slots(self):
        lock, release, four_started = threading.Lock(), self.event(), self.event()
        calls = 0
        def provider(*args, **kwargs):
            nonlocal calls
            with lock:
                calls += 1
                if calls == 4:
                    four_started.set()
            release.wait(3)
            raise ValueError('fixture request failed')
        with patch.object(expansion, 'call', side_effect=provider):
            job = self.start(concurrency=4)
            try:
                self.assertTrue(four_started.wait(2))
            finally:
                release.set()
            self.assertEqual(self.wait(job)['counts'], {'failed': 4})
        self.assert_four_slots_available()

    def test_cached_success_is_validated_and_corrupt_cache_is_regenerated(self):
        items = self.items(2)
        cache = self.directory/'cache'
        cache.mkdir()
        for item in items:
            key = expansion.cache_key(item['material'], profile=PROFILE, **{**SETTINGS,
                                                                          'codex_model': '', 'codex_reasoning': 'default', 'codex_timeout': 300})
            (cache/(key+'.json')).write_text(json.dumps({'text': fixtures.TEXT}) if item['index'] == 0 else '{broken', encoding='utf-8')
        with patch.object(expansion, 'call', return_value=ANSWER) as provider:
            final = self.wait(self.start(items))
        self.assertEqual(provider.call_count, 1)
        self.assertEqual([item['status'] for item in final['items']], ['cached', 'succeeded'])

    def test_invalid_material_and_segment_failure_do_not_block_other_items(self):
        items = self.items(4)
        items[0]['material'] = None
        items[1].update(material=None, protected=True)
        def provider(path, payload, **kwargs):
            if 'brief 2' in payload['messages'][1]['content'][0]['text']:
                raise ValueError('fixture segment error')
            return ANSWER
        with patch.object(expansion, 'call', side_effect=provider) as request:
            final = self.wait(self.start(items, concurrency=1))
        self.assertEqual(request.call_count, 2, 'No automatic provider retries')
        self.assertEqual([item['status'] for item in final['items']], ['failed', 'protected', 'failed', 'succeeded'])
        self.assertEqual(final['items'][1]['attempt'], 0)
        self.assertEqual(final['items'][3]['text'], fixtures.TEXT)

    def test_simultaneous_same_key_failure_does_not_repeat_the_paid_request(self):
        entered, release = self.event(), self.event()
        def provider(*args, **kwargs):
            entered.set()
            release.wait(3)
            raise ValueError('fixture request failed')
        with patch.object(expansion, 'call', side_effect=provider) as request:
            job = self.start([dict(index=0, material=self.packet(0)), dict(index=1, material=self.packet(0))])
            self.assertTrue(entered.wait(2))
            release.set()
            final = self.wait(job)
        request.assert_called_once()
        self.assertEqual(final['counts'], {'failed': 2})
        self.assertFalse(expansion._KEY_LOCKS)

    def test_cancel_api_keeps_active_until_return_and_does_not_touch_other_project(self):
        entered, release = self.event(), self.event()
        calls = []
        def provider(path, payload, **kwargs):
            brief = json.loads(payload['messages'][1]['content'][0]['text'])['brief']
            calls.append(brief)
            if brief == 'Approved brief 0':
                entered.set()
                release.wait(3)
            return ANSWER
        with patch.object(expansion, '_codex_cancelled', side_effect=AssertionError('Global interrupt touched')), \
             patch.object(expansion, 'call', side_effect=provider):
            job = self.start(concurrency=1)
            self.assertTrue(entered.wait(2))
            cancelling = jobs.cancel(self.directory/'project-a', job['id'])
            self.assertEqual(cancelling['status'], 'cancelling')
            self.assertTrue(jobs.active('project-a'))
            other = self.start([dict(index=0, material=self.packet('other'))], project_id='other')
            self.assertEqual(self.wait(other)['status'], 'succeeded')
            self.assertTrue(jobs.active('project-a'))
            release.set()
            final = self.wait(job)
        self.assertEqual(len(calls), 2)
        self.assertEqual(final['counts'], {'cancelled': 4})
        self.assertFalse(any(item['text'] for item in final['items']))
        self.assertFalse((self.directory/'project-a'/'segments.json').exists())
        self.assert_four_slots_available()

    def test_cli_batch_cancellation_uses_only_its_callback(self):
        entered, lock, calls = self.event(), threading.Lock(), 0
        def generate(*args, cancelled, **kwargs):
            nonlocal calls
            with lock:
                calls += 1
                if calls == 4:
                    entered.set()
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                cancelled()
                time.sleep(.01)
            raise AssertionError('CLI callback did not cancel')
        with patch.object(expansion, '_codex_cancelled', side_effect=AssertionError('Global interrupt touched')), \
             patch.object(cli, 'generate', side_effect=generate):
            job = self.start(settings={**SETTINGS, 'provider': 'codex_cli'},
                request_profile={'cli_identity': {'config_hash': 'fixture'}}, concurrency=4)
            self.assertTrue(entered.wait(2))
            jobs.cancel(self.directory/'project-a', job['id'])
            final = self.wait(job)
        self.assertEqual(final['status'], 'cancelled')
        self.assertEqual(final['counts'], {'cancelled': 4})
        self.assert_four_slots_available()

    def test_cancel_preserves_completed_cache_even_when_inflight_api_returns_error(self):
        items = self.items(3)
        key = expansion.cache_key(items[0]['material'], profile=PROFILE, **SETTINGS)
        cache = self.directory/'cache'
        cache.mkdir()
        (cache/(key+'.json')).write_text(json.dumps({'text': fixtures.TEXT}), encoding='utf-8')
        entered, release = self.event(), self.event()
        def provider(*args, **kwargs):
            entered.set()
            release.wait(3)
            raise ValueError('fixture connection interrupted')
        with patch.object(expansion, 'call', side_effect=provider):
            job = self.start(items, concurrency=1)
            self.assertTrue(entered.wait(2))
            jobs.cancel(self.directory/'project-a', job['id'])
            release.set()
            final = self.wait(job)
        self.assertEqual(final['counts'], {'cached': 1, 'cancelled': 2})
        self.assertEqual(final['items'][0]['text'], fixtures.TEXT)

    def test_definite_global_configuration_error_pauses_pending_without_retry(self):
        with patch.object(expansion, 'call', side_effect=ValueError('HTTP 401 invalid_api_key')) as provider:
            final = self.wait(self.start(concurrency=1))
        provider.assert_called_once()
        self.assertEqual(final['status'], 'paused')
        self.assertEqual(final['counts'], {'failed': 1, 'pending': 3})
        self.assertTrue(all(item['attempt'] == 0 for item in final['items'][1:]))

    def test_profile_and_settings_are_frozen_and_credentials_never_leave_memory(self):
        profile, settings = copy.deepcopy(PROFILE), {**SETTINGS, 'api_key': PROFILE['api_key']}
        entered, release = self.event(), self.event()
        received = []
        def provider(path, payload, profile):
            received.append(copy.deepcopy(profile))
            if len(received) == 1:
                entered.set()
                release.wait(3)
            return ANSWER
        items = self.items(2)
        items[0]['material']['api_key'] = PROFILE['api_key']
        with patch.object(expansion, 'settings', side_effect=AssertionError('Batch reread live API settings')), \
             patch.object(expansion, 'call', side_effect=provider):
            job = self.start(items, settings=settings, request_profile=profile, concurrency=1)
            self.assertTrue(entered.wait(2))
            profile.update(api_key='new-private-key', base_url='https://changed.test/v1')
            profile['extra_body']['temperature'] = 1
            settings['model'] = 'changed-model'
            release.set()
            final = self.wait(job)
        expected = {**PROFILE, 'response_mode': 'json', 'thinking_mode': 'auto'}
        self.assertEqual(received, [expected, expected])
        serialized = json.dumps(jobs.list_jobs(self.directory/'project-a'))
        disk = (self.directory/'project-a'/'state'/'expansion_jobs'/(job['id']+'.json')).read_text(encoding='utf-8')
        for secret in (PROFILE['api_key'], 'new-private-key', 'cli_identity', 'api_key'):
            self.assertNotIn(secret, serialized+disk)
        self.assertEqual(final['settings']['model'], SETTINGS['model'])
        final['items'][0]['text'] = 'caller mutation'
        self.assertEqual(jobs.get(self.directory/'project-a', job['id'])['items'][0]['text'], fixtures.TEXT)

    def test_restart_preserves_success_and_marks_unfinished_without_sending(self):
        job_id = str(uuid.uuid4())
        directory = self.directory/'project-a'
        path = directory/'state'/'expansion_jobs'/(job_id+'.json')
        path.parent.mkdir(parents=True)
        job = dict(id=job_id, job_id=job_id, project_id='project-a', project_revision=7,
            status='running', items=[dict(index=0, status='succeeded', text=fixtures.TEXT, key='saved-key'),
                                     dict(index=1, status='running', text=''), dict(index=2, status='pending', text='')])
        path.write_text(json.dumps(job), encoding='utf-8')
        with patch.object(expansion, 'call', side_effect=AssertionError('Restart resent a paid request')):
            restored = jobs.get(directory, job_id)
            listing = jobs.list_jobs(directory)
        self.assertEqual(restored['status'], 'interrupted')
        self.assertEqual([item['status'] for item in restored['items']], ['succeeded', 'interrupted', 'interrupted'])
        self.assertEqual(restored['items'][0]['text'], fixtures.TEXT)
        self.assertEqual(listing[0], restored)
        self.assertFalse(jobs.active('project-a'))
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['status'], 'interrupted')

    def test_v2_material_intent_is_frozen_in_requests_keys_and_persisted_candidates(self):
        entered, release = self.event(), self.event()
        received = []
        items = self.items(2)
        for item in items:
            item['material'].update(contract_version=2, brief_source='none', shot_intent='@图1向左走，镜头跟随')
        originals = copy.deepcopy(items)
        def provider(path, payload, **kwargs):
            received.append(json.loads(payload['messages'][1]['content'][0]['text']))
            if len(received) == 1:
                entered.set()
                release.wait(3)
            return ANSWER
        with patch.object(expansion, 'call', side_effect=provider):
            job = self.start(items, concurrency=1)
            self.assertTrue(entered.wait(2))
            for item in items:
                item['material'].update(contract_version=3, brief_source='automatic', shot_intent='调用方后续修改')
            release.set()
            final = self.wait(job)
        self.assertEqual(final['counts'], {'succeeded': 2})
        for original, item, request in zip(originals, final['items'], received):
            for name in ('contract_version', 'brief_source', 'shot_intent'):
                self.assertEqual(item['material'][name], original['material'][name])
            self.assertEqual(request['contract_version'], 2)
            self.assertEqual(request['brief_source'], 'none')
            self.assertEqual(request['shot_intent'], '<Picture 1>向左走，镜头跟随')
            self.assertEqual(item['key'], expansion.cache_key(original['material'], profile=PROFILE, **SETTINGS))
        self.assertEqual(jobs.get(self.directory/'project-a', job['id'])['items'], final['items'])

    def test_error_redacts_frozen_credentials_and_rejects_duplicate_active_project(self):
        entered, release = self.event(), self.event()
        def provider(*args, **kwargs):
            entered.set()
            release.wait(3)
            raise ValueError('fixture error '+PROFILE['api_key'])
        with patch.object(expansion, 'call', side_effect=provider):
            job = self.start(concurrency=1)
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, '已有批量扩写'):
                self.start()
            release.set()
            final = self.wait(job)
        self.assertNotIn(PROFILE['api_key'], json.dumps(final))

    def test_concurrency_requires_an_integer_between_one_and_four(self):
        for concurrency in (0, 5, -1, True, False, 1.0, 2.0, 3.0, 4.0, 1.5, '4', None):
            with self.subTest(concurrency=concurrency), self.assertRaisesRegex(ValueError, '整数 1、2、3 或 4'):
                self.start(concurrency=concurrency)

    def test_invalid_h3_response_or_duration_fails_only_affected_batch_items(self):
        items = self.items(3)
        items[0]['material'].update(generation_seconds=16, generation_frames=384)
        def provider(path, payload, **kwargs):
            if 'brief 1' in payload['messages'][1]['content'][0]['text']:
                return {'choices': [{'message': {'content': fixtures.TEXT.replace('fully_preserved', 'bad_marker')}}]}
            return ANSWER
        with patch.object(expansion, 'call', side_effect=provider) as request:
            final = self.wait(self.start(items, concurrency=1))
        self.assertEqual(request.call_count, 2, 'Out-of-range generation must never call a provider')
        self.assertEqual([item['status'] for item in final['items']], ['failed', 'failed', 'succeeded'])
        self.assertFalse(list((self.directory/'cache').glob(final['items'][1]['key']+'.json')))

    def test_confirmed_audio_metadata_survives_batch_material_snapshot(self):
        packet = self.packet(0)
        packet.update(audio_reference_available=True, audio_reference_slot=1)
        final = self.start([dict(index=0, material=packet, protected=True)])
        self.assertTrue(final['items'][0]['material']['audio_reference_available'])
        self.assertEqual(final['items'][0]['material']['audio_reference_slot'], 1)


if __name__ == '__main__':
    unittest.main()
