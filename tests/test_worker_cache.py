import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("h3lv_worker_cache_test", ROOT / "worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class CacheMissError(Exception):
    pass


class WorkerCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "snapshots" / ("a" * 40)
        self.snapshot.mkdir(parents=True)
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"):
            (self.snapshot / name).write_text("{}", encoding="utf-8")
        self.download = Mock(return_value=str(self.snapshot))
        utils = types.ModuleType("faster_whisper.utils")
        utils.download_model = self.download
        hub_utils = types.ModuleType("huggingface_hub.utils")
        hub_utils.LocalEntryNotFoundError = CacheMissError
        self.modules = patch.dict(sys.modules, {
            "faster_whisper.utils": utils, "huggingface_hub.utils": hub_utils})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def resolve(self):
        return worker.resolve_model_path("large-v3-turbo", self.root)

    def test_complete_cache_uses_offline_lookup_only(self):
        self.assertEqual(self.resolve(), str(self.snapshot))
        self.download.assert_called_once_with(
            "large-v3-turbo", cache_dir=str(self.root), local_files_only=True)

    def test_missing_cache_downloads_once(self):
        self.download.side_effect = [CacheMissError(), str(self.snapshot)]
        self.assertEqual(self.resolve(), str(self.snapshot))
        self.assertEqual([c.kwargs["local_files_only"] for c in self.download.call_args_list],
                         [True, False])

    def test_partial_cache_repairs_missing_tokenizer(self):
        (self.snapshot / "tokenizer.json").unlink()
        def download(*args, **kwargs):
            if not kwargs["local_files_only"]:
                (self.snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
            return str(self.snapshot)
        self.download.side_effect = download
        self.assertEqual(self.resolve(), str(self.snapshot))
        self.assertEqual(self.download.call_count, 2)

    def test_empty_weights_remain_an_error_after_download(self):
        (self.snapshot / "model.bin").write_bytes(b"")
        with self.assertRaisesRegex(FileNotFoundError, "model.bin"):
            self.resolve()
        self.assertEqual(self.download.call_count, 2)

    def test_missing_vocabulary_triggers_repair(self):
        (self.snapshot / "vocabulary.json").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "vocabulary"):
            self.resolve()
        self.assertEqual(self.download.call_count, 2)

    def test_local_directory_bypasses_hub(self):
        self.assertEqual(worker.resolve_model_path(str(self.snapshot), self.root), str(self.snapshot))
        self.download.assert_not_called()

    def test_permission_error_does_not_trigger_download(self):
        self.download.side_effect = PermissionError("cache access denied")
        with self.assertRaises(PermissionError):
            self.resolve()
        self.assertEqual(self.download.call_count, 1)

    def test_download_failure_is_preserved(self):
        self.download.side_effect = [CacheMissError(), ConnectionError("offline")]
        with self.assertRaisesRegex(ConnectionError, "offline"):
            self.resolve()

    def test_worker_reuses_resolved_path_during_cpu_fallback(self):
        model = Mock()
        model.transcribe.return_value = (iter([]), types.SimpleNamespace(language="zh"))
        factory = Mock(side_effect=[RuntimeError("CUDA unavailable"), model])
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = factory
        argv = ["worker.py", "--model", "large-v3-turbo", "--download-root", str(self.root),
                "--audio", str(self.root / "audio.wav"), "--output", str(self.root / "result.json")]
        with patch.dict(sys.modules, {"faster_whisper": module}), \
                patch.object(sys, "argv", argv), \
                patch.object(worker.site, "getsitepackages", return_value=[]), \
                patch.dict(worker.os.environ, {}):
            worker.main()
        self.download.assert_called_once()
        self.assertEqual(factory.call_count, 2)
        for call in factory.call_args_list:
            self.assertEqual(call.args[0], str(self.snapshot))
            self.assertTrue(call.kwargs["local_files_only"])


if __name__ == "__main__":
    unittest.main()
