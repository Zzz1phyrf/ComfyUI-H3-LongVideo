"""Run Faster-Whisper in a short-lived ComfyUI-Python subprocess."""
import argparse
import json
import os
from pathlib import Path
import site


def missing_model_files(directory):
    """Check required local assets, including the tokenizer to avoid a hidden download."""
    root = Path(directory)
    def present(name):
        path = root / name
        return path.is_file() and path.stat().st_size > 0
    missing = [name for name in ("model.bin", "config.json", "tokenizer.json")
               if not present(name)]
    if not any(present(name) for name in ("vocabulary.json", "vocabulary.txt")):
        missing.append("vocabulary.json or vocabulary.txt")
    return missing


def resolve_model_path(model, download_root):
    """Resolve a complete cached snapshot offline; download only absent assets."""
    if Path(model).is_dir():
        path = model
    else:
        from faster_whisper.utils import download_model
        from huggingface_hub.utils import LocalEntryNotFoundError
        try:
            path = download_model(model, cache_dir=str(download_root), local_files_only=True)
        except LocalEntryNotFoundError:
            path = None
        if path is None or missing_model_files(path):
            print("Faster-Whisper cache missing or incomplete; downloading model assets.", flush=True)
            path = download_model(model, cache_dir=str(download_root), local_files_only=False)
    missing = missing_model_files(path)
    if missing:
        raise FileNotFoundError("Incomplete Faster-Whisper model: " + ", ".join(missing))
    return str(path)


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
        model = WhisperModel(model_path, device=device,
            compute_type="float16" if device == "cuda" else "int8",
            download_root=str(download_root), local_files_only=True)
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
        model_path = resolve_model_path(args.model, download_root)
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
