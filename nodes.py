import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from . import director_rules
from .core import (audio_file, decorate, fingerprint, project_path, read_plan, segmentation,
                   validate_segment_brief, write_plan)


def storage_root():
    import folder_paths
    return Path(folder_paths.get_output_directory()) / "H3LongVideo"


def data_root():
    return storage_root() / "projects"


def final_root():
    return storage_root() / "final_videos"


def user_data_root():
    import folder_paths
    get_user = getattr(folder_paths, "get_user_directory", None)
    root = Path(get_user()) if get_user else Path(folder_paths.base_path)/"user"/"default"
    return root/"H3LongVideo"


def rules_path():
    return user_data_root()/"rules"


def asr_model_root():
    import folder_paths
    root = Path(getattr(folder_paths, "models_dir", Path(folder_paths.base_path)/"models"))
    return root/"faster-whisper"


def audio_array(audio):
    import numpy as np
    x = audio["waveform"].detach().cpu().float()
    if x.ndim != 3 or x.shape[0] != 1:
        raise ValueError("请一次输入一首音频，不支持 AUDIO batch。")
    x = x[0].T.numpy().copy()
    if not len(x) or not np.isfinite(x).all():
        raise ValueError("音频为空或无效。")
    return x, int(audio["sample_rate"])


def separate(audio, sr):
    import numpy as np
    import torch
    from torchaudio.models import hdemucs_high
    from torchaudio.functional import resample
    import comfy.model_management as mm
    checkpoint = Path(torch.hub.get_dir())/"torchaudio/models/hdemucs_high_trained.pt"
    if not checkpoint.is_file():
        raise ValueError("没有找到缓存人声分离模型。请把已有分离节点的人声输出接到 vocals；插件不会自动下载模型。")
    device = mm.get_torch_device()
    x = torch.from_numpy(audio.T.copy())
    if x.shape[0] == 1:
        x = x.repeat(2, 1)
    if x.shape[0] != 2:
        raise ValueError("人声分离支持单声道或双声道。")
    if sr != 44100:
        x = resample(x, sr, 44100)
    model = hdemucs_high(sources=["drums", "bass", "other", "vocals"])
    model.load_state_dict(torch.load(checkpoint, weights_only=True, map_location="cpu"))
    model.eval().to(device)
    mean, std = x.mean(0).mean(), x.mean(0).std().clamp_min(1e-8)
    normalized = (x-mean)/std
    output, weights = torch.zeros_like(x), torch.zeros(x.shape[1])
    chunk, overlap = 8*44100, 44100
    try:
        with torch.inference_mode():
            for start in range(0, x.shape[1], chunk-overlap):
                mm.throw_exception_if_processing_interrupted()
                end = min(start+chunk, x.shape[1])
                value = model(normalized[:, start:end].unsqueeze(0).to(device))[0, 3].cpu()
                w = torch.ones(end-start)
                fade = min(overlap, len(w))
                if start:
                    w[:fade] *= torch.linspace(1/(fade+1), 1, fade)
                if end < x.shape[1]:
                    w[-fade:] *= torch.linspace(1, 1/(fade+1), fade)
                output[:, start:end] += value*w
                weights[start:end] += w
                if end == x.shape[1]:
                    break
        output = output/weights.clamp_min(1e-8)*std+mean
        if sr != 44100:
            output = resample(output, 44100, sr)
        output = output[:, :len(audio)]
        if output.shape[1] < len(audio):
            output = torch.nn.functional.pad(output, (0, len(audio)-output.shape[1]))
        return output.T.numpy().astype(np.float32)
    finally:
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def run_asr(project):
    import importlib.util
    import comfy.model_management as mm
    if importlib.util.find_spec("faster_whisper") is None:
        raise ValueError(
            "当前 ComfyUI Python 缺少 faster-whisper。请通过 ComfyUI Manager 重新安装本插件依赖，"
            "或在当前 ComfyUI Python 中安装 requirements.txt 后重启。")
    model_root = asr_model_root()
    model_root.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-B", str(Path(__file__).with_name("worker.py")),
               "--model", "large-v3-turbo", "--download-root", str(model_root),
               "--audio", str(audio_file(project, "vocals.wav")),
               "--output", str(project/"state"/"transcript.json"), "--device", "auto"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    with (project/"state"/"asr.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(command, stdout=log, stderr=log, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        try:
            deadline = time.monotonic()+3600
            while child.poll() is None:
                mm.throw_exception_if_processing_interrupted()
                if time.monotonic() > deadline:
                    raise TimeoutError("语音识别超过一小时，已停止本次分析进程。")
                time.sleep(.2)
        except BaseException:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            raise
    if child.returncode:
        raise RuntimeError(
            "语音识别失败。首次使用可能正在下载模型；请检查网络、磁盘空间和运行环境。"
            f"详细日志：{project/'state'/'asr.log'}")
    return json.loads((project/"state"/"transcript.json").read_text(encoding="utf-8"))


class Analyze:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"audio": ("AUDIO",), "mode": (["singing", "speaking"],),
            "max_seconds": ("FLOAT", {"default": 15, "min": 5, "max": 15, "step": .1}),
            "target_seconds": ("FLOAT", {"default": 11, "min": 5, "max": 15, "step": .1}),
            "asr_python": ("STRING", {"default": ""}), "asr_model": ("STRING", {"default": ""}),
            "asr_device": (["auto", "cuda", "cpu"],),
            "camera_activity": (["auto", "moderate", "dynamic"],),
            "widest_framing": (["medium close-up", "medium shot"],),
            # Hidden and ignored compatibility slot. Keeping its position prevents
            # older workflow widget values from shifting project_id/segment_index.
            "director_mode": ("STRING", {"default": "本地规则"}),
            }, "optional": {"vocals": ("AUDIO",)}}
    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("project_id", "segment_count")
    FUNCTION = "analyze"
    CATEGORY = "像素幻想/H3 长视频"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def analyze(self, audio, mode, max_seconds, target_seconds, asr_python, asr_model,
                asr_device="auto", camera_activity="auto", widest_framing="medium close-up",
                director_mode="本地规则", vocals=None):
        import soundfile as sf
        import numpy as np
        mix, sr = audio_array(audio)
        audio_seed = hashlib.sha256(np.ascontiguousarray(mix).tobytes()).hexdigest()[:16]
        rules = director_rules.read_rules(rules_path())
        if vocals is not None:
            voice, vsr = audio_array(vocals)
            if vsr != sr or len(voice) != len(mix):
                raise ValueError("原曲与人声必须采样率、样本数一致；请提供同版本未裁切的人声。")
        else:
            voice = separate(mix, sr) if mode=="singing" else mix.copy()
        project_id = uuid.uuid4().hex
        root = data_root()
        directory = project_path(root, project_id)
        for name in ("state", "audio", "takes", "cache", "work"):
            (directory/name).mkdir(parents=True, exist_ok=True)
        sf.write(audio_file(directory, "source.wav"), mix, sr, subtype="FLOAT")
        sf.write(audio_file(directory, "vocals.wav"), voice, sr, subtype="FLOAT")
        transcript = run_asr(directory)
        rows, analysis = segmentation(voice, sr, transcript, mode, float(max_seconds),
                                      float(target_seconds), mix_audio=mix, return_analysis=True)
        analysis_temp = directory/"state"/f".analysis-{uuid.uuid4().hex}.tmp"
        analysis_temp.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        analysis_temp.replace(directory/"state"/"analysis.json")
        rhythm = analysis.get("rhythm") or {}
        audio_structure = {
            "tempo_bpm": rhythm.get("tempo_bpm"),
            "tempo_confidence": rhythm.get("confidence"),
            "sections": list(analysis.get("sections") or [])[:32],
        }
        plan = {"id": project_id, "schema": 4, "analysis_schema": 2,
            "revision": 1, "created": time.time(),
            "mode": mode, "max_seconds": float(max_seconds), "target_seconds": float(target_seconds),
            "sample_rate": sr, "samples": len(mix), "duration": len(mix)/sr,
            "director": {"mode": "rule", "performance_intensity": "auto",
                         "camera_activity": camera_activity, "widest_framing": widest_framing,
                         "note": "", "rule_config": rules["config"],
                         "schedule_seed": audio_seed, "rule_revision": rules["revision"]},
            "audio_structure": audio_structure,
            "segments": rows, "approved": False,
            "run_status": "draft", "warnings": ["ASR 文字和时间戳未经校对；请试听风险切点。",
            "气口、拖音和无人声段是声学估计；无文字不代表无人声。"]}
        plan = decorate(plan)
        reconstructed = np.concatenate([mix[r["start_sample"]:r["end_sample"]] for r in plan["segments"]])
        if not np.array_equal(reconstructed, mix):
            raise AssertionError("音频覆盖检查失败。")
        plan["sample_coverage_verified"] = True
        write_plan(root, plan)
        return {"ui": {"h3lv_project": [project_id], "text": [f"分析完成：{len(rows)} 段。点击分段预览检查后生成。"]},
                "result": (project_id, len(rows))}


class LoadSegment:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"project_id": ("STRING", {"default": ""}),
                             "segment_index": ("INT", {"default": 0, "min": 0, "max": 10000})}}
    RETURN_TYPES = ("AUDIO", "AUDIO", "STRING", "INT", "STRING")
    RETURN_NAMES = ("original_audio_padded", "vocals_padded", "segment_brief", "generation_frames", "filename_prefix")
    FUNCTION = "load"
    CATEGORY = "像素幻想/H3 长视频"

    @classmethod
    def IS_CHANGED(cls, project_id, segment_index):
        try:
            plan = read_plan(data_root(), project_id)
            return fingerprint(plan)+str(plan.get("approved", False))
        except (ValueError, FileNotFoundError):
            return float("nan")

    def load(self, project_id, segment_index):
        import numpy as np
        import soundfile as sf
        import torch
        if not str(project_id).strip():
            raise ValueError("尚未分析音频。请先运行 H3 音频分析节点并确认分段。")
        plan = read_plan(data_root(), project_id)
        if not plan.get("approved"):
            raise ValueError("音频已经分析，但分段尚未确认。请先在分段审核界面点击“保存并确认”。")
        if not 0 <= segment_index < len(plan["segments"]):
            raise ValueError("分段编号超出范围。")
        row = plan["segments"][segment_index]
        validate_segment_brief(row.get("prompt", ""))
        directory = project_path(data_root(), project_id)
        outputs = []
        for name in ("source.wav", "vocals.wav"):
            audio, sr = sf.read(audio_file(directory, name), start=row["start_sample"], stop=row["end_sample"], dtype="float32", always_2d=True)
            target = math_ceil_samples(row["generation_frames"], sr)
            audio = np.pad(audio, ((0, max(0, target-len(audio))), (0, 0)))
            outputs.append({"waveform": torch.from_numpy(audio.T.copy()).unsqueeze(0), "sample_rate": sr})
        return (*outputs, row["prompt"], row["generation_frames"],
                f"H3LongVideo/projects/{project_id}/takes/seg_{segment_index:04d}")


class Unified:
    """Analyze on native partial execution; load one approved segment during controller runs."""
    @classmethod
    def INPUT_TYPES(cls):
        analyze = Analyze.INPUT_TYPES()
        required = dict(analyze["required"])
        required.update(project_id=("STRING", {"default": ""}),
                        segment_index=("INT", {"default": 0, "min": 0, "max": 10000}))
        return {"required": required, "optional": analyze.get("optional", {})}

    RETURN_TYPES = LoadSegment.RETURN_TYPES
    RETURN_NAMES = LoadSegment.RETURN_NAMES
    FUNCTION = "process"
    CATEGORY = "像素幻想/H3 长视频"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, project_id="", **kwargs):
        if str(project_id).strip():
            try:
                plan = read_plan(data_root(), project_id)
                if plan.get("approved"):
                    return fingerprint(plan)
            except (ValueError, FileNotFoundError):
                pass
        return float("nan")

    def process(self, audio, mode, max_seconds, target_seconds, asr_python, asr_model,
                asr_device="auto", camera_activity="auto", widest_framing="medium close-up",
                director_mode="本地规则", project_id="", segment_index=0, vocals=None):
        if str(project_id).strip():
            try:
                plan = read_plan(data_root(), project_id)
                if plan.get("approved"):
                    return LoadSegment().load(project_id, int(segment_index))
            except FileNotFoundError:
                pass
        analyzed = Analyze().analyze(audio, mode, max_seconds, target_seconds,
                                     asr_python, asr_model, asr_device, camera_activity,
                                     widest_framing, director_mode, vocals)
        # Native Run targets only this node. These placeholders are not sent downstream;
        # the approved controller run replaces them with the selected segment outputs.
        return {"ui": analyzed["ui"],
                "result": (audio, vocals or audio, "", 0, "")}


def math_ceil_samples(frames, sr):
    return (frames*sr+23)//24


NODE_CLASS_MAPPINGS = {"H3LVUnified": Unified}
NODE_DISPLAY_NAME_MAPPINGS = {"H3LVUnified": "H3 长视频 · 音频分析与顺序生成"}
