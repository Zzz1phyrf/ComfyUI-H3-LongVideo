"""Run Faster-Whisper in a short-lived ComfyUI-Python subprocess."""
import argparse
import json
import os
from pathlib import Path
import site


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--download-root", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="auto", choices=("auto", "cuda", "cpu"))
    args = parser.parse_args()
    download_root = Path(args.download_root)
    download_root.mkdir(parents=True, exist_ok=True)
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1")
    directories = [str(p) for root in site.getsitepackages() for name in ("cublas", "cudnn", "cuda_nvrtc")
                   if (p := Path(root)/"nvidia"/name/"bin").is_dir()]
    directories.extend(str(p) for root in site.getsitepackages()
                       if (p := Path(root)/"torch"/"lib").is_dir())
    os.environ["PATH"] = os.pathsep.join(directories+[os.environ.get("PATH", "")])
    handles = [os.add_dll_directory(p) for p in directories] if hasattr(os, "add_dll_directory") else []
    from faster_whisper import WhisperModel

    def transcribe(device):
        model = WhisperModel(args.model, device=device,
            compute_type="float16" if device == "cuda" else "int8",
            download_root=str(download_root))
        try:
            iterator, info = model.transcribe(args.audio, language="zh", beam_size=5,
                temperature=0.0, word_timestamps=True, vad_filter=False,
                condition_on_previous_text=False)
            segments = []
            for segment in iterator:
                segments.append({"start": segment.start, "end": segment.end, "text": segment.text,
                    "words": [{"word": word.word, "start": word.start, "end": word.end,
                               "probability": word.probability} for word in segment.words or []]})
            return {"segments": segments,
                    "words": [word for segment in segments for word in segment["words"]],
                    "language": info.language, "human_verified": False,
                    "asr_device": device, "model": args.model}
        finally:
            del model

    try:
        if args.device == "cpu":
            result = transcribe("cpu")
        elif args.device == "cuda":
            result = transcribe("cuda")
        else:
            try:
                result = transcribe("cuda")
            except Exception as exc:
                print(f"CUDA Faster-Whisper unavailable; retrying on CPU: {exc}", flush=True)
                result = transcribe("cpu")
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    main()
