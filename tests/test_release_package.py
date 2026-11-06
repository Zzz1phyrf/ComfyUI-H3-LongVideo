import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


class ReleasePackageTests(unittest.TestCase):
    def test_package_contains_runtime_two_examples_and_no_user_data(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('release_builder', root/'scripts/build_talking_rh_candidate.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)/'plugin.zip'
            builder.build(package)
            with zipfile.ZipFile(package) as archive:
                names = {name.removeprefix('ComfyUI-H3-LongVideo/') for name in archive.namelist()}
                self.assertIsNone(archive.testzip())
                self.assertTrue({'__init__.py','audio_preparation.py','diagnostics.py',
                                 'defaults/talking_rh_graph.json','talking_rh/flat.py','web/talking_rh.js'} <= names)
                self.assertEqual({name for name in names if name.startswith('examples/')}, {
                    'examples/MiniMaxH3MV数字人工作流 By 像素幻想Lab.json',
                    'examples/MiniMaxH3口播数字人工作流 By 像素幻想Lab.json'})
                for name in names:
                    self.assertNotIn(name.split('/')[0], {'input','output','tests','projects','runtime'})
                    self.assertNotIn(Path(name).suffix.lower(), {'.mp4','.wav','.png','.jpg','.pyc'})
                    self.assertEqual(archive.read('ComfyUI-H3-LongVideo/'+name), (root/name).read_bytes())


if __name__ == '__main__':
    unittest.main()
