"""Exercise failed ASR subprocess output through the node and shared log download."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from test_diagnostics import diagnostics

nodes = importlib.import_module('h3lv_test.nodes')
errors = importlib.import_module('h3lv_test.workflow_errors')

PROXY_ERROR = "requests.exceptions.ProxyError: Cannot connect to proxy. [WinError 10061] 连接被拒绝"


class ASRDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.output = Path(temp.name)
        self.project = self.output/'H3LongVideo/MV/projects'/('a'*32)
        (self.project/'state').mkdir(parents=True)
        paths = types.SimpleNamespace(get_output_directory=lambda: str(self.output))
        mm = types.ModuleType('comfy.model_management')
        mm.throw_exception_if_processing_interrupted = lambda: None
        comfy = types.ModuleType('comfy'); comfy.model_management = mm
        self.modules = patch.dict(sys.modules, {'folder_paths': paths, 'comfy': comfy,
                                                'comfy.model_management': mm})
        self.modules.start(); self.addCleanup(self.modules.stop)
        key = patch.object(diagnostics, 'configured_key', return_value='fixture-private-key')
        key.start(); self.addCleanup(key.stop)

    def fail_asr(self, text):
        def launch(command, stdout, stderr, **options):
            stdout.write(text); stdout.flush()
            return types.SimpleNamespace(returncode=1, poll=lambda: 1)
        with patch('importlib.util.find_spec', return_value=object()), \
             patch.object(nodes, 'asr_model_root', return_value=self.output/'models'), \
             patch.object(nodes.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(RuntimeError) as result:
                nodes.run_asr(self.project)
        return result.exception

    def test_real_node_error_and_download_include_child_reason_without_original_file(self):
        error = self.fail_asr('Faster-Whisper cache missing or incomplete\n'
            'Traceback: worker.py\nfixture-private-key Bearer fixture-token\n'
            'https://user:proxy-password@proxy.test/?token=fixture-query\n' + PROXY_ERROR)
        self.assertIn('ProxyError', str(error))
        self.assertIn('10061', str(error))
        self.assertIn('退出码 1', str(error))
        self.assertEqual(type(error).__name__, 'ASRError')
        panel = errors.failure_report(error, 'api_route')
        self.assertEqual(panel['category'], 'ASR 语音识别')
        self.assertIn('ProxyError', panel['reason'])
        (self.project/'state/asr.log').unlink()
        report = diagnostics.download_report().decode()
        self.assertIn('worker.py', report)
        self.assertIn('cache missing or incomplete', report)
        self.assertIn('ProxyError', report)
        self.assertIn('10061', report)
        for secret in ('fixture-private-key', 'fixture-token', 'proxy-password', 'fixture-query'):
            self.assertNotIn(secret, report)
            self.assertNotIn(secret, str(error))

    def test_empty_child_output_still_identifies_failed_exit(self):
        error = self.fail_asr('')
        self.assertIn('退出码 1', str(error))
        self.assertIn('未输出具体错误', str(error))

    def test_old_record_download_backfills_asr_log_without_changing_project(self):
        log = self.project/'state/asr.log'
        log.write_text('Traceback: worker.py\n' + PROXY_ERROR, encoding='utf-8')
        diagnostics.record_error('speech_recognition', RuntimeError('语音识别失败。'),
                                 project_id=self.project.name)
        report = diagnostics.download_report().decode()
        self.assertIn('asr.log', report)
        self.assertIn('ProxyError', report)
        self.assertEqual(log.read_text(encoding='utf-8'), 'Traceback: worker.py\n' + PROXY_ERROR)
        self.assertFalse((self.project/'state/segments.json').exists())

    def test_large_log_keeps_last_error_and_bounded_report(self):
        error = self.fail_asr(('progress line\n'*20000) + PROXY_ERROR)
        self.assertIn('ProxyError', str(error))
        report = diagnostics.download_report()
        self.assertIn(b'ProxyError', report)
        self.assertLess(len(report), 32000)

    def test_legacy_attachment_rejects_external_project_paths(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other)/'state'; outside.mkdir()
            (outside/'asr.log').write_text('OUTSIDE-PRIVATE', encoding='utf-8')
            diagnostics.record_error('speech_recognition', RuntimeError('语音识别失败。'),
                                     project_id=other)
            self.assertNotIn('OUTSIDE-PRIVATE', diagnostics.download_report().decode())

    def test_legacy_attachments_are_limited_to_five_recent_projects(self):
        for index in range(8):
            project = self.project.parent/f'{index:032x}'
            (project/'state').mkdir(parents=True)
            (project/'state/asr.log').write_text(f'ASR-old-{index}', encoding='utf-8')
            diagnostics.record_error('speech_recognition', RuntimeError('语音识别失败。'),
                                     project_id=project.name)
        report = diagnostics.download_report().decode()
        for index in range(3):
            self.assertNotIn(f'ASR-old-{index}', report)
        for index in range(3, 8):
            self.assertIn(f'ASR-old-{index}', report)


if __name__ == '__main__':
    unittest.main()
