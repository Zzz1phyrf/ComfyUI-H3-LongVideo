import ast
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('h3lv_candidate_builder', ROOT/'scripts/build_talking_rh_candidate.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class CandidatePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.result = builder.build(cls.directory/'candidate.zip')

    def test_archive_contains_current_runtime_examples_and_docs(self):
        required = {
            'FRIEND_TESTING.md','talking_rh/management.py','storage.py','audio_preparation.py',
            'scripts/migrate_talking_resolution.py','scripts/migrate_talking_sampler.py',
            'rh_credentials.py', 'web/rh_settings.js', 'web/widget_visibility.js','web/node_ui.js',
            'mv_rh.py', 'MV_RUNNINGHUB.md', 'TALKING_CANVAS.md',
            'examples/06_H3口播_本地完整工作流.json',
            'examples/07_H3MV_本地与RH.json',
            'examples/08_H3MV_RH云端生成模板.json',
            'examples/09_H3口播_Ref2VA本地展开.json',
            'examples/10_H3口播_Ref2VA标准K本地展开.json',
            'examples/11_H3口播_Ref2VA标准K_RH云端模板.json',
            'talking_rh/prefix.py',
            'talking_rh/overlap.py','TALKING_OVERLAP.md',
            'examples/13_H3口播_独立窗口_本地展开.json',
            'examples/14_H3口播_独立窗口_RH控制.json',
            'examples/15_H3口播_独立窗口_RH云端模板.json',
        }
        with zipfile.ZipFile(self.result['path']) as archive:
            names = {name.removeprefix('ComfyUI-H3-LongVideo/') for name in archive.namelist()}
        self.assertFalse(required-names, f'Missing package files: {sorted(required-names)}')
        self.assertFalse(any(name.startswith(('tests/', 'experiments/')) or '__pycache__' in name for name in names))

    def test_archive_resolves_every_local_python_import(self):
        with zipfile.ZipFile(self.result['path']) as archive:
            prefix = 'ComfyUI-H3-LongVideo/'
            files = {name.removeprefix(prefix): archive.read(name) for name in archive.namelist()}
        for name, content in files.items():
            if not name.endswith('.py'):
                continue
            parents = Path(name).parts[:-1]
            for node in ast.walk(ast.parse(content, filename=name)):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                base = list(parents[:len(parents)-node.level+1])
                modules = [base+node.module.split('.')] if node.module else [base+[alias.name] for alias in node.names]
                for module in modules:
                    stem = '/'.join(module)
                    candidates = (stem+'.py', stem+'/__init__.py')
                    if any((ROOT/path).is_file() for path in candidates):
                        self.assertTrue(any(path in files for path in candidates), f'{name}:{node.lineno}: missing {stem}')

    def test_extracted_plugin_imports_and_registers_nodes_and_routes(self):
        from aiohttp import web
        routes = web.RouteTableDef()
        server = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=routes)))
        with zipfile.ZipFile(self.result['path']) as archive:
            archive.extractall(self.directory/'unpacked')
        package = self.directory/'unpacked/ComfyUI-H3-LongVideo'
        name = 'h3lv_packaged_import_test'
        spec = importlib.util.spec_from_file_location(name, package/'__init__.py', submodule_search_locations=[str(package)])
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'server': server, name: module}):
            spec.loader.exec_module(module)
            self.assertTrue({'H3LVUnified', 'H3LVPromptExpand', 'H3LVTalkingAvatar', 'H3LVTalkingRH', 'H3LVTalkingMerge', 'H3LVTalkingFrozenPrefix'}.issubset(module.NODE_CLASS_MAPPINGS))
        paths = {route.path for route in routes}
        self.assertIn('/h3lv/project/{project_id}/run', paths)
        self.assertIn('/h3talk/project/{pid}/{action}', paths)
        self.assertIn('/h3talk/projects/manage',paths)
        self.assertIn('/h3talk/projects/delete',paths)
        self.assertIn('/h3talk/project/{pid}/reveal-final',paths)


if __name__ == '__main__':
    unittest.main()
