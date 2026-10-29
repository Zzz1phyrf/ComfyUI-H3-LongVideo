import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import test_plugin


remote = importlib.import_module('h3lv_test.talking_rh.remote')


class RemoteDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.target = self.directory/'video.latent'
        with patch.dict('os.environ', {'RUNNINGHUB_API_KEY': 'test-only-key'}):
            self.client = remote.Client({'base_url': 'https://www.runninghub.ai', 'workflow_id': '123', 'instance_type': 'ultra'})
        self.response = MagicMock()
        self.response.__enter__.return_value = self.response
        self.request = patch.object(remote.requests, 'get', return_value=self.response)
        self.get = self.request.start()
        self.addCleanup(self.request.stop)

    def test_repeated_connection_drops_remove_only_the_current_partial(self):
        unrelated = self.directory/'.old-download.part'
        unrelated.write_bytes(b'keep')
        def chunks(*args):
            yield b'partial latent'
            raise remote.requests.exceptions.ChunkedEncodingError('connection dropped')
        self.response.iter_content.side_effect = chunks
        for _ in range(2):
            with self.assertRaises(remote.RemoteError):
                self.client.download('https://example.test/latent', self.target)
            self.assertFalse(self.target.exists())
            self.assertEqual(list(self.directory.iterdir()), [unrelated])
        self.assertEqual(unrelated.read_bytes(), b'keep')

    def test_success_keeps_complete_bytes_and_no_partial(self):
        self.response.iter_content.return_value = [b'first', b'second']
        self.client.download('https://example.test/latent', self.target)
        self.assertEqual(self.target.read_bytes(), b'firstsecond')
        self.assertEqual(list(self.directory.iterdir()), [self.target])

    def test_existing_complete_output_is_not_touched(self):
        self.target.write_bytes(b'accepted')
        self.client.download('https://example.test/latent', self.target)
        self.get.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'accepted')

    def test_write_failure_removes_partial_and_preserves_original_error(self):
        original_open = Path.open
        def open_file(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            wrapped = MagicMock(wraps=stream)
            wrapped.__enter__.return_value = wrapped
            wrapped.__exit__.side_effect = lambda *args: stream.close()
            wrapped.write.side_effect = OSError('disk full')
            return wrapped
        self.response.iter_content.return_value = [b'partial']
        with patch.object(Path, 'open', new=open_file):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.client.download('https://example.test/latent', self.target)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_cleanup_permission_error_does_not_hide_the_download_error(self):
        def chunks(*args):
            yield b'partial'
            raise remote.requests.ConnectionError('connection dropped')
        self.response.iter_content.side_effect = chunks
        with patch.object(Path, 'unlink', side_effect=PermissionError('cleanup denied')):
            with self.assertLogs(level='WARNING'):
                with self.assertRaises(remote.RemoteError):
                    self.client.download('https://example.test/latent', self.target)

    def test_replace_failure_removes_partial_and_preserves_original_error(self):
        self.response.iter_content.return_value = [b'complete']
        with patch.object(Path, 'replace', side_effect=PermissionError('replace denied')):
            with self.assertRaisesRegex(PermissionError, 'replace denied'):
                self.client.download('https://example.test/latent', self.target)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_exclusive_open_collision_never_deletes_an_existing_file(self):
        partial = self.directory/'.video.latent.fixed.part'
        partial.write_bytes(b'another download')
        with patch.object(remote.uuid, 'uuid4', return_value=MagicMock(hex='fixed')):
            with self.assertRaises(FileExistsError):
                self.client.download('https://example.test/latent', self.target)
        self.assertEqual(partial.read_bytes(), b'another download')


if __name__ == '__main__':
    unittest.main()
