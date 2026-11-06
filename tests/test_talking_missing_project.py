import importlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from test_diagnostics import diagnostics

talk=importlib.import_module('h3lv_test.talking_rh.routes')


class MissingProjectEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_id_is_explicitly_missing_but_corrupt_plan_is_not(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer
        pid='9d01d98fc21743b9be70acc224239155'
        with tempfile.TemporaryDirectory() as directory:
            server=types.SimpleNamespace(routes=web.RouteTableDef())
            with patch.dict('sys.modules',{'server':types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=server)),
                   'folder_paths':types.SimpleNamespace(get_output_directory=lambda:directory)}):
                talk.register();app=web.Application();app.add_routes(server.routes)
                async with TestClient(TestServer(app)) as client:
                    response=await client.get('/h3talk/project/'+pid)
                    self.assertEqual(response.status,404)
                    self.assertEqual((await response.json())['code'],'PROJECT_NOT_FOUND')
                    self.assertFalse((Path(directory)/'H3LongVideo/口播/logs/errors.log').exists())
                    plan=Path(directory)/'H3LongVideo/口播/projects'/pid/'state/segments.json'
                    plan.parent.mkdir(parents=True);plan.write_text('{broken')
                    response=await client.get('/h3talk/project/'+pid)
                    self.assertEqual(response.status,400)
                    self.assertNotEqual((await response.json()).get('code'),'PROJECT_NOT_FOUND')
                    self.assertEqual(plan.read_text(),'{broken')


if __name__=='__main__':unittest.main()
