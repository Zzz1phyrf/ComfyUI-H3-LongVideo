import importlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import test_plugin as support

credentials = importlib.import_module('h3lv_test.rh_credentials')
transport = importlib.import_module('h3lv_test.talking_rh.remote')
diagnostics = importlib.import_module('h3lv_test.diagnostics')
AI, CN = credentials.SITES


class CredentialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'runninghub_api.json'
        for context in (patch.object(credentials,'settings_path',return_value=self.path),
                        patch.dict('os.environ',{'RUNNINGHUB_API_KEY':''})):
            context.start(); self.addCleanup(context.stop)

    def test_save_is_live_site_scoped_and_not_part_of_client_config(self):
        config = dict(base_url=AI,workflow_id='123',instance_type='plus')
        with self.assertRaisesRegex(transport.RemoteError,'RH API 设置'):transport.Client(config)
        self.assertEqual(credentials.save_settings(AI,'fixture-private-ai'),{'configured':True,'source':'local'})
        credentials.save_settings(CN,'fixture-private-cn')
        self.assertEqual(transport.Client(config).key,'fixture-private-ai')
        self.assertEqual(transport.Client({**config,'base_url':CN}).key,'fixture-private-cn')
        self.assertNotIn('private',json.dumps(config))
        credentials.save_settings(AI,'fixture-private-new')
        self.assertEqual(transport.Client(config).key,'fixture-private-new')
        self.assertNotIn('private',json.dumps(credentials.public_settings(AI)))

    def test_empty_input_retains_key_clear_reveals_environment_fallback(self):
        credentials.save_settings(AI,'fixture-private-local')
        with patch.dict('os.environ',{'RUNNINGHUB_API_KEY':'fixture-private-env'}):
            with self.assertRaises(ValueError):credentials.save_settings(AI,'')
            self.assertEqual(credentials.api_key(AI),'fixture-private-local')
            self.assertEqual(credentials.save_settings(AI,clear=True),{'configured':True,'source':'environment'})
            self.assertEqual(credentials.api_key(AI),'fixture-private-env')
        self.assertFalse(credentials.public_settings(AI)['configured'])

    def test_atomic_write_failure_preserves_previous_key_and_removes_temp(self):
        credentials.save_settings(AI,'old-fixture')
        with patch.object(credentials.os,'replace',side_effect=OSError('denied')):
            with self.assertRaisesRegex(ValueError,'保存失败'):credentials.save_settings(AI,'new-fixture')
        self.assertEqual(credentials.api_key(AI),'old-fixture')
        self.assertEqual(list(self.path.parent.glob('.rh-*.tmp')),[])

    def test_known_keys_are_redacted_and_invalid_site_is_rejected(self):
        credentials.save_settings(AI,'fixture-private-ai')
        credentials.save_settings(CN,'fixture-private-cn')
        self.assertNotIn('private',diagnostics.redact('fixture-private-ai fixture-private-cn'))
        for site in ('http://www.runninghub.ai','https://example.invalid',None):
            with self.assertRaises(ValueError):credentials.save_settings(site,'fixture-key')


class CredentialRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_never_return_key_or_contact_provider(self):
        from aiohttp import web
        table=web.RouteTableDef()
        server=types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=table)))
        with patch.dict('sys.modules',server=server):support.routes.register_routes()
        handlers={r.method:r.handler for r in table if r.path=='/h3lv/rh/settings'}
        with tempfile.TemporaryDirectory() as tmp,patch.object(credentials,'settings_path',return_value=Path(tmp)/'key.json'),patch.dict('os.environ',{'RUNNINGHUB_API_KEY':''}),patch.object(transport.requests,'post') as network,patch.object(support.routes,'record_error'):
            request=types.SimpleNamespace(headers={'Origin':'http://127.0.0.1:8188'},host='127.0.0.1:8188',content_type='application/json',match_info={},query={'base_url':AI},json=AsyncMock(return_value={'base_url':AI,'api_key':'fixture-private-ai'}))
            for method in ('POST','GET'):
                response=await handlers[method](request)
                self.assertEqual(response.status,200)
                self.assertEqual(json.loads(response.text),{'configured':True,'source':'local'})
                self.assertNotIn('private',response.text)
                self.assertEqual(response.headers['Cache-Control'],'no-store')
            request.headers['Origin']='https://untrusted.invalid'
            response=await handlers['POST'](request)
            self.assertEqual(response.status,400)
            request.json.assert_awaited_once()
            network.assert_not_called()
