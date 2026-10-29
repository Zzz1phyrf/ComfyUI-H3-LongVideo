"""API compatibility regression tests; fixtures never contact a paid service."""
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import IncompleteRead
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from PIL import Image
import test_materials as fixtures

expansion = fixtures.expansion
KEY = 'test-private-api-key'


class Response(io.BytesIO):
    def __init__(self, body, content_type='application/json'):
        super().__init__(body)
        self.headers = {'Content-Type': content_type}
        self.status = 200


class ExpansionAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        image = self.directory/'fixture.png'
        Image.new('RGB', (16, 16), (70, 50, 80)).save(image)
        self.packet = dict(paths=[str(image)], hashes=['fixture'], material_note='@图1是人物',
            visual_type='performance', mode='singing', brief='Approved fixture brief', duration=10,
            generation_frames=242, generation_seconds=242/24, audio_role='vocal',
            audio_section='vocal', audio_role_reason='fixture', cache_dir=str(self.directory/'cache'))
        self.config = {'base_url': 'https://compatible.test/v1', 'api_key': KEY}

    def payload(self, model, host='compatible.test', **options):
        profile = {'base_url': f'https://{host}/v1', **options}
        with patch.object(expansion, 'public_settings', return_value=profile), \
             patch.object(expansion, 'call', return_value={'choices': [{'message': {'content': fixtures.TEXT}}]}) as request:
            expansion.PromptExpand().run(self.packet, 'vision', model, 'fixture rule')
            return request.call_args.args[1]

    def test_bailian_bare_and_prefixed_qwen_disable_thinking(self):
        for model in ['qwen3.8-max', 'qwen/qwen3.8-flash']:
            with self.subTest(model=model):
                payload = self.payload(model, 'dashscope.aliyuncs.com')
                self.assertIs(payload['enable_thinking'], False)
                self.assertNotIn('reasoning', payload)

    def test_omni_uses_its_own_control(self):
        payload = self.payload('qwen3.8-omni-flash', 'dashscope.aliyuncs.com')
        self.assertEqual(payload['reasoning_effort'], 'none')
        self.assertNotIn('enable_thinking', payload)

    def test_openrouter_qwen_uses_gateway_control(self):
        payload = self.payload('qwen/qwen3.8-max', 'openrouter.ai')
        self.assertEqual(payload['reasoning'], {'effort': 'none'})
        self.assertNotIn('enable_thinking', payload)

    def test_unknown_provider_and_non_qwen_receive_no_vendor_fields(self):
        for model, host in [('qwen/qwen3.8-max', 'compatible.test'),
                            ('gpt-example', 'dashscope.aliyuncs.com'),
                            ('qwen3.8-max', 'dashscope.aliyuncs.com.attacker.test'),
                            ('qwen3.7-max-2026-05-17', 'dashscope.aliyuncs.com'),
                            ('qwen3.8-2.4t-a95b', 'dashscope.aliyuncs.com')]:
            with self.subTest(model=model, host=host):
                payload = self.payload(model, host)
                self.assertEqual(set(payload), {'model', 'messages', 'max_tokens', 'stream'})

    def test_custom_options_override_and_can_remove_token_limit(self):
        payload = self.payload('model-alias', extra_body={
            'max_tokens': None, 'max_completion_tokens': 4096, 'reasoning_effort': 'low'},
            response_mode='stream')
        self.assertNotIn('max_tokens', payload)
        self.assertEqual(payload['max_completion_tokens'], 4096)
        self.assertEqual(payload['reasoning_effort'], 'low')
        self.assertIs(payload['stream'], True)

    def test_provider_default_does_not_override_thinking(self):
        payload = self.payload('qwen3.8-max', 'dashscope.aliyuncs.com', thinking_mode='provider_default')
        self.assertNotIn('enable_thinking', payload)

    def test_settings_migrate_validate_and_keep_secrets_private(self):
        with patch.object(expansion, 'settings_path', return_value=self.directory/'profile.json'):
            value = expansion.save_settings(self.config['base_url'], KEY)
            self.assertEqual(value['timeout_seconds'], 300)
            self.assertEqual(value['response_mode'], 'json')
            self.assertEqual(value['extra_body'], {})
            value = expansion.save_settings(self.config['base_url'], '', timeout_seconds=600,
                response_mode='stream', thinking_mode='provider_default', extra_body={'temperature': 0.3})
            self.assertTrue(value['configured'])
            self.assertEqual(value['timeout_seconds'], 600)
            self.assertNotIn(KEY, str(value))
            expansion.save_settings(self.config['base_url'], '')
            self.assertEqual(expansion.public_settings()['response_mode'], 'stream')
            for fields in [dict(timeout_seconds=0), dict(timeout_seconds=True),
                           dict(response_mode='invalid'), dict(thinking_mode='invalid'),
                           dict(extra_body=[]), dict(extra_body={'messages': []}),
                           dict(extra_body={'stream': True}), dict(extra_body={'api_key': KEY})]:
                with self.subTest(fields=list(fields)), self.assertRaises(ValueError):
                    expansion.save_settings(self.config['base_url'], '', **fields)

    def test_output_parameters_invalidate_cache_but_transport_does_not(self):
        profile = {'base_url': self.config['base_url'], 'extra_body': {}}
        with patch.object(expansion, 'public_settings', return_value=profile):
            key = expansion.cache_key(self.packet, 'vision', 'model', 'rule', 0)
            profile.update(timeout_seconds=600, response_mode='stream')
            self.assertEqual(key, expansion.cache_key(self.packet, 'vision', 'model', 'rule', 0))
            profile['extra_body'] = {'temperature': 0.5}
            self.assertNotEqual(key, expansion.cache_key(self.packet, 'vision', 'model', 'rule', 0))

    def call(self, response=None, error=None, config=None):
        kwargs = {'side_effect': error} if error else {'return_value': response}
        with patch.object(expansion, 'settings', return_value=config or self.config), \
             patch.object(expansion, 'urlopen', **kwargs) as request:
            try:
                return expansion.call('/chat/completions', {'model': 'fixture', 'stream': False})
            finally:
                self.assertEqual(request.call_count, 1, 'No automatic paid retries')

    def test_timeout_connection_and_format_errors_are_distinct(self):
        messages = []
        for error in [TimeoutError('fixture'), URLError(socket.gaierror(-2, 'fixture'))]:
            with self.assertRaises(ValueError) as caught:
                self.call(error=error)
            messages.append(str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            self.call(Response(b'<html>fixture</html>', 'text/html'))
        messages.append(str(caught.exception))
        self.assertEqual(len(set(messages)), 3)
        self.assertIn('超时', messages[0])
        self.assertIn('DNS', messages[1])
        self.assertIn('JSON', messages[2])

    def test_tls_and_broken_http_connections_have_safe_errors(self):
        for error in (ssl.SSLError(KEY), URLError(ssl.SSLError(KEY)), IncompleteRead(b'fixture', 20)):
            with self.subTest(error=type(error).__name__), self.assertRaises(ValueError) as caught:
                self.call(error=error)
            self.assertNotIn(KEY, str(caught.exception))

    def test_timeout_setting_reaches_transport_and_does_not_leak_secret(self):
        config = {**self.config, 'timeout_seconds': 600}
        with patch.object(expansion, 'settings', return_value=config), \
             patch.object(expansion, 'urlopen', side_effect=URLError(KEY)) as request:
            with self.assertRaises(ValueError) as caught:
                expansion.call('/models')
            self.assertEqual(request.call_args.kwargs['timeout'], 600)
            self.assertNotIn(KEY, str(caught.exception))

    def test_frozen_profile_controls_transport_without_reading_changed_settings(self):
        frozen = {**self.config, 'timeout_seconds': 600}
        with patch.object(expansion, 'settings', side_effect=AssertionError('Live profile reread')), \
             patch.object(expansion, 'urlopen', return_value=Response(b'{"choices": []}')) as request:
            self.assertEqual(expansion.call('/chat/completions', {'model': 'fixture'}, profile=frozen), {'choices': []})
        self.assertEqual(request.call_args.kwargs['timeout'], 600)
        self.assertEqual(request.call_args.args[0].full_url, frozen['base_url']+'/chat/completions')
        self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Bearer '+KEY)

    def test_capture_request_profile_deep_copies_all_effective_api_options(self):
        config = {**self.config, 'extra_body': {'temperature': .2}}
        with patch.object(expansion, 'settings', return_value=config):
            captured = expansion.capture_request_profile()
        config['extra_body']['temperature'] = .8
        config['api_key'] = 'changed'
        self.assertEqual(captured['extra_body'], {'temperature': .2})
        self.assertEqual(captured['api_key'], KEY)
        self.assertEqual(captured['response_mode'], 'json')

    def test_http_error_preserves_safe_code_not_key_or_request_body(self):
        body = json.dumps({'error': {'code': 'invalid_parameter', 'message': f'unsupported: {KEY}'}}).encode()
        error = HTTPError(self.config['base_url'], 400, 'fixture', {}, io.BytesIO(body))
        with self.assertRaises(ValueError) as caught:
            self.call(error=error)
        self.assertIn('HTTP 400', str(caught.exception))
        self.assertIn('invalid_parameter', str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))

    def test_http_200_error_object_is_not_a_success(self):
        for value in [{'error': {'code': 'upstream_unavailable', 'message': 'fixture'}},
                      {'code': 'upstream_unavailable', 'message': 'fixture'}]:
            with self.assertRaisesRegex(ValueError, 'upstream_unavailable'):
                self.call(Response(json.dumps(value).encode()))

    def test_json_and_content_blocks_are_accepted(self):
        answer = {'choices': [{'message': {'content': [{'type': 'text', 'text': fixtures.TEXT}]}}]}
        with patch.object(expansion, 'public_settings', return_value={'base_url': self.config['base_url']}), \
             patch.object(expansion, 'call', return_value=answer):
            self.assertEqual(expansion.expand(self.packet, 'vision', 'model', 'rule'), fixtures.TEXT)

    def stream(self, chunks, done=True):
        body = b': keepalive\n\n'
        for chunk in chunks:
            body += ('data: '+json.dumps(chunk, ensure_ascii=False)+'\n\n').encode('utf-8')
        if done:
            body += b'data: [DONE]\n\n'
        return Response(body, 'text/event-stream')

    def test_sse_merges_only_first_choice_body_not_reasoning(self):
        response = self.stream([
            {'choices': [{'index': 0, 'delta': {'reasoning_content': 'not the answer'}}]},
            {'choices': [{'index': 1, 'delta': {'content': 'other choice'}},
                         {'index': 0, 'delta': {'content': 'Hello '}}]},
            {'choices': []},
            {'choices': [{'index': 0, 'delta': {'content': 'world'}, 'finish_reason': 'stop'}]},
        ])
        answer = self.call(response)
        self.assertEqual(answer['choices'][0]['message']['content'], 'Hello world')
        self.assertEqual(answer['choices'][0]['finish_reason'], 'stop')

    def test_sse_disconnect_and_error_do_not_cache_partial_result(self):
        for response in [self.stream([{'choices': [{'index': 0, 'delta': {'content': fixtures.TEXT}}]}], done=False),
                         self.stream([{'error': {'code': 'upstream_error', 'message': 'fixture'}}])]:
            with patch.object(expansion, 'settings', return_value=self.config), \
                 patch.object(expansion, 'public_settings', return_value={'base_url': self.config['base_url']}), \
                 patch.object(expansion, 'urlopen', return_value=response):
                with self.assertRaises(ValueError):
                    expansion.expand(self.packet, 'vision', 'model', 'rule')
            self.assertFalse(Path(self.packet['cache_dir']).exists())

    def test_json_fallback_and_sse_without_content_type(self):
        response = self.stream([{'choices': [{'index': 0, 'delta': {'content': 'fixture'}, 'finish_reason': 'stop'}]}])
        response.headers = {}
        self.assertEqual(self.call(response)['choices'][0]['message']['content'], 'fixture')
        answer = {'choices': [{'message': {'content': 'fixture'}}]}
        self.assertEqual(self.call(Response(json.dumps(answer).encode())), answer)

    def test_node_roundtrip_through_real_local_http_json_and_sse(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(payload)
                if payload['stream']:
                    chunks = [{'choices': [{'index': 0, 'delta': {'reasoning_content': 'not the answer'}}]}]
                    for part in (fixtures.TEXT[:50], fixtures.TEXT[50:]):
                        chunks.append({'choices': [{'index': 0, 'delta': {'content': part}}]})
                    chunks.append({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})
                    body = ''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n'
                    kind = 'text/event-stream'
                else:
                    body = json.dumps({'choices': [{'message': {'content': fixtures.TEXT}, 'finish_reason': 'stop'}]})
                    kind = 'application/json'
                body = body.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            for mode in ('json', 'stream'):
                config = {**self.config, 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                          'response_mode': mode, 'extra_body': {'max_tokens': None, 'max_completion_tokens': 4096}}
                packet = {**self.packet, 'cache_dir': str(self.directory/mode)}
                with patch.object(expansion, 'settings', return_value=config):
                    result = expansion.PromptExpand().run(packet, 'vision', 'strict-compatible-model', 'fixture rule')
                    self.assertEqual(result['result'][0], fixtures.TEXT)
            self.assertEqual(len(received), 2)
            self.assertEqual([value['stream'] for value in received], [False, True])
            self.assertTrue(all('enable_thinking' not in value and 'max_tokens' not in value for value in received))
            self.assertTrue(all(len(value['messages'][1]['content']) == 2 for value in received))
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
