"""Host-independent segmentation, camera scaffolds and project persistence."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import uuid

import numpy as np

from .director_rules import default_config, validate_config

LOCK = threading.RLock()


def project_path(root, project_id):
    if not re.fullmatch(r"[0-9a-f]{32}", str(project_id)):
        raise ValueError("项目编号无效，请先运行音频分析节点。")
    root = Path(root).resolve()
    result = (root / project_id).resolve()
    if result.parent != root:
        raise ValueError("项目路径越界。")
    return result


def inside(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("文件不属于当前项目。")
    return path


def output_preview(output_root, path, fps=24):
    """Describe an output file in the format consumed by VHS video previews."""
    if not path:
        return None
    output_root = Path(output_root).resolve()
    path = inside(output_root, path)
    if not path.is_file():
        return None
    relative = path.relative_to(output_root)
    subfolder = "" if relative.parent == Path(".") else relative.parent.as_posix()
    return {"filename": path.name, "subfolder": subfolder, "type": "output",
            "format": "video/h264-mp4", "frame_rate": float(fps), "fullpath": str(path)}


def state_file(directory, name):
    if Path(name).name != str(name):
        raise ValueError("项目状态文件名无效。")
    return Path(directory) / "state" / str(name)


def audio_file(directory, name):
    if Path(name).name != str(name):
        raise ValueError("项目音频文件名无效。")
    return Path(directory) / "audio" / str(name)


def read_plan(root, project_id):
    with LOCK:
        return json.loads(state_file(project_path(root, project_id), "segments.json").read_text(encoding="utf-8"))


def read_project_transcript(directory, plan):
    """Read ASR data, reconstructing enough text timing for legacy project review."""
    path = state_file(directory, "transcript.json")
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8")), False
        except (OSError, json.JSONDecodeError):
            pass
    segments = [{"start": float(row["start"]), "end": float(row["end"]),
                 "text": str(row.get("text", "")).strip()}
                for row in plan.get("segments", []) if str(row.get("text", "")).strip()]
    if not segments:
        raise ValueError("这个旧项目缺少语音识别缓存，也没有可恢复的分段文本。请重新运行音频分析。")
    return {"segments": segments, "words": []}, True


def write_plan(root, plan):
    with LOCK:
        directory = project_path(root, plan["id"])
        state = directory / "state"
        state.mkdir(parents=True, exist_ok=True)
        temp = state / f".segments-{uuid.uuid4().hex}.tmp"
        temp.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(state / "segments.json")


def frames_for(seconds, edit_frames):
    return 5 + 17 * math.ceil((max(124, math.ceil(seconds * 24), edit_frames) - 5) / 17)


def preview_bounds(plan, index, start=None, end=None, boundary=False):
    """Resolve a bounded, unsaved segment preview without changing the plan."""
    row = plan["segments"][int(index)]
    sr = int(plan["sample_rate"])
    if (start is None) != (end is None):
        raise ValueError("试听起止时间必须同时提供。")
    if start is None:
        a, b = int(row["start_sample"]), int(row["end_sample"])
    else:
        start, end = float(start), float(end)
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("试听时间无效。")
        a, b = round(start*sr), round(end*sr)
        if a < 0 or b > int(plan["samples"]) or b <= a:
            raise ValueError("试听范围必须位于原曲内，且结束时间晚于开始时间。")
        if b-a > round((float(plan["max_seconds"])+1/sr)*sr):
            raise ValueError("试听片段超过最长分段时长。")
    if boundary:
        a, b = max(0, b-2*sr), min(int(plan["samples"]), b+2*sr)
    return a, b


def envelope(audio, sr):
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError("音频为空或含无效数值。")
    if audio.ndim == 1:
        audio = audio[:, None]
    hop = max(1, round(sr * .02))
    power = np.mean(np.square(audio.astype(np.float64)), axis=1)
    starts = np.arange(0, len(audio), hop)
    blocks = np.add.reduceat(power, starts) / np.minimum(hop, len(audio) - starts)
    smooth = np.convolve(np.pad(blocks, (2, 2), mode="edge"), np.ones(5) / 5, mode="valid")
    return starts, 10 * np.log10(np.maximum(smooth, 1e-12))


def _mono(audio):
    return audio.astype(np.float64) if audio.ndim == 1 else np.mean(audio.astype(np.float64), axis=1)


def waveform_peaks(audio, bins=1200):
    mono = np.abs(_mono(audio))
    if not len(mono):
        return []
    edges = np.linspace(0, len(mono), min(bins, len(mono)) + 1, dtype=int)
    peaks = [float(np.max(mono[edges[i]:edges[i+1]])) for i in range(len(edges)-1)]
    scale = np.percentile(peaks, 99) if peaks else 1
    return [round(min(1, value/max(scale, 1e-9)), 4) for value in peaks]


def zero_crossing(audio, starts, sr):
    mono = _mono(audio)
    signs = mono >= 0
    changes = np.r_[0, signs[1:] != signs[:-1]].astype(np.int32)
    prefix = np.r_[0, np.cumsum(changes)]
    width = max(1, round(sr*.04))
    ends = np.minimum(len(mono), starts + width)
    return (prefix[ends]-prefix[starts]) / np.maximum(1, ends-starts)


def rhythm_analysis(audio, sr, hop_seconds=.02):
    """Return an approximate beat grid used only after vocal-safety scoring."""
    mono = _mono(audio)
    hop, width = max(1, round(sr*hop_seconds)), max(32, round(sr*.04))
    window = np.hanning(width)
    flux, previous = [], None
    for start in range(0, max(1, len(mono)-width+1), hop):
        frame = mono[start:start+width]
        if len(frame) < width:
            frame = np.pad(frame, (0, width-len(frame)))
        magnitude = np.abs(np.fft.rfft(frame*window))
        flux.append(0 if previous is None else float(np.maximum(0, magnitude-previous).sum()))
        previous = magnitude
    flux = np.asarray(flux, dtype=np.float64)
    if len(flux) < 80 or np.max(flux) <= 0:
        return {"tempo_bpm": None, "confidence": 0, "beats": [], "bars": []}
    flux /= np.percentile(flux, 95) or 1
    flux = np.maximum(0, flux-np.median(flux))
    min_lag = max(1, round(60/180/hop_seconds))
    max_lag = min(len(flux)//3, round(60/60/hop_seconds))
    scores = np.asarray([np.dot(flux[lag:], flux[:-lag]) for lag in range(min_lag, max_lag+1)])
    if not len(scores) or np.max(scores) <= 0:
        return {"tempo_bpm": None, "confidence": 0, "beats": [], "bars": []}
    lag = int(np.argmax(scores))+min_lag
    period = lag*hop_seconds
    anchor_index = int(np.argmax(flux[:min(len(flux), max(lag*4, 1))]))
    anchor = anchor_index*hop_seconds
    duration = len(audio)/sr
    first = anchor-math.ceil(anchor/period)*period
    beats = np.arange(first, duration+.5*period, period)
    beats = beats[(beats >= 0) & (beats <= duration)]
    sampled = [flux[min(len(flux)-1, round(t/hop_seconds))] for t in beats]
    phase = max(range(min(4, len(sampled))), key=lambda p: sum(sampled[p::4]), default=0)
    bars = beats[phase::4]
    confidence = float(np.clip(np.max(scores)/(np.mean(scores)+1e-9)/4, 0, 1))
    return {"tempo_bpm": round(60/period, 2), "confidence": round(confidence, 3),
            "beats": [round(float(t), 3) for t in beats], "bars": [round(float(t), 3) for t in bars]}


def _sections(lines, starts, active, sr, duration, minimum=2):
    recognized = np.zeros(len(starts), dtype=bool)
    for line in lines:
        recognized |= (starts/sr < line["end"]) & ((starts+.02*sr)/sr > line["start"])
    quiet_text = ~recognized & ~active
    sections, i = [], 0
    while i < len(quiet_text):
        if not quiet_text[i]:
            i += 1
            continue
        j = i+1
        while j < len(quiet_text) and quiet_text[j]:
            j += 1
        a, b = starts[i]/sr, min((starts[j-1]/sr)+.02, duration)
        if b-a >= minimum:
            if a <= .25:
                kind = "intro"
            elif b >= duration-.25:
                kind = "outro"
            else:
                kind = "interlude"
            sections.append({"start": round(a, 3), "end": round(b, 3), "kind": kind,
                             "confidence": "medium", "reason": "无识别文字且分离人声活动较低"})
        i = j
    return sections


def segmentation(audio, sr, transcript, mode, maximum, target, mix_audio=None, return_analysis=False):
    if not 5 <= maximum <= 15 or not 5 <= target <= maximum:
        raise ValueError("首版测试范围：5 ≤ 目标时长 ≤ 最长时长 ≤ 15 秒。")
    starts, db = envelope(audio, sr)
    lines, words = transcript.get("segments", []), transcript.get("words", [])
    noise, voice = np.percentile(db, [15, 85])
    dynamic = max(6, float(voice-noise))
    quiet_threshold = min(float(np.median(db)-8), float(noise+.35*dynamic))
    active_threshold = float(noise+.55*dynamic)
    active = db > active_threshold
    zcr = zero_crossing(audio, starts, sr)
    zcr_high = float(np.percentile(zcr, 75))
    rhythm = rhythm_analysis(audio if mix_audio is None else mix_audio, sr)
    beat_points = rhythm["beats"]
    phrase_ranges = [(min(a["end"], b["start"])-.75, max(a["end"], b["start"])+.75)
                     for a, b in zip(lines, lines[1:])]
    candidates = {}

    def word_overlap(t):
        return [w["word"] for w in words if w["start"]+.02 < t < w["end"]-.02]

    def quiet_span(i):
        a = b = i
        while a > 0 and db[a-1] <= quiet_threshold: a -= 1
        while b+1 < len(db) and db[b+1] <= quiet_threshold: b += 1
        return a, b, (b-a+1)*.02

    def add(point, reason, penalty, warnings=None, kind="valley", confidence=.5):
        point = int(np.clip(point, 1, len(audio)-1))
        i = min(len(db)-1, int(np.searchsorted(starts, point)))
        overlap = word_overlap(point/sr)
        item = {"sample": point, "db": float(db[i]), "reason": reason, "penalty": penalty,
                "warnings": list(warnings or []), "kind": kind, "confidence_score": confidence,
                "overlap": bool(overlap), "protected_words": overlap}
        if overlap:
            item["warnings"].append("切点与估计字时间戳重叠："+"/".join(overlap))
            item["penalty"] += 20
            item["confidence_score"] = min(item["confidence_score"], .15)
        old = candidates.get(point)
        if old is None or item["penalty"] < old["penalty"]:
            candidates[point] = item

    for i in range(1, len(db) - 1):
        if db[i] <= db[i - 1] and db[i] < db[i + 1] and db[i] <= min(db[max(0, i-15):i+16]):
            a, b, span = quiet_span(i)
            previous = float(np.mean(db[max(0, i-10):i])) if i else db[i]
            following = float(np.mean(db[i+1:min(len(db), i+6)])) if i+1 < len(db) else db[i]
            if span >= .08:
                add(starts[i], "持续低人声区", 4, kind="quiet", confidence=.65)
            elif previous-db[i] >= 6 and following <= quiet_threshold+4:
                add(starts[i], "拖音衰减后的能量落点", 3, kind="drag_end", confidence=.72)
            elif zcr[i] >= zcr_high and db[i] < active_threshold:
                add(starts[i], "疑似换气附近的低能量区", 3.5, ["气口为声学估计，需要试听"], "breath", .62)
            else:
                add(starts[i], "人声局部低谷（非已确认句尾）", 7,
                    ["可能在句中，需要试听"], "valley", .4)

    # Faster-Whisper often makes adjacent lyric segments touch exactly even when
    # the waveform contains a clear cadence gap. Search each ASR boundary window
    # directly instead of requiring it to have survived the generic-minimum pass.
    for first, second in zip(lines, lines[1:]):
        center = (float(first["end"])+float(second["start"]))/2
        indices = [i for i, sample in enumerate(starts)
                   if center-.75 <= sample/sr <= center+.75 and not word_overlap(sample/sr)]
        if not indices:
            continue
        i = min(indices, key=lambda n: db[n])
        span = quiet_span(i)[2]
        if db[i] <= quiet_threshold and span >= .06:
            add(starts[i], "分句附近的持续低能量区", 0, kind="phrase_gap", confidence=.92)
        elif db[i] < active_threshold:
            add(starts[i], "分句附近的人声低谷", 3,
                ["低谷较短，需要试听确认"], "phrase_valley", .64)
        point = round(center*sr)
        if .02 < center < len(audio)/sr-.02 and not word_overlap(center):
            add(point, "ASR 分句边界", 5,
                ["分句处没有清晰持续低谷，需要检查唱音拖尾"], "phrase_boundary", .55)

    for lo, hi in phrase_ranges:
        options = []
        for point, c in candidates.items():
            if not lo <= point/sr <= hi or c["db"] > quiet_threshold or c["overlap"]:
                continue
            i = int(np.searchsorted(starts, point))
            if quiet_span(i)[2] >= .08:
                options.append(c)
        if options:
            chosen = min(options, key=lambda c: c["db"])
            chosen.update(reason="分句附近的持续低能量区", penalty=0, kind="phrase_gap", confidence_score=.92)
            chosen["warnings"] = []

    sections = _sections(lines, starts, active, sr, len(audio)/sr)
    for section in sections:
        for t, edge in ((section["start"], "开始"), (section["end"], "结束")):
            if .05 < t < len(audio)/sr-.05 and not word_overlap(t):
                add(round(t*sr), f"疑似{section['kind']} {edge}边界", 2, ["无人声段为声学估计，需要试听"],
                    "section", .75)

    # Word boundaries are fallback candidates for overlong phrases, never unflagged safe cuts.
    for word in words:
        point = round(word["end"] * sr)
        if 0 < point < len(audio) and point not in candidates:
            add(point, "估计词界回退", 12, ["词界回退，可能截断唱歌拖音"], "word_fallback", .28)
    # Preserve full coverage even for silence, ASR failure or sustained notes.
    for t in np.arange(target, len(audio) / sr, target):
        point = round(t * sr)
        if point not in candidates:
            warnings = ["无可靠切点，按时长回退，必须检查"]
            i = min(len(db)-1, int(np.searchsorted(starts, point)))
            if active[i]: warnings.append("强制切点可能落在持续唱音区")
            add(point, "时长强制回退", 40, warnings, "forced", .08)
    candidates[0] = {"sample": 0, "db": -120, "penalty": 0, "warnings": [], "reason": "音频开始",
                     "kind": "endpoint", "confidence_score": 1, "overlap": False, "protected_words": []}
    candidates[len(audio)] = {"sample": len(audio), "db": -120, "penalty": 0, "warnings": [], "reason": "音频结束",
                              "kind": "endpoint", "confidence_score": 1, "overlap": False, "protected_words": []}
    points = sorted(candidates)
    costs, prev = {0: 0}, {}
    levels = [c["db"] for c in candidates.values() if c["kind"] == "phrase_gap"]
    quiet, loud = np.percentile(levels if levels else db, [10, 90])
    for j, end in enumerate(points[1:], 1):
        costs[end] = float("inf")
        for start in reversed(points[:j]):
            duration = (end-start) / sr
            if duration > maximum:
                break
            if duration < 3 and end != len(audio):
                continue
            c = candidates[end]
            level = 0 if end == len(audio) else float(np.clip((c["db"]-quiet)/max(loud-quiet, 1), 0, 1))
            beat_cost = 0
            if end != len(audio) and beat_points:
                beat_cost = min(abs(end/sr-b) for b in beat_points) / max(60/(rhythm["tempo_bpm"] or 120), .2)
                beat_cost = min(.5, beat_cost)*.35
            score = costs.get(start, float("inf")) + .5 + ((duration-target)/4)**2 + c["penalty"] + 2*level + beat_cost
            if duration < 3:
                score += 5
            if score < costs[end]:
                costs[end], prev[end] = score, start
    if len(audio) not in prev:
        raise ValueError("无法构造完整分段，请调整目标时长。")
    boundaries = [len(audio)]
    while boundaries[-1]:
        boundaries.append(prev[boundaries[-1]])
    boundaries.reverse()
    selected = set(boundaries)
    rows = []
    for start, end in zip(boundaries, boundaries[1:]):
        related = [line["text"] for line in lines if start/sr <= (line["start"]+line["end"])/2 < end/sr]
        mask = (starts >= start) & (starts < end)
        level = float(np.median(db[mask])) if mask.any() else -120
        rows.append({"start_sample": start, "end_sample": end, "text": " / ".join(related),
                     "energy_db": level, "reason": candidates[end]["reason"],
                     "warnings": list(candidates[end]["warnings"]),
                     "boundary_kind": candidates[end]["kind"],
                     "boundary_confidence": round(float(candidates[end]["confidence_score"]), 3),
                     "vocal_state": "含识别人声" if related else "人声状态不确定"})
    if not return_analysis:
        return rows
    diagnostics = []
    ranked = sorted(candidates.values(), key=lambda c: (c["sample"] not in selected, c["penalty"], c["sample"]))[:600]
    for c in sorted(ranked, key=lambda c: c["sample"]):
        diagnostics.append({"time": round(c["sample"]/sr, 3), "kind": c["kind"], "reason": c["reason"],
            "confidence": round(float(c["confidence_score"]), 3), "selected": c["sample"] in selected,
            "protected": c["overlap"], "warnings": c["warnings"]})
    analysis = {"schema": 2, "duration": len(audio)/sr, "waveform": {
        "original": waveform_peaks(audio if mix_audio is None else mix_audio), "vocals": waveform_peaks(audio)},
        "phrases": [{"start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3),
                     "text": s["text"]} for s in lines],
        "protected_words": [{"start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3),
                             "text": w["word"]} for w in words],
        "sections": sections, "rhythm": rhythm, "candidates": diagnostics,
        "thresholds_db": {"quiet": round(quiet_threshold, 2), "active": round(active_threshold, 2)},
        "limitations": ["气口、拖音和无人声段均为声学估计，必须试听确认", "节拍仅用于安全候选间的次级择优"]}
    return rows, analysis


def camera_plan(mode, index, count, energy, median, previous_camera="", previous_frame="medium close-up"):
    if mode == "speaking":
        return "medium close-up", "medium close-up", "a steady locked-off camera"
    framing = "medium close-up" if index == 0 else previous_frame
    if index == 0:
        return framing, "medium shot", (
            "a restrained physical dolly out from chest-up to waist-up framing; "
            "the singer changes scale gently while reference-stage landmarks become more readable")
    if index == count-1:
        if framing == "medium shot":
            return framing, "medium close-up", (
                "a restrained physical dolly in from waist-up to chest-up framing; "
                "the singer grows gently in frame with visible background parallax")
        return framing, framing, (
            "a very slow lateral camera truck through a small arc; "
            "background landmarks shift subtly while the singer's scale remains constant")
    if energy > median and "dolly in" not in previous_camera and framing == "medium shot":
        return framing, "medium close-up", (
            "a deliberate physical dolly in from waist-up to chest-up framing, "
            "with visible foreground/background parallax and the singer's face kept readable")
    direction, background = ("left", "right") if "dolly in" in previous_camera else ("right", "left")
    return framing, framing, (
        f"a slow lateral camera truck to the {direction} through a small arc on the same side of the performer; "
        f"the background landmarks visibly slide {background} relative to the singer while {framing} framing is maintained")


def director_preferences(plan):
    """Normalize local-rule controls while keeping legacy saved projects readable."""
    supplied = plan.get("director") or {}
    performance = supplied.get("performance_intensity", "auto")
    camera = supplied.get("camera_activity", "auto")
    widest = supplied.get("widest_framing", "medium close-up")
    note = str(supplied.get("note", plan.get("visual_brief", "")) or "").strip()
    if performance not in {"auto", "restrained", "natural", "energetic"}:
        performance = "auto"
    if camera not in {"auto", "steady", "moderate", "dynamic"}:
        camera = "auto"
    if plan.get("mode") == "singing" and camera == "steady":
        camera = "moderate"
    if widest not in {"medium close-up", "medium shot"}:
        widest = "medium close-up"
    rules = (validate_config(supplied["rule_config"])
             if isinstance(supplied.get("rule_config"), dict) else default_config())
    return {"mode": "rule", "performance_intensity": performance, "camera_activity": camera,
            "widest_framing": widest, "note": note, "rule_config": rules,
            "schedule_seed": str(supplied.get("schedule_seed") or plan.get("id") or "h3lv"),
            "rule_revision": str(supplied.get("rule_revision") or "legacy")}


REFERENCE_LAYOUT_ALIASES = {
    "single_composite": "single_composite",
    "单图：图1人物+场景": "single_composite",
    "单图：人物+场景": "single_composite",
    "solo_scene": "solo_scene",
    "单人+场景：图1人物，图2场景": "solo_scene",
    "双图：图1人物，图2场景": "solo_scene",
}

REFERENCE_ROLE_LABELS = {
    "performer": "主表演者",
    "performer_environment": "人物与环境同图",
    "performer_reference": "人物补充参考",
    "environment": "场景环境",
    "appearance": "服装或外形参考",
    "prop": "道具参考",
    "other": "其他可见参考",
    "ignore": "不参与提示词",
}


def dynamic_reference_manifest(definitions, picture_count=None):
    """Validate user-declared roles without inferring meaning from image count."""
    if isinstance(definitions, str):
        try:
            definitions = json.loads(definitions or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError("参考图职责设置无法读取，请重新打开“设置参考图职责”保存。") from exc
    if isinstance(definitions, dict):
        definitions = definitions.get("assets", [])
    if not isinstance(definitions, list):
        raise ValueError("参考图职责必须是按图片顺序保存的列表。")
    count = int(picture_count if picture_count is not None else len(definitions))
    if not 1 <= count <= 9:
        raise ValueError("请连接 1至9 张参考图。")
    if len(definitions) != count:
        raise ValueError(f"已连接 {count} 张图，但参考图职责保存了 {len(definitions)} 项；请重新确认。")
    assets = []
    for index, supplied in enumerate(definitions, 1):
        if not isinstance(supplied, dict):
            raise ValueError(f"图{index}的参考职责无效。")
        role = str(supplied.get("role") or "unspecified").strip()
        if role not in REFERENCE_ROLE_LABELS:
            raise ValueError(f"图{index}尚未指定参考职责，请打开“设置参考图职责”。")
        name = str(supplied.get("name") or REFERENCE_ROLE_LABELS[role]).strip()[:80]
        if role == "other" and not str(supplied.get("name") or "").strip():
            raise ValueError(f"图{index}选择了“其他可见参考”，请填写它的名称。")
        asset = {"id": f"ref_{index:02d}", "picture": index,
                 "role": role, "name": name}
        snapshot = str(supplied.get("snapshot") or "").strip()
        if snapshot:
            asset["snapshot"] = snapshot
        assets.append(asset)
    primary = [item for item in assets if item["role"] in {"performer", "performer_environment"}]
    if len(primary) != 1:
        raise ValueError("参考图职责中需要且只能指定1张“主表演者”或“人物与环境同图”。")
    performer_pictures = [item["picture"] for item in assets
                          if item["role"] in {"performer", "performer_environment",
                                              "performer_reference", "appearance"}]
    # A combined performer/environment image still defines only Subject 1.
    # It must not recreate the old single-picture bug by inventing Subject 2.
    environment_pictures = [item["picture"] for item in assets if item["role"] == "environment"]
    subjects = []
    subjects.append({"id": "performer_1", "subject": 1, "kind": "performer",
                     "name": primary[0]["name"], "pictures": performer_pictures,
                     "includes_environment": primary[0]["role"] == "performer_environment"})
    next_subject = 2
    if environment_pictures:
        subjects.append({"id": "environment", "subject": next_subject, "kind": "environment",
                         "name": "环境", "pictures": environment_pictures})
        next_subject += 1
    for item in assets:
        if item["role"] in {"prop", "other"}:
            subjects.append({"id": item["id"], "subject": next_subject,
                             "kind": item["role"], "name": item["name"],
                             "pictures": [item["picture"]]})
            next_subject += 1
    environment = next((item for item in subjects if item["kind"] == "environment"), None)
    return {"schema": 3, "layout": "dynamic", "picture_count": count,
            "assets": assets, "subjects": subjects,
            "performers": [{"id": "performer_1", "subject": 1,
                            "picture": primary[0]["picture"], "pictures": performer_pictures,
                            "speaker": "S1"}],
            "environment": ({"id": "environment", "subject": environment["subject"],
                             "picture": environment_pictures[0], "pictures": environment_pictures}
                            if environment else None)}

def reference_manifest(layout="solo_scene"):
    """Return one stable picture/Subject contract for the whole project."""
    layout = REFERENCE_LAYOUT_ALIASES.get(str(layout), str(layout))
    if layout not in {"single_composite", "solo_scene"}:
        raise ValueError("双人图片组合已停用；请选择单图或双图单人模式。")
    performers = [{"id": "performer_1", "subject": 1, "picture": 1, "speaker": "S1"}]
    environment_picture = 1 if layout == "single_composite" else 2
    environment = {"id": "environment", "subject": 2, "picture": environment_picture}
    picture_count = 1 if layout == "single_composite" else 2
    return {"schema": 2, "layout": layout, "picture_count": picture_count,
            "performers": performers, "environment": environment}


def normalize_reference_manifest(plan):
    supplied = plan.get("references")
    if isinstance(supplied, dict) and supplied.get("schema") == 3:
        manifest = dynamic_reference_manifest(supplied.get("assets", []), supplied.get("picture_count"))
    elif isinstance(supplied, dict) and supplied.get("layout"):
        manifest = reference_manifest(supplied["layout"])
    else:
        # Legacy projects used Picture 1 as performer and Picture 2 as environment.
        manifest = reference_manifest("solo_scene")
    plan["references"] = manifest
    return manifest


def reference_brief_lines(manifest, mode="singing"):
    if manifest.get("schema") == 3:
        parts = []
        for subject in manifest["subjects"]:
            pictures = "、".join(f"<Picture {number}>" for number in subject["pictures"])
            if subject["kind"] == "performer":
                suffix = "及同图可见环境；环境不单独编号" if subject.get("includes_environment") else ""
                role = f"{pictures}=主表演者 <Subject {subject['subject']}>{suffix}"
            elif subject["kind"] == "environment":
                role = f"{pictures}=场景环境 <Subject {subject['subject']}>"
            else:
                role = f"{pictures}={subject['name']} <Subject {subject['subject']}>"
            parts.append(role)
        vocal_action = "说话" if mode == "speaking" else "唱歌"
        return "参考：动态参考图；" + "；".join(parts) + \
            f"。 <Audio 1>=<Subject 1> 的{vocal_action}参考。\n"
    performer = manifest["performers"][0]
    environment = manifest["environment"]
    if manifest["layout"] == "single_composite":
        reference = (f"参考：单图；<Picture {performer['picture']}>=人物 <Subject 1> "
                     "与同图可见环境；环境不单独编号。")
    else:
        reference = (f"参考：双图；<Picture {performer['picture']}>=人物 <Subject 1>；"
                     f"<Picture {environment['picture']}>=环境 <Subject {environment['subject']}>。")
    vocal_action = "说话" if mode == "speaking" else "唱歌"
    return reference + f" <Audio 1>=<Subject 1> 的{vocal_action}参考。\n"


def energy_bands(rows):
    values = np.asarray([float(row.get("energy_db", -20)) for row in rows], dtype=float)
    if len(values) < 2 or float(np.max(values)-np.min(values)) < 3:
        return ["medium"]*len(rows)
    low, high = np.quantile(values, [.33, .67])
    return ["low" if value <= low else ("high" if value >= high else "medium")
            for value in values]


def performance_direction(preference, band):
    resolved = preference if preference != "auto" else {
        "low": "restrained", "medium": "natural", "high": "energetic"}[band]
    return {
        "restrained": "restrained music-driven expression with small head, shoulder and free-hand motion",
        "natural": "natural music-driven expression with controlled head, shoulder and free-hand motion",
        "energetic": "engaged music-driven expression with clear but controlled head, shoulder and free-hand motion",
    }[resolved]


def _start_composition(index, band, rows, sizes, previous_end, states, angles=None,
                       seed_key="", avoid_axis_cross=True):
    if index == 0:
        has_vocal = bool(str(rows[index].get("text", "")).strip()) or "含识别人声" in str(rows[index].get("vocal_state", ""))
        if "medium shot" in sizes and band == "high":
            return "medium shot", "front"
        if "medium shot" in sizes and band == "low" and not has_vocal:
            return "medium shot", "front"
        return "medium close-up", "front"

    target_size = "medium close-up" if band == "high" else (
        "medium shot" if band == "low" and "medium shot" in sizes else previous_end["framing"])
    if states and states[-1].get("camera_move_family") == "dolly in":
        target_size = "medium close-up"
    if (len(sizes) > 1 and len(states) >= 2
            and states[-1]["camera_start"] == states[-2]["camera_start"] == target_size):
        target_size = "medium shot" if target_size == "medium close-up" else "medium close-up"
    angles = list(angles or ["front", "front three-quarter right", "front three-quarter left"])
    candidates = []
    previous_angle = previous_end["angle"]
    previous_side = "right" if "right" in previous_angle else ("left" if "left" in previous_angle else "front")
    for framing in sizes:
        for angle in angles:
            if framing == previous_end["framing"] and angle == previous_angle:
                continue
            side = "right" if "right" in angle else ("left" if "left" in angle else "front")
            # A direct left-quarter/right-quarter reversal crosses the readable
            # performance axis and is harder to read than returning through front.
            if avoid_axis_cross and previous_side != "front" and side != "front" and side != previous_side:
                continue
            score = 0.0
            if framing != target_size:
                score += 1.5
            changes = int(framing != previous_end["framing"])+int(angle != previous_angle)
            if changes == 1:
                score += .25
            if states and angle == states[-1]["camera_start_angle"]:
                score += .3
            tie = hashlib.sha256(
                f"{seed_key}|composition|{index}|{framing}|{angle}".encode("utf-8")
            ).hexdigest()
            candidates.append((score, tie, framing, angle))
    _, _, framing, angle = min(candidates)
    return framing, angle


def _movement_type_family(value):
    if value.startswith("truck_") or value == "lateral":
        return "lateral"
    if value.startswith("arc_") or value == "arc":
        return "arc"
    if value.startswith("dolly_") or value in {"dolly in", "dolly out"}:
        return "dolly"
    if value in {"micro_reframe", "micro reframe"}:
        return "micro"
    return value


def _select_movement(rules, band, states, seed_key):
    candidates = list(rules["energy_movements"][band])
    previous_family = _movement_type_family(
        states[-1].get("camera_move_type") or states[-1].get("camera_move_family", "")
    ) if states else None
    if rules.get("no_adjacent_same_family") and previous_family:
        filtered = [item for item in candidates if _movement_type_family(item) != previous_family]
        if filtered:
            candidates = filtered

    if rules.get("alternate_lateral_direction"):
        last_lateral = next((state for state in reversed(states)
                             if _movement_type_family(state.get("camera_move_type") or
                                state.get("camera_move_family", "")) == "lateral"), None)
        if last_lateral and last_lateral.get("camera_move_direction") in {"left", "right"}:
            opposite = "truck_right" if last_lateral["camera_move_direction"] == "left" else "truck_left"
            if opposite in candidates:
                candidates = [item for item in candidates
                              if _movement_type_family(item) != "lateral" or item == opposite]

    if not candidates:
        raise ValueError(f"唱歌规则在 {band} 能量下没有可用的非重复运镜。")
    return min(candidates, key=lambda item: hashlib.sha256(
        f"{seed_key}|movement|{len(states)}|{band}|{item}".encode("utf-8")
    ).hexdigest())


def _movement_for(framing, angle, band, activity, sizes, states, rules=None, seed_key=""):
    rules = rules or default_config()["singing"]
    resolved = activity if activity != "auto" else {
        "low": "moderate", "medium": "moderate", "high": "dynamic"}[band]
    if resolved == "steady":
        resolved = "moderate"
    planned = _select_movement(rules, band, states, seed_key)
    if planned == "dolly_in":
        pace = "very slow controlled" if band == "low" else (
            "controlled" if resolved == "moderate" else "deliberate but restrained")
        return (framing, "dolly in", "forward",
                f"a {pace} short physical dolly in within the established {framing}; "
                "the performer becomes only slightly larger and remains chest-up, with subtle readable background parallax",
                "short dolly-in motion is already gently readable at the cut",
                "short dolly-in motion remains active at the cut", planned)
    if planned == "dolly_out":
        pace = "very slow controlled" if band == "low" else "controlled"
        return (framing, "dolly out", "backward",
                f"a {pace} short physical dolly out within the established {framing}; "
                "the performer becomes only slightly smaller while the frame remains above the waist, with subtle readable background parallax",
                "short dolly-out motion is already gently readable at the cut",
                "short dolly-out motion remains active at the cut", planned)
    if planned == "micro_reframe":
        pace = "barely perceptible" if band == "low" else "restrained"
        return (framing, "micro reframe", "", (
                f"a {pace} tripod-based breathing reframe around the established composition; "
                "the performer, eye line and background landmarks remain spatially anchored"),
                "micro-reframing is already gently readable at the cut",
                "micro-reframing remains active at the cut", planned)
    if planned in {"arc_left", "arc_right"}:
        direction = "left" if planned == "arc_left" else "right"
        return (framing, "arc", direction, (
                f"a short shallow arc move a few degrees to the {direction} around the performer; "
                f"{framing} scale and centered eye line stay stable while local background parallax remains subtle"),
                f"shallow arc motion to the {direction} is already gently readable at the cut",
                f"shallow arc motion to the {direction} remains active at the cut", planned)
    direction = "left" if planned == "truck_left" else "right"
    background = "right" if direction == "left" else "left"
    speed = "slow" if resolved == "moderate" else "deliberate"
    move = (f"a {speed} lateral camera truck to the {direction} at a constant viewing angle; "
            f"background landmarks shift briefly {background} relative to the singer while {framing} framing is maintained; "
            "the move stays short and does not become continuous background travel")
    return (framing, "lateral", direction, move,
            f"lateral motion to the {direction} is already gently readable at the cut",
            f"lateral motion to the {direction} remains active at the cut", planned)


def _cut_relationship(previous_end, framing, angle, previous_state, current_family, current_direction):
    size_changed = framing != previous_end["framing"]
    angle_changed = angle != previous_end["angle"]
    if size_changed and angle_changed:
        strategy = "shot-size plus angle cut"
        reason = "both scale and camera angle change in one controlled editorial step"
        risk = "low"
    elif size_changed:
        strategy = "shot-size cut"
        reason = "shot size changes while the viewing axis remains readable"
        risk = "medium"
    elif angle_changed:
        strategy = "30-degree angle cut"
        reason = "camera angle carries the cut because only one shot size is allowed"
        risk = "review"
    else:
        strategy = "matched-action cut"
        reason = "composition is retained only because visible motion supplies the edit transition"
        risk = "review"
    if (previous_state and previous_state.get("camera_move_family") == current_family == "lateral"
            and previous_state.get("camera_move_direction") == current_direction):
        reason += "; screen-direction camera motion remains compatible across the cut"
    elif previous_state and (previous_state.get("camera_move_family") == "steady" or current_family == "steady"):
        reason += "; the composition change makes the motion contrast readable"
    return strategy, reason, risk


def _arc_ending_angle(angle, direction, allowed_angles):
    angle_order = ["front three-quarter left", "front", "front three-quarter right"]
    position = angle_order.index(angle)
    step = -1 if direction == "left" else 1
    candidate = angle_order[max(0, min(len(angle_order) - 1, position + step))]
    return candidate if candidate in allowed_angles else angle


def camera_sequence(mode, rows, director=None):
    """Plan the full editorial sequence from relative audio evidence and prior camera state."""
    prefs = director or {"performance_intensity": "auto", "camera_activity": "auto",
                         "widest_framing": "medium close-up", "note": "",
                         "rule_config": default_config()}
    rules = validate_config(prefs.get("rule_config") or default_config())
    if mode == "speaking":
        speaking = rules["speaking"]
        framing, angle, movement = speaking["framing"], speaking["angle"], speaking["movement"]
        camera_move = "a steady locked-off camera" if movement == "steady" else movement
        return [{
            "camera_start": framing, "camera_end": framing,
            "camera_start_angle": angle, "camera_end_angle": angle,
            "camera_move": camera_move,
            "entry_cut_strategy": "opening" if index == 0 else "locked-camera continuity cut",
            "entry_cut_risk": "low", "entry_cut_reason": "the fixed-camera composition is unchanged",
            "entry_motion_state": "still", "exit_motion_state": "still",
            "previous_end_framing": framing, "previous_end_angle": angle,
            "exit_cut_strategy": "locked-camera continuity cut" if index + 1 < len(rows) else "ending",
            "camera_move_family": "steady", "camera_move_direction": "", "camera_move_type": "steady",
            "relative_energy": "medium",
            "performance_direction": "restrained fixed-camera spoken delivery with direct gaze and compact natural gestures",
            "composition_anchor": "speaker centered with a stable upper-third eye line, direct reference-consistent gaze, shoulder line, hand position and visible tabletop props",
        } for index in range(len(rows))]
    singing = rules["singing"]
    sizes = list(singing["allowed_framings"])
    if prefs["widest_framing"] == "medium close-up":
        sizes = [item for item in sizes if item == "medium close-up"] or ["medium close-up"]
    angles = list(singing["allowed_angles"])
    seed_key = f"{prefs.get('schedule_seed', 'h3lv')}|{prefs.get('rule_revision', 'legacy')}"
    bands = energy_bands(rows)
    states = []
    previous_end = {"framing": "medium close-up", "angle": "front", "motion": "still"}
    for index, row in enumerate(rows):
        band = bands[index]
        framing, angle = _start_composition(
            index, band, rows, sizes, previous_end, states, angles=angles,
            seed_key=seed_key, avoid_axis_cross=singing.get("avoid_direct_axis_cross", True))
        ending, family, direction, move, entry_motion, exit_motion, movement_type = _movement_for(
            framing, angle, band, prefs["camera_activity"], sizes, states,
            rules=singing, seed_key=seed_key)
        ending_angle = angle
        if family == "arc":
            ending_angle = _arc_ending_angle(angle, direction, angles)
        if index == 0:
            strategy, reason, risk = "opening", "no preceding edit", "low"
        else:
            strategy, reason, risk = _cut_relationship(previous_end, framing, angle, states[-1], family, direction)
        states.append({
            "camera_start": framing, "camera_end": ending,
            "camera_start_angle": angle, "camera_end_angle": ending_angle,
            "camera_move": move, "entry_cut_strategy": strategy,
            "entry_cut_risk": risk, "entry_cut_reason": reason,
            "entry_motion_state": entry_motion, "exit_motion_state": exit_motion,
            "previous_end_framing": previous_end["framing"],
            "previous_end_angle": previous_end["angle"],
            "camera_move_family": family, "camera_move_direction": direction,
            "camera_move_type": movement_type,
            "relative_energy": band,
            "performance_direction": performance_direction(prefs["performance_intensity"], band),
            "composition_anchor": "performer centered with the eye line near the upper third; reference-consistent gaze and microphone screen side",
        })
        previous_end = {"framing": ending, "angle": ending_angle, "motion": exit_motion}
    for index, state in enumerate(states):
        state["exit_cut_strategy"] = states[index + 1]["entry_cut_strategy"] if index + 1 < len(states) else "ending"
    return states


def framing_crop(framing):
    if framing == "medium close-up":
        return "胸部以上构图；画面下沿稳定在腰部以上，不出现骨盆和腿部"
    if framing == "medium shot":
        return "腰部以上构图；画面下沿稳定在腰线，不出现骨盆和腿部"
    return framing


def _zh_framing(value):
    return {"medium close-up": "中近景（胸部以上）", "medium shot": "中景（腰部以上）"}.get(value, value)


def _zh_angle(value):
    return {"front": "正面", "front three-quarter left": "正面左前侧约30度",
            "front three-quarter right": "正面右前侧约30度"}.get(value, value)


def _zh_cut(value):
    return {"opening": "开场", "shot-size plus angle cut": "景别与机位同时变化的硬切",
            "shot-size cut": "景别变化硬切", "30-degree angle cut": "约30度机位变化硬切",
            "matched-action cut": "动作匹配切镜", "ending": "结束"}.get(value, value)


def _zh_risk(value):
    return {"low": "低", "medium": "中", "review": "需要重点检查"}.get(value, value)


def _zh_performance(value, mode="singing"):
    if mode == "speaking":
        return "克制自然的固定机位口播，保持正面视线和稳定坐姿或站姿，只做小幅头部、肩部和手势动作"
    if value.startswith("restrained"):
        return "克制的音乐表演，以小幅头部、肩部和空闲手动作回应音乐"
    if value.startswith("engaged"):
        return "投入而受控的音乐表演，头部、肩部和空闲手动作清晰可见"
    return "自然且受控的音乐表演，头部、肩部和空闲手动作适中"


def _zh_camera_operation(row, framing, ending):
    family = row.get("camera_move_family")
    direction = row.get("camera_move_direction")
    if family == "steady":
        return "固定机位，人物与背景构图保持稳定"
    if family == "dolly in":
        return f"摄影机短距离缓慢前移，人物仅轻微变大并保持{_zh_framing(framing)}，背景产生轻微视差"
    if family == "dolly out":
        return f"摄影机短距离缓慢后移，人物仅轻微变小并保持{_zh_framing(framing)}，背景产生轻微视差"
    if family == "micro reframe":
        return "摄影机仅做轻微呼吸式构图调整，人物、眼线和背景保持稳定"
    if family == "arc":
        side = "左侧" if direction == "left" else "右侧"
        return (f"摄影机实体向{side}环绕人物约20度，人物保持居中，"
                "背景产生清晰但克制的局部视差；不是原地摇镜")
    side = "左侧" if direction == "left" else "右侧"
    background = "右移" if direction == "left" else "左移"
    return f"摄影机向{side}短距离平稳横移，人物保持居中，背景短暂向画面{background}产生视差"


def _zh_motion(row, entry=True):
    family = row.get("camera_move_family")
    if family == "steady" or not family:
        return "静止"
    direction = row.get("camera_move_direction")
    if family == "dolly in":
        return "前移运镜已自然进行" if entry else "前移运镜保持进行"
    if family == "dolly out":
        return "后移运镜已自然进行" if entry else "后移运镜保持进行"
    if family == "micro reframe":
        return "轻微构图调整已自然进行" if entry else "轻微构图调整保持进行"
    if family == "arc":
        side = "左" if direction == "left" else "右"
        return f"向{side}小弧线运镜已自然进行" if entry else f"向{side}小弧线运镜保持进行"
    side = "左" if direction == "left" else "右"
    return f"向{side}横移已自然进行" if entry else f"向{side}横移保持进行"


def segment_brief(plan, row, framing, ending, move, previous_frame):
    mode = plan["mode"]
    if mode == "speaking":
        camera_plan = ("中近景（胸部以上）正面固定机位，人物居中，眼线位于画面上三分之一附近；"
                       "全程不推拉、不平移、不摇摄、不变焦，人物尺度、背景透视和构图保持稳定")
        performance = "自然口型和克制的小幅动作"
    else:
        opening = f"{_zh_framing(framing)}{_zh_angle(row.get('camera_start_angle', 'front'))}开场，人物居中"
        camera = _zh_camera_operation(row, framing, ending)
        ending_text = f"{_zh_framing(ending)}{_zh_angle(row.get('camera_end_angle', 'front'))}"
        exit_motion = _zh_motion(row, entry=False)
        ending_state = "静止结束" if exit_motion == "静止" else f"{exit_motion}至片段结束"
        camera_plan = f"{opening}；{camera}；结束于{ending_text}，{ending_state}"
        performance = _zh_performance(row.get('performance_direction', 'natural controlled performance'), mode)
    return (
        f"镜头方案：{camera_plan}\n"
        + f"表演节奏：{performance}\n"
    )


H3LV_CAMERA_FIELDS = ("镜头方案", "表演节奏")
H3LV_CAMERA_LEGACY_FIELDS = (
    "生成时长", "开场构图", "段内运镜", "结束构图",
    "入口衔接", "出口运动状态", "表演节奏",
)


def validate_segment_brief(value):
    """Validate the material-agnostic camera handoff while allowing legacy projects."""
    text = str(value or "").strip()
    if re.search(r"<(?:Picture|Subject)\s+\d+>", text):
        raise ValueError("分段运镜简报不能声明参考图或主体关系；请在提示词小助手的用户提示词中填写素材说明。")
    if text.startswith("协议：H3LV_SEGMENT_V1"):
        return text
    fields = {}
    for line in text.splitlines():
        if "：" in line:
            name, content = line.split("：", 1)
            fields[name.strip()] = content.strip()
    if "协议" in fields and fields["协议"] != "H3LV_CAMERA_V1":
        raise ValueError("分段镜头简报协议版本无效。")
    if "镜头方案" in fields:
        missing = [name for name in H3LV_CAMERA_FIELDS if not fields.get(name)]
        if missing:
            raise ValueError("分段镜头简报缺少字段：" + "、".join(missing))
        return text
    if any(name in fields for name in H3LV_CAMERA_LEGACY_FIELDS):
        missing = [name for name in H3LV_CAMERA_LEGACY_FIELDS if not fields.get(name)]
        if missing:
            raise ValueError("旧版分段镜头简报缺少字段：" + "、".join(missing))
        if "固定机位" in fields["段内运镜"] and fields["出口运动状态"] != "静止":
            raise ValueError("固定机位的出口运动状态必须为“静止”。")
        return text
    if "表演节奏" in fields:
        raise ValueError("分段镜头简报缺少字段：镜头方案")
    return text


H3_REF2VA_SECTIONS = (
    "subject_definitions", "summary", "retention_analysis", "detailed_description",
    "overall_soundscape", "non_diegetic_music",
)


def _english_angle(value):
    return {
        "front": "frontal",
        "front three-quarter left": "front three-quarter left",
        "front three-quarter right": "front three-quarter right",
    }.get(value, "frontal")


def _composer_subjects(manifest):
    if manifest.get("schema") == 3:
        return list(manifest.get("subjects") or [])
    performer = manifest["performers"][0]
    subjects = [{"subject": 1, "kind": "performer", "name": "visible performer",
                 "pictures": [performer["picture"]]}]
    if manifest.get("layout") != "single_composite" and manifest.get("environment"):
        environment = manifest["environment"]
        subjects.append({"subject": environment["subject"], "kind": "environment",
                         "name": "environment", "pictures": [environment["picture"]]})
    return subjects


def compose_h3_prompt(plan, row):
    """Render trusted shot state into Ref2VA English without another model call."""
    manifest = normalize_reference_manifest(plan)
    subjects = _composer_subjects(manifest)
    definitions, retention = [], []
    for subject in subjects:
        number = int(subject["subject"])
        pictures = list(dict.fromkeys(int(value) for value in subject.get("pictures") or []))
        sources = " and ".join(f"<Picture {value}>" for value in pictures)
        if subject["kind"] == "performer":
            visible_scope = ("identity, facial features, hairstyle, body proportions, clothing, "
                             "only the worn accessories or handheld items visibly present, and the complete visible setting"
                             if subject.get("includes_environment") else
                             "identity, facial features, hairstyle, body proportions, clothing, and only the worn accessories or handheld items visibly present")
            definitions.append(
                f"<Subject {number}> is the visible on-camera performer defined by {sources}, "
                f"preserving the referenced {visible_scope} in those references.")
            retention.append(
                f"<Subject {number}> (appears in [Shot 1]): fully_preserved - the referenced performer identity, "
                "appearance, clothing, and visible carried or worn items remain consistent.")
        elif subject["kind"] == "environment":
            definitions.append(
                f"<Subject {number}> is the visible environment defined by {sources}, preserving its spatial layout, "
                "landmarks, materials, lighting, and color relationships.")
            retention.append(
                f"<Subject {number}> (appears in [Shot 1]): fully_preserved - the referenced environment layout, "
                "landmarks, lighting, and color relationships remain consistent.")
        else:
            name = str(subject.get("name") or "visible reference item")
            definitions.append(
                f"<Subject {number}> is {name}, defined by {sources} and used only in that declared visible role.")
            retention.append(
                f"<Subject {number}> (appears in [Shot 1] only when visible in the reference composition): "
                f"fully_preserved - the declared {name} reference remains consistent.")
    action = "spoken delivery" if plan.get("mode") == "speaking" else "singing performance"
    definitions.append(f"<Audio 1> is the vocal-performance reference for <Subject 1> (S1), guiding visible mouth articulation, {action} timing, pauses, and breathing.")
    retention.append("<Audio 1>: reference - the current segment audio guides the visible vocal performance of <Subject 1> (S1) without being copied as the generated soundtrack.")

    duration = float(row["generation_frames"]) / 24.0
    environment = next((item for item in subjects if item["kind"] == "environment"), None)
    environment_phrase = f" within <Subject {environment['subject']}>" if environment else " within the referenced visible setting"
    summary = (f"[reference generation + audio reference] {duration:.3f} seconds, one continuous shot, "
               f"{action}, with <Subject 1> as the on-camera performer{environment_phrase}; "
               "<Audio 1> guides the visible vocal timing.")

    framing = str(row.get("camera_start") or "medium close-up")
    ending = str(row.get("camera_end") or framing)
    angle = _english_angle(row.get("camera_start_angle"))
    ending_angle = _english_angle(row.get("camera_end_angle"))
    camera = str(row.get("camera_move") or "a steady locked-off camera")
    anchor = str(row.get("composition_anchor") or "performer centered with a stable upper-third eye line")
    performance = str(row.get("performance_direction") or (
        "restrained fixed-camera spoken delivery with compact natural gestures"
        if plan.get("mode") == "speaking" else
        "natural music-driven expression with controlled head, shoulder and free-hand motion"))
    vocal = "speaks" if plan.get("mode") == "speaking" else "sings"
    detailed = (
        f"[Shot 1] The shot opens in a {framing} from a {angle} view, with <Subject 1> (S1) {anchor}. "
        f"The camera performs {camera}. <Subject 1> {vocal} in visible synchronization with <Audio 1>, "
        f"using {performance}. The performance remains one continuous shot with natural mouth articulation, blinking, "
        f"breathing, and restrained body motion. The shot ends in a {ending} from a {ending_angle} view while preserving "
        "the declared reference identities, clothing, visible items, environment geometry, perspective, and lighting. "
        "No additional performer, worn accessory, handheld prop, or cut is introduced.")
    prompt = (
        "subject_definitions:\n" + "\n".join(definitions) + "\n\n"
        "summary:\n" + summary + "\n\n"
        "retention_analysis:\n" + "\n".join(retention) + "\n\n"
        "detailed_description:\n" + detailed + "\n\n"
        "overall_soundscape:\n<Audio 1> guides the visible vocal performance; no additional diegetic sound is specified.\n\n"
        "non_diegetic_music:\nN/A"
    )
    validate_h3_prompt(prompt, manifest)
    return prompt


def validate_h3_prompt(value, manifest=None):
    text = str(value or "").strip()
    positions = [text.find(section + ":") for section in H3_REF2VA_SECTIONS]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("最终H3提示词必须按顺序包含六个Ref2VA字段。")
    detailed = text[positions[3]:positions[4]]
    if detailed.count("[Shot 1]") != 1 or re.search(r"\[Shot\s+[2-9]\d*\]", detailed):
        raise ValueError("每个分段的最终H3提示词只能包含一个 [Shot 1]。")
    if manifest:
        count = int(manifest.get("picture_count", 0))
        referenced = {int(value) for value in re.findall(r"<Picture\s+(\d+)>", text)}
        invalid = sorted(number for number in referenced if number < 1 or number > count)
        if invalid:
            raise ValueError("最终H3提示词引用了未连接的参考图：" + "、".join(f"Picture {n}" for n in invalid))
    return text


def decorate(plan, regenerate_prompts=True):
    sr, rows = plan["sample_rate"], plan["segments"]
    if not rows or rows[0]["start_sample"] != 0 or rows[-1]["end_sample"] != plan["samples"]:
        raise ValueError("分段必须覆盖完整原曲。")
    last = 0
    prefs = director_preferences(plan)
    plan["director"] = prefs
    plan["shot_plan_version"] = 14
    plan.pop("ai_shot_plan", None)
    plan.pop("ai_motion_contract", None)
    camera_states = camera_sequence(plan["mode"], rows, prefs)
    for i, row in enumerate(rows):
        start, end = int(row["start_sample"]), int(row["end_sample"])
        duration = (end-start)/sr
        if start != last or end <= start or duration > plan["max_seconds"] + 1/sr:
            raise ValueError("切点必须连续、不重叠，且不超过最长时长。")
        last = end
        edit_start, edit_end = round(start/sr*24), round(end/sr*24)
        if edit_end <= edit_start:
            raise ValueError("分段过短，未达到一帧。")
        row.update(index=i, start=start/sr, end=end/sr, duration=duration,
                   edit_frames=edit_end-edit_start, generation_frames=frames_for(duration, edit_end-edit_start))
        if regenerate_prompts or not row.get("prompt"):
            state = camera_states[i]
            row.update(state)
            framing, ending, move = state["camera_start"], state["camera_end"], state["camera_move"]
            row["camera"] = f"{framing}; {move}"
            row["prompt"] = segment_brief(plan, row, framing, ending, move, state["previous_end_framing"])
        row.pop("h3_prompt", None)
        row.pop("h3_prompt_mode", None)
        row.setdefault("warnings", [])
    return plan


def segment_fingerprint(row):
    data = {k: row.get(k) for k in (
        "start_sample", "end_sample", "prompt", "generation_frames", "edit_frames")}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def archive_take(row, job):
    """Keep one recoverable copy of a completed take without duplicating it."""
    if not job or job.get("status") != "completed" or not job.get("video"):
        return
    takes = row.setdefault("takes", [])
    identity = (job.get("video"), job.get("prompt_id"))
    if not any((item.get("video"), item.get("prompt_id")) == identity for item in takes):
        takes.append(copy.deepcopy(job))


def request_regeneration(row, reason):
    """Mark only this segment stale while retaining its currently usable take."""
    job = row.get("job")
    if job and job.get("status") == "completed" and job.get("video"):
        row["needs_regeneration"] = True
        row["regeneration_reason"] = str(reason)
        return
    if job:
        row.pop("job", None)
    row.pop("needs_regeneration", None)
    row.pop("regeneration_reason", None)


def edit_plan(plan, submitted):
    if plan.get("run_status") in {"running", "pausing", "stopping", "merging"}:
        raise ValueError("请等当前任务停止后再修改分段。")
    if len(submitted) != len(plan["segments"]):
        raise ValueError("首版支持移动切点；增删段请调整参数重新分析。")
    sr = plan["sample_rate"]
    old_signatures = [segment_fingerprint(row) for row in plan["segments"]]
    previous = 0
    for i, (row, update) in enumerate(zip(plan["segments"], submitted)):
        end = plan["samples"] if i == len(submitted)-1 else round(float(update["end"]) * sr)
        changed = row["start_sample"] != previous or row["end_sample"] != end
        prompt = str(update["prompt"])
        if not changed:
            validate_segment_brief(prompt)
        row.update(start_sample=previous, end_sample=end, prompt=prompt)
        if changed:
            row["reason"] = "手动调整切点"
            row["boundary_kind"] = "manual"
            row["boundary_confidence"] = None
            row["warnings"] = ["用户调整边界，歌词关联和节奏标注需重新检查"]
            row["prompt"] = ""
        previous = end
    plan.update(approved=False, run_status="draft", revision=plan.get("revision", 0)+1)
    decorate(plan, regenerate_prompts=False)
    changed = []
    for index, (row, old_signature) in enumerate(zip(plan["segments"], old_signatures)):
        if segment_fingerprint(row) != old_signature:
            changed.append(index)
            request_regeneration(row, "切点或提示词已修改")
    if changed and plan.get("final_video"):
        plan["final_stale"] = True
    plan["changed_segments"] = changed
    return plan


def fingerprint(plan):
    data = [{k: row.get(k) for k in ("start_sample", "end_sample", "prompt")}
            for row in plan["segments"]]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()
