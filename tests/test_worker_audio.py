"""The H3 worker must not send its WAV through incompatible PyAV decoding."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf


spec = importlib.util.spec_from_file_location('h3_worker_audio_test', Path(__file__).parents[1]/'worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerAudioTests(unittest.TestCase):
    def test_real_legacy_decoder_reproduces_removed_argument_but_worker_reads_wav(self):
        import av
        from faster_whisper.audio import decode_audio
        original = av.open
        def pyav19_open(*args, **kwargs):
            if 'metadata_errors' in kwargs:
                raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
            return original(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'voice.wav'
            sf.write(source, np.ones(24000, np.float32)*.1, 24000)
            with patch.object(av, 'open', side_effect=pyav19_open) as opened:
                with self.assertRaisesRegex(TypeError, 'metadata_errors'):
                    decode_audio(str(source))
                opened.reset_mock()
                self.assertEqual(worker.load_audio(source).shape, (16000,))
                opened.assert_not_called()
        self.assertIs(av.open, original)

    def test_pyav19_path_decoder_is_bypassed_in_cuda_and_cpu_worker(self):
        for device in ('cuda', 'cpu', 'auto'):
            with self.subTest(device=device), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                wave = np.column_stack((np.full(48000, .4, np.float32), np.zeros(48000, np.float32)))
                sf.write(root/'voice.wav', wave, 48000, subtype='FLOAT')
                captured = []
                class Model:
                    def transcribe(self, audio, **options):
                        if not isinstance(audio, np.ndarray):
                            # Exactly the old faster-whisper / PyAV 19 failure.
                            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
                        captured.append(audio)
                        return iter([]), types.SimpleNamespace(language='zh')
                factory = Mock(return_value=Model())
                if device == 'auto':
                    factory.side_effect = [RuntimeError('CUDA unavailable'), Model()]
                module = types.SimpleNamespace(WhisperModel=factory)
                argv = ['worker.py', '--model', str(root), '--download-root', str(root),
                        '--audio', str(root/'voice.wav'), '--output', str(root/'result.json'), '--device', device]
                with patch.dict(sys.modules, {'faster_whisper':module}), patch.object(sys, 'argv', argv), \
                     patch.object(worker, 'resolve_model_path', return_value=str(root)), \
                     patch.object(worker.site, 'getsitepackages', return_value=[]):
                    worker.main()
                self.assertEqual(len(captured), 1)
                self.assertEqual(captured[0].shape, (16000,))
                self.assertEqual(captured[0].dtype, np.float32)
                np.testing.assert_allclose(captured[0][100:-100], .2, atol=.001)
                self.assertTrue((root/'result.json').is_file())


if __name__ == '__main__':
    unittest.main()
