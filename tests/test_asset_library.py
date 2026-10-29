import concurrent.futures
import hashlib
import importlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('h3lv_test')
package.__path__ = [str(ROOT)]
sys.modules.setdefault('h3lv_test', package)
library = importlib.import_module('h3lv_test.asset_library')


def picture(color=(90, 40, 20), image_format='PNG', size=(20, 10)):
    output = io.BytesIO()
    Image.new('RGB', size, color).save(output, format=image_format)
    return output.getvalue()


class AssetLibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory/'library'

    def upload(self, **kwargs):
        return library.upload(self.root, kwargs.pop('filename', '角色.png'),
                              kwargs.pop('content', picture()), **kwargs)

    def project(self, name):
        path = self.directory/name
        path.mkdir()
        return path

    def test_empty_library_read_has_no_filesystem_side_effect(self):
        self.assertEqual(library.list_assets(self.root), {'revision': 0, 'assets': []})
        self.assertFalse(self.root.exists())

    def test_upload_preserves_original_and_builds_a_separate_thumbnail(self):
        content = picture(image_format='JPEG', size=(512, 200))
        result = self.upload(filename='原始照片.jpg', content=content,
                             name='角色 A', description='舞台主唱', tags=['演唱', '演唱', ' 正面 '])
        asset = result['asset']
        self.assertEqual(result['revision'], 1)
        self.assertEqual(asset['tags'], ['演唱', '正面'])
        self.assertEqual(asset['sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(asset['original_filename'], '原始照片.jpg')
        self.assertTrue(asset['created_at'].endswith('+08:00'))
        self.assertIsNone(asset['deleted_at'])
        original = library.asset_path(self.root, asset['id'])
        self.assertEqual(original.read_bytes(), content)
        self.assertEqual(original.suffix, '.jpg')
        thumbnail = library.asset_path(self.root, asset['id'], thumbnail=True)
        with Image.open(thumbnail) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.size, (256, 100))
        self.assertNotEqual(original, thumbnail)

    def test_metadata_search_filter_and_restore(self):
        character = self.upload(name='Singer A', description='主唱人物', tags=['蓝衣'])['asset']
        scene = self.upload(name='海边', category='scene', description='夕阳与海浪')['asset']
        self.assertEqual(library.list_assets(self.root, query='singer')['assets'][0]['id'], character['id'])
        self.assertEqual(library.list_assets(self.root, query='蓝衣')['assets'][0]['id'], character['id'])
        self.assertEqual(library.list_assets(self.root, query='海浪')['assets'][0]['id'], scene['id'])
        self.assertEqual(len(library.list_assets(self.root, category='scene')['assets']), 1)
        deleted = library.set_deleted(self.root, character['id'], expected_revision=2)
        self.assertEqual(deleted['revision'], 3)
        self.assertIsNotNone(deleted['asset']['deleted_at'])
        self.assertEqual(len(library.list_assets(self.root)['assets']), 1)
        self.assertEqual(len(library.list_assets(self.root, include_deleted=True)['assets']), 2)
        self.assertTrue(library.asset_path(self.root, character['id'], thumbnail=True).is_file())
        restored = library.set_deleted(self.root, character['id'], False, expected_revision=3)
        self.assertEqual(restored['revision'], 4)
        self.assertIsNone(restored['asset']['deleted_at'])

    def test_new_image_version_preserves_metadata_and_old_original(self):
        first = self.upload(name='侧脸', category='scene', description='旧说明', tags=['收藏'])['asset']
        first_path = library.asset_path(self.root, first['id'], 1)
        first_content = first_path.read_bytes()
        second = self.upload(asset_id=first['id'], content=picture((20, 40, 90)), expected_revision=1)['asset']
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(second['version'], 2)
        self.assertEqual(len(second['versions']), 2)
        for field in ('name', 'category', 'description', 'tags', 'created_at'):
            self.assertEqual(second[field], first[field])
        self.assertEqual(first_path.read_bytes(), first_content)
        self.assertNotEqual(library.asset_path(self.root, first['id']).read_bytes(), first_content)
        self.assertEqual(library.asset_path(self.root, first['id'], '1'), first_path)
        with self.assertRaises(FileNotFoundError):
            library.asset_path(self.root, first['id'], 3)

    def test_duplicate_content_reuses_blob_without_merging_semantic_entries(self):
        first = self.upload(name='角色 A')['asset']
        second = self.upload(name='角色 B')['asset']
        self.assertNotEqual(first['id'], second['id'])
        self.assertIn(first['id'], second['duplicate_ids'])
        self.assertEqual(library.asset_path(self.root, first['id']), library.asset_path(self.root, second['id']))
        self.assertEqual(len(list((self.root/'blobs').iterdir())), 1)
        self.assertEqual(len(library.list_assets(self.root)['assets']), 2)

    def test_project_imports_are_ordered_independent_snapshots(self):
        red = self.upload(name='角色 A', content=picture((255, 0, 0)))['asset']
        blue = self.upload(name='场景 B', category='scene', content=picture((0, 0, 255)))['asset']
        selection = [{'asset_id': blue['id'], 'version': 1}, {'asset_id': red['id'], 'version': 1}]
        project_a = self.project('project_a')
        project_b = self.project('project_b')
        segments = b'{"revision": 7, "segments": []}'
        (project_b/'segments.json').write_bytes(segments)
        result_a = library.import_assets(self.root, project_a, selection)
        result_b = library.import_assets(self.root, project_b, selection)
        self.assertEqual([result_b['origins'][name]['asset_id'] for name in result_b['refs']], [blue['id'], red['id']])
        for directory, result in ((project_a, result_a), (project_b, result_b)):
            colors = []
            for name in result['refs']:
                with Image.open(directory/'refs'/name) as image:
                    self.assertEqual(image.format, 'PNG')
                    colors.append(image.getpixel((0, 0)))
            self.assertEqual(colors, [(0, 0, 255), (255, 0, 0)])
        previous = {name: (project_b/'refs'/name).read_bytes() for name in result_b['refs']}
        library.edit_asset(self.root, red['id'], name='改名', description='新说明')
        self.upload(asset_id=red['id'], content=picture((0, 255, 0)))
        library.set_deleted(self.root, red['id'])
        shutil.rmtree(project_a)
        self.assertEqual((project_b/'segments.json').read_bytes(), segments)
        self.assertEqual({name: (project_b/'refs'/name).read_bytes() for name in result_b['refs']}, previous)
        saved_sources = json.loads((project_b/'state'/'library_sources.json').read_text(encoding='utf-8'))
        self.assertEqual(saved_sources['origins'], result_b['origins'])
        old_source = result_b['origins'][result_b['refs'][1]]
        self.assertEqual(old_source['name_at_import'], '角色 A')
        self.assertEqual(old_source['version'], 1)

    def test_import_old_and_new_versions_and_accumulate_provenance(self):
        first = self.upload()['asset']
        self.upload(asset_id=first['id'], content=picture((1, 2, 3)))
        project = self.project('project')
        result = library.import_assets(self.root, project, [{'asset_id': first['id'], 'version': 2},
                                                          {'asset_id': first['id'], 'version': 1}])
        self.assertEqual([result['origins'][name]['version'] for name in result['refs']], [2, 1])
        extra = library.import_assets(self.root, project, [{'asset_id': first['id'], 'version': 1}])
        saved = json.loads((project/'state'/'library_sources.json').read_text(encoding='utf-8'))
        self.assertEqual(set(saved['origins']), set(result['refs'] + extra['refs']))
        self.assertEqual(library.list_assets(self.root)['revision'], 2)

    def test_import_limits_duplicates_and_deleted_assets_before_writing(self):
        asset = self.upload()['asset']
        selection = {'asset_id': asset['id'], 'version': 1}
        project = self.project('project')
        with self.assertRaisesRegex(ValueError, '最多 6'):
            library.import_assets(self.root, project, [selection] * 7)
        with self.assertRaisesRegex(ValueError, '不能重复'):
            library.import_assets(self.root, project, [selection, selection])
        library.set_deleted(self.root, asset['id'])
        with self.assertRaisesRegex(ValueError, '回收站'):
            library.import_assets(self.root, project, [selection])
        self.assertFalse((project/'refs').exists())
        with self.assertRaisesRegex(ValueError, '回收站'):
            self.upload(asset_id=asset['id'])

    def test_library_can_hold_more_than_six_assets_and_import_exactly_six(self):
        assets = [self.upload(name=f'角色 {number}')['asset'] for number in range(8)]
        self.assertEqual(len(library.list_assets(self.root)['assets']), 8)
        result = library.import_assets(self.root, self.project('project'),
                                       [{'asset_id': asset['id'], 'version': 1} for asset in assets[:6]])
        self.assertEqual(len(result['refs']), 6)

    def test_metadata_concurrency_preserves_independent_edits(self):
        asset = self.upload()['asset']
        barrier = threading.Barrier(3)

        def update(**fields):
            barrier.wait()
            return library.edit_asset(self.root, asset['id'], **fields)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            name = pool.submit(update, name='新名称')
            description = pool.submit(update, description='新说明')
            barrier.wait()
            name.result()
            description.result()
        saved = library.list_assets(self.root)
        self.assertEqual(saved['revision'], 3)
        self.assertEqual(saved['assets'][0]['name'], '新名称')
        self.assertEqual(saved['assets'][0]['description'], '新说明')

    def test_concurrent_expected_revision_allows_exactly_one_writer(self):
        asset = self.upload()['asset']
        barrier = threading.Barrier(3)

        def update(name):
            barrier.wait()
            try:
                library.edit_asset(self.root, asset['id'], expected_revision=1, name=name)
                return True
            except ValueError as error:
                self.assertIn('其他窗口修改', str(error))
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(update, name) for name in ['名称 A', '名称 B']]
            barrier.wait()
            self.assertEqual(sorted(result.result() for result in results), [False, True])
        self.assertEqual(library.list_assets(self.root)['revision'], 2)

    def test_noop_metadata_and_recycle_edits_keep_revision(self):
        asset = self.upload(name='角色')['asset']
        self.assertEqual(library.edit_asset(self.root, asset['id'], name='角色')['revision'], 1)
        self.assertEqual(library.set_deleted(self.root, asset['id'], False)['revision'], 1)
        with self.assertRaisesRegex(ValueError, '其他窗口修改'):
            library.edit_asset(self.root, asset['id'], expected_revision=0, name='改名')
        with self.assertRaisesRegex(ValueError, '其他窗口修改'):
            self.upload(asset_id=asset['id'], expected_revision=0)

    def test_invalid_images_and_paths_do_not_create_library_files(self):
        cases = [('fake.png', b'not an image'), ('empty.png', b''), ('image.svg', b'<svg/>'),
                 ('../image.png', picture()), ('folder\\image.png', picture()), ('C:foo.png', picture())]
        for filename, content in cases:
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.upload(filename=filename, content=content)
        with patch.object(library, 'MAX_REFERENCE_BYTES', 4), self.assertRaisesRegex(ValueError, '32 MiB'):
            self.upload(content=b'12345')
        self.assertFalse(self.root.exists())

    def test_invalid_ids_versions_and_metadata_are_rejected(self):
        asset = self.upload()['asset']
        for identifier in ['../index.json', 'a' * 31, 'A' * 32, None]:
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                library.asset_path(self.root, identifier)
        for version in [0, -1, '../1', True, 1.0]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                library.asset_path(self.root, asset['id'], version)
        for fields in [{'category': 'video'}, {'tags': '字符串'}, {'name': ''}, {'sha256': 'x'}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                library.edit_asset(self.root, asset['id'], **fields)
        with self.assertRaises(ValueError):
            library.edit_asset(self.root, asset['id'], expected_revision=True, name='合法名称')
        self.assertEqual(library.list_assets(self.root)['revision'], 1)

    def test_index_cannot_redirect_image_read_outside_library(self):
        asset = self.upload()['asset']
        path = self.root/'index.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        value['assets'][asset['id']]['versions'][0]['sha256'] = '../outside'
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '哈希无效'):
            library.asset_path(self.root, asset['id'])

    def test_thumbnail_rebuild_does_not_change_original_or_revision(self):
        asset = self.upload()['asset']
        original = library.asset_path(self.root, asset['id']).read_bytes()
        thumbnail = library.asset_path(self.root, asset['id'], thumbnail=True)
        thumbnail.unlink()
        self.assertEqual(library.asset_path(self.root, asset['id'], thumbnail=True), thumbnail)
        self.assertTrue(thumbnail.exists())
        self.assertEqual(library.asset_path(self.root, asset['id']).read_bytes(), original)
        self.assertEqual(library.list_assets(self.root)['revision'], 1)

    def test_import_detects_changed_original_before_creating_refs(self):
        asset = self.upload()['asset']
        library.asset_path(self.root, asset['id']).write_bytes(picture((3, 2, 1)))
        project = self.project('project')
        with self.assertRaisesRegex(ValueError, '原图已改变'):
            library.import_assets(self.root, project, [{'asset_id': asset['id'], 'version': 1}])
        self.assertFalse((project/'refs').exists())

    def test_import_rejects_project_inside_library(self):
        asset = self.upload()['asset']
        with self.assertRaisesRegex(ValueError, '相互独立'):
            library.import_assets(self.root, self.root, [{'asset_id': asset['id'], 'version': 1}])

    def test_project_refs_symlink_cannot_escape_project(self):
        asset = self.upload()['asset']
        project = self.project('project')
        outside = self.project('outside')
        link = project/'refs'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f'当前 Windows 账户不能创建符号链接：{error}')
        self.addCleanup(link.unlink)
        with self.assertRaisesRegex(ValueError, '不属于当前项目'):
            library.import_assets(self.root, project, [{'asset_id': asset['id'], 'version': 1}])
        self.assertEqual(list(outside.iterdir()), [])

    def test_library_blob_symlink_cannot_escape_library(self):
        asset = self.upload()['asset']
        original = library.asset_path(self.root, asset['id'])
        outside = self.directory/'outside.png'
        outside.write_bytes(original.read_bytes())
        original.unlink()
        try:
            original.symlink_to(outside)
        except OSError as error:
            self.skipTest(f'当前 Windows 账户不能创建符号链接：{error}')
        self.addCleanup(original.unlink)
        with self.assertRaisesRegex(ValueError, '不属于当前项目'):
            library.asset_path(self.root, asset['id'])

    def test_import_provenance_write_failure_removes_only_new_snapshots(self):
        asset = self.upload()['asset']
        project = self.project('project')
        first = library.import_assets(self.root, project, [{'asset_id': asset['id'], 'version': 1}])
        source = project/'state'/'library_sources.json'
        previous = source.read_bytes()
        replace = library.os.replace

        def fail_sources(temporary, destination):
            if Path(destination).name == 'library_sources.json':
                raise OSError('disk unavailable')
            replace(temporary, destination)

        with patch.object(library.os, 'replace', side_effect=fail_sources):
            with self.assertRaises(OSError):
                library.import_assets(self.root, project, [{'asset_id': asset['id'], 'version': 1}])
        self.assertEqual(source.read_bytes(), previous)
        self.assertEqual([path.name for path in (project/'refs').iterdir()], first['refs'])
        self.assertTrue((project/'refs'/first['refs'][0]).is_file())

    def test_failed_atomic_index_write_keeps_previous_revision(self):
        asset = self.upload()['asset']
        with patch.object(library.os, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                library.edit_asset(self.root, asset['id'], name='不能保存')
        saved = library.list_assets(self.root)
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(saved['assets'][0]['name'], asset['name'])
        self.assertEqual(list(self.root.glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
