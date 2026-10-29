import importlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import test_plugin as mv
import test_talking_rh as talking


class UnifiedStorageTests(unittest.TestCase):
    def test_both_kinds_share_the_selected_output_folder(self):
        node = importlib.import_module('h3talk_test.talking_rh')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.dict('sys.modules', folder_paths=types.SimpleNamespace(get_output_directory=lambda: directory)):
                self.assertEqual(mv.nodes.storage_root(), output/'H3LongVideo/MV')
                self.assertEqual(mv.nodes.data_root(), output/'H3LongVideo/MV/projects')
                self.assertEqual(mv.nodes.final_root(), output/'H3LongVideo/MV/final_videos')
                self.assertEqual(node.root(), output/'H3LongVideo/口播/projects')

    def test_native_talking_outputs_stay_inside_the_managed_project(self):
        import json
        canvas = importlib.import_module('h3talk_test.talking_rh.canvas')
        source = json.loads((Path(__file__).parent/'fixtures/talking_canvas_api.json').read_text(encoding='utf-8'))
        saved = canvas.capture(source, '5')
        plan = {'id': 'a'*32, 'assets': [{}, {}], 'config': {'execution_mode': 'canvas', 'canvas': saved}}
        queued = canvas.build(plan, {'index': 1}, {'attempt': 'b'*12})
        expected = 'H3LongVideo/口播/projects/'+'a'*32+'/takes/0001/'+'b'*12+'/generated/'
        for key in canvas.output_ids(plan['config']).values():
            self.assertTrue(queued[key]['inputs']['filename_prefix'].startswith(expected))

    def test_preview_paths_are_relative_to_comfy_output_not_the_category(self):
        storage = importlib.import_module('h3lv_test.storage')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for kind in ('MV', '口播'):
                projects = output/'H3LongVideo'/kind/'projects'
                final = projects.parent/'final_videos/test.mp4'
                final.parent.mkdir(parents=True)
                final.touch()
                preview = mv.core.output_preview(storage.comfy_output(projects), final)
                self.assertEqual(preview['subfolder'], f'H3LongVideo/{kind}/final_videos')

    def test_local_receipts_use_the_talking_bucket(self):
        local = importlib.import_module('h3talk_test.talking_rh.local')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'output'
            client = local.LocalClient({}, server=types.SimpleNamespace(),
                                       input_root=Path(directory)/'input', output_root=output)
            self.assertEqual(client.records, output/'H3LongVideo/口播/local_tasks')


if __name__ == '__main__':
    unittest.main()
