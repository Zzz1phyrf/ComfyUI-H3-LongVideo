import importlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image
import psutil
import test_materials as fixtures

expansion = fixtures.expansion
cli = importlib.import_module('h3lv_test.codex_cli')


class CodexExpansionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        paths = []
        for i, color in enumerate(((200, 10, 20), (20, 30, 210))):
            image = self.directory/f'{i}.png'
            Image.new('RGB', (20, 16), color).save(image)
            paths.append(str(image))
        self.packet = dict(paths=paths, hashes=['one', 'two'], material_note='@图1是人物，@图2是环境',
            visual_type='atmosphere', mode='singing', brief='Keep the performer silent.', duration=10,
            generation_frames=242, generation_seconds=242/24, audio_role='instrumental',
            audio_section='interlude', audio_role_reason='fixture', cache_dir=str(self.directory/'cache'))
        self.identity = patch.object(cli, 'identity', side_effect=lambda model='', reasoning='default':
            {'backend': 'fixture', 'model': model, 'reasoning': reasoning})
        self.identity.start()
        self.addCleanup(self.identity.stop)
        interrupt = patch.object(expansion, '_codex_cancelled', return_value=None)
        interrupt.start()
        self.addCleanup(interrupt.stop)

    def test_cli_vision_keeps_picture_order_and_needs_no_api_profile(self):
        with patch.object(expansion, 'public_settings', side_effect=AssertionError('API accessed')), \
             patch.object(expansion, 'call', side_effect=AssertionError('API called')), \
             patch.object(cli, 'generate', return_value=fixtures.TEXT) as generate:
            result = expansion.PromptExpand().run(self.packet, 'vision', 'unused-api-model', 'fixture rule',
                provider='codex_cli', codex_model='gpt-fixture', codex_reasoning='high')
            self.assertEqual(result['result'], (fixtures.TEXT,))
            prompt, images = generate.call_args.args
            self.assertEqual(images, self.packet['paths'])
            self.assertIn('<Picture 1>是人物', prompt)
            self.assertIn('<Picture 2>是环境', prompt)
            self.assertEqual(generate.call_args.kwargs['model'], 'gpt-fixture')
            self.assertEqual(generate.call_args.kwargs['reasoning'], 'high')
            self.assertEqual(expansion.PromptExpand().run(self.packet, 'vision', 'unused-api-model', 'fixture rule',
                provider='codex_cli', codex_model='gpt-fixture', codex_reasoning='high')['result'], (fixtures.TEXT,))
            generate.assert_called_once()

    def test_text_mode_does_not_send_images(self):
        with patch.object(cli, 'generate', return_value=fixtures.TEXT) as generate:
            expansion.expand(self.packet, 'text', 'unused', 'rule', provider='codex_cli')
            self.assertEqual(generate.call_args.args[1], [])

    def test_backend_model_reasoning_and_config_invalidate_cache_but_timeout_does_not(self):
        args = (self.packet, 'vision', 'api-model', 'rule', 0)
        api = expansion.cache_key(*args, profile={'base_url': 'https://fixture.test/v1'})
        first = expansion.cache_key(*args, provider='codex_cli')
        self.assertNotEqual(first, api)
        self.assertEqual(first, expansion.cache_key(*args, provider='codex_cli', codex_timeout=600))
        self.assertEqual(first, expansion.cache_key(self.packet, 'vision', 'another-api-model', 'rule', 0, provider='codex_cli'))
        self.assertNotEqual(first, expansion.cache_key(*args, provider='codex_cli', codex_model='gpt-fixture'))
        self.assertNotEqual(first, expansion.cache_key(*args, provider='codex_cli', codex_reasoning='high'))
        with patch.object(cli, 'identity', return_value={'backend': 'changed config'}):
            self.assertNotEqual(first, expansion.cache_key(*args, provider='codex_cli'))

    def test_bad_cli_output_is_not_cached_or_retried(self):
        with patch.object(cli, 'generate', return_value='incomplete result') as generate:
            with self.assertRaises(ValueError):
                expansion.expand(self.packet, 'vision', 'unused', 'rule', provider='codex_cli')
            generate.assert_called_once()
        self.assertFalse(list((self.directory/'cache').glob('*.json')))

    def test_cli_invalid_ref2va_retention_never_becomes_a_success_cache(self):
        bad = fixtures.TEXT.replace('fully_preserved', 'bad_marker')
        with patch.object(cli, 'generate', return_value=bad) as generate:
            with self.assertRaisesRegex(ValueError, 'retention|marker'):
                expansion.expand(self.packet, 'text', 'unused', 'rule', provider='codex_cli')
        generate.assert_called_once()
        self.assertFalse(list((self.directory/'cache').glob('*.json')))

    def test_old_node_inputs_and_manual_mode_remain_usable(self):
        self.assertEqual(set(expansion.PromptExpand.INPUT_TYPES()['required']),
                         {'material', 'mode', 'model', 'rule', 'revision'})
        with patch.object(expansion, 'public_settings', return_value={'base_url': 'https://fixture.test/v1'}), \
             patch.object(cli, 'generate', side_effect=AssertionError('CLI called')):
            self.assertEqual(expansion.PromptExpand().run(self.packet, 'manual', 'old-model', 'rule',
                provider='codex_cli', codex_model='invalid name')['result'], (self.packet['material_note'],))
            manual = {**self.packet, 'shot_intent': '人物向左转身'}
            self.assertEqual(expansion.PromptExpand().run(manual, 'manual', 'old-model', 'rule')['result'],
                             (manual['material_note']+'\n\n'+manual['shot_intent'],))
            manual.update(material_note='', shot_intent='')
            self.assertEqual(expansion.PromptExpand().run(manual, 'manual', 'old-model', 'rule')['result'],
                             (manual['brief'],))

    def test_pending_snapshots_refresh_only_expansion_backend_settings(self):
        controller = fixtures.controller
        snapshot = {'prompt': {'1': {'class_type': 'H3LVPromptExpand', 'inputs': {'material': ['5', 4], 'model': 'old'}},
                               '2': {'class_type': 'Sampler', 'inputs': {'steps': 10}}}}
        current = {'1': {'class_type': 'H3LVPromptExpand', 'inputs': {'material': ['5', 4], 'model': 'old',
            'provider': 'codex_cli', 'codex_model': 'gpt-fixture', 'codex_reasoning': 'high', 'codex_timeout': 600}},
            '2': {'class_type': 'Sampler', 'inputs': {'steps': 99}}}
        controller.refresh_expansion_settings(snapshot, current)
        self.assertEqual(snapshot['prompt']['1']['inputs']['provider'], 'codex_cli')
        self.assertEqual(snapshot['prompt']['2']['inputs']['steps'], 10)
        current['1']['inputs'] = {'material': ['5', 4], 'model': 'old'}
        controller.refresh_expansion_settings(snapshot, current)
        self.assertNotIn('provider', snapshot['prompt']['1']['inputs'])

    def test_runner_uses_native_argv_utf8_and_final_file_without_leaking_keys(self):
        captured = {}
        def run(argv, directory, stdin, stdout, stderr, env, timeout, cancelled):
            captured.update(argv=argv, prompt=stdin.read().decode('utf-8'), env=env)
            images = argv[argv.index('--image')+1:]
            captured['colors'] = [Image.open(path).getpixel((0, 0)) for path in images]
            Path(argv[argv.index('--output-last-message')+1]).write_text('final fixture', encoding='utf-8')
            return 0
        with patch.object(cli, 'resolve_executable', return_value='C:/fixture/codex.exe'), \
             patch.object(cli, '_config', return_value=(b'', {})), patch.object(cli, 'run_process', side_effect=run), \
             patch.dict(os.environ, {'OPENAI_API_KEY': 'never-forward', 'CODEX_API_KEY': 'never-forward'}):
            self.assertEqual(cli.generate('中文输入', self.packet['paths']), 'final fixture')
        self.assertEqual(captured['prompt'], '中文输入')
        self.assertIn('--ephemeral', captured['argv'])
        self.assertEqual(captured['argv'][captured['argv'].index('--sandbox')+1], 'read-only')
        self.assertNotIn('-', captured['argv'])
        self.assertEqual(len(captured['colors']), 2)
        self.assertGreater(captured['colors'][0][0], captured['colors'][0][2])
        self.assertGreater(captured['colors'][1][2], captured['colors'][1][0])
        self.assertNotIn('OPENAI_API_KEY', captured['env'])
        self.assertNotIn('CODEX_API_KEY', captured['env'])

    def test_cli_error_does_not_echo_stderr_secrets(self):
        def fail(argv, directory, stdin, stdout, stderr, env, timeout, cancelled):
            stderr.write(b'private-token-and-prompt unauthorized')
            return 1
        with patch.object(cli, 'resolve_executable', return_value='C:/fixture/codex.exe'), \
             patch.object(cli, '_config', return_value=(b'', {})), patch.object(cli, 'run_process', side_effect=fail):
            with self.assertRaisesRegex(ValueError, '未登录') as error:
                cli.generate('fixture', [])
        self.assertNotIn('private-token', str(error.exception))

    def test_changed_default_cli_configuration_blocks_old_batch_before_spawn(self):
        expected = {'config_hash': hashlib.sha256(b'original').hexdigest()}
        with patch.object(cli, 'resolve_executable', return_value='C:/fixture/codex.exe'), \
             patch.object(cli, '_config', return_value=(b'changed', {})), \
             patch.object(cli, 'run_process') as process:
            with self.assertRaisesRegex(ValueError, 'CLI 配置已变化'):
                cli.generate('fixture', [], expected_identity=expected)
        process.assert_not_called()

    def test_cli_configuration_change_during_request_discards_result(self):
        raw = b'original'
        config = {'current': raw}
        expected = {'config_hash': hashlib.sha256(raw).hexdigest()}
        def run(argv, directory, stdin, stdout, stderr, env, timeout, cancelled):
            Path(argv[argv.index('--output-last-message')+1]).write_text('fixture result', encoding='utf-8')
            config['current'] = b'changed'
            return 0
        with patch.object(cli, 'resolve_executable', return_value='C:/fixture/codex.exe'), \
             patch.object(cli, '_config', side_effect=lambda: (config['current'], {})), \
             patch.object(cli, 'run_process', side_effect=run) as process:
            with self.assertRaisesRegex(ValueError, 'CLI 配置已变化'):
                cli.generate('fixture', [], expected_identity=expected)
        process.assert_called_once()

    def test_batch_cli_identity_is_used_for_key_and_forwarded_to_runner(self):
        profile = {'cli_identity': {'config_hash': 'original', 'model': '', 'reasoning': 'default'}}
        with patch.object(cli, 'identity', side_effect=AssertionError('Live CLI identity reread')), \
             patch.object(cli, 'generate', return_value=fixtures.TEXT) as generate:
            original = expansion.cache_key(self.packet, 'text', 'unused', 'rule', 0,
                                           profile=profile, provider='codex_cli')
            info = {}
            expansion.expand(self.packet, 'text', 'unused', 'rule', provider='codex_cli',
                             request_profile=profile, cancelled=lambda: False, result_info=info)
        self.assertEqual(info['key'], original)
        self.assertEqual(generate.call_args.kwargs['expected_identity'], profile['cli_identity'])


class ProcessLifecycleTests(unittest.TestCase):
    def check_tree(self, action):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            marker = directory/'child.pid'
            script = ("import subprocess,sys,time,pathlib;"
                      "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                      f"pathlib.Path({str(marker)!r}).write_text(str(p.pid));"
                      + ("time.sleep(60)" if action != 'success' else ''))
            class Cancelled(Exception):
                pass
            def cancel():
                if action == 'cancel' and marker.exists():
                    raise Cancelled()
            with (directory/'in').open('wb+') as stdin, (directory/'out').open('wb') as out, \
                 (directory/'err').open('wb') as err:
                if action == 'timeout':
                    with self.assertRaisesRegex(ValueError, '等待超过'):
                        cli.run_process([sys.executable, '-c', script], directory, stdin, out, err,
                                        os.environ.copy(), 1.5, cancel)
                elif action == 'cancel':
                    with self.assertRaises(Cancelled):
                        cli.run_process([sys.executable, '-c', script], directory, stdin, out, err,
                                        os.environ.copy(), 10, cancel)
                else:
                    self.assertEqual(cli.run_process([sys.executable, '-c', script], directory, stdin, out, err,
                                                     os.environ.copy(), 10, cancel), 0)
            self.assertTrue(marker.is_file())
            child = int(marker.read_text())
            deadline = time.monotonic()+3
            while psutil.pid_exists(child) and time.monotonic() < deadline:
                time.sleep(.05)
            alive = psutil.pid_exists(child)
            if alive:
                psutil.Process(child).kill()
            self.assertFalse(alive, 'Codex descendant survived cleanup')

    def test_timeout_kills_descendants(self):
        self.check_tree('timeout')

    def test_cancellation_kills_descendants(self):
        self.check_tree('cancel')

    def test_success_cleans_detached_descendants(self):
        self.check_tree('success')


if __name__ == '__main__':
    unittest.main()
