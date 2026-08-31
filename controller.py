import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from .core import (LOCK, archive_take, audio_file, fingerprint, inside, output_preview, project_path,
                   read_plan, segment_fingerprint, write_plan)

TASKS = {}
SEGMENT_NODE_TYPES = {"H3LVUnified"}


def apply_bundled_prompt_rule(prompt):
    """Keep saved workflows on the plugin's current Ref2VA formatter rule."""
    rule = (Path(__file__).resolve().parent/"ref2va_performance_rule.txt").read_text(encoding="utf-8")
    for node in prompt.values():
        if node.get("class_type") != "PromptExpand":
            continue
        inputs = node.setdefault("inputs", {})
        inputs["custom_rule"] = True
        inputs["custom_rule_content"] = rule
    return prompt


def normalize_output_contract(snapshot):
    """Upgrade frozen queue graphs from the former 7-output loader contract."""
    loader = str(snapshot.get("loader_id", ""))
    video = str(snapshot.get("video_id", ""))
    prompt = snapshot.get("prompt", {})
    inputs = prompt.get(video, {}).get("inputs", {})
    filename = inputs.get("filename_prefix")
    if isinstance(filename, (list, tuple)) and str(filename[0]) == loader:
        inputs["filename_prefix"] = [loader, 4]
    frame_rate = inputs.get("frame_rate")
    if isinstance(frame_rate, (list, tuple)) and str(frame_rate[0]) == loader:
        inputs["frame_rate"] = 24
    snapshot["output_contract_version"] = 2
    return snapshot


def final_prompt_from_history(history):
    """Keep the actual formatter output beside each take for later diagnosis."""
    for output in history.get("outputs", {}).values():
        texts = output.get("text") if isinstance(output, dict) else None
        if not isinstance(texts, list):
            continue
        value = "\n".join(str(item) for item in texts).strip()
        if all(f"{heading}:" in value for heading in (
                "subject_definitions", "summary", "retention_analysis",
                "detailed_description", "overall_soundscape", "non_diegetic_music")):
            return value
    return ""


def halt_status(plan):
    if plan.get("stop_requested"):
        return "stopped"
    if plan.get("pause_requested"):
        return "paused"
    return None


def video_from_history(history, video_node, directory, output_root):
    if history.get("status", {}).get("status_str") != "success":
        raise RuntimeError("该段 ComfyUI 生成失败，请检查原始节点报错，再点击重试。")
    entries = history.get("outputs", {}).get(str(video_node), {}).get("gifs", [])
    for entry in reversed(entries):
        if entry.get("type") == "output" and str(entry.get("filename", "")).lower().endswith(".mp4"):
            path = inside(directory, Path(output_root)/entry.get("subfolder", "")/entry["filename"])
            if path.is_file():
                return str(path)
    raise RuntimeError("未找到当前项目内的 MP4。请把一体化节点的 filename_prefix 接到 VHS，并开启 save_output。")


def queued_ids(server):
    running, waiting = server.prompt_queue.get_current_queue()
    return {item[1] for item in running+waiting}


async def execute_project(root, project_id, server):
    import execution
    import folder_paths
    directory = project_path(root, project_id)
    snapshot_file = directory/"state"/"queue_snapshot.json"
    snapshot = normalize_output_contract(json.loads(snapshot_file.read_text(encoding="utf-8")))
    snapshot_file.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    try:
        initial_plan = read_plan(root, project_id)
        only_segment = initial_plan.get("run_only_segment")
        indices = ([int(only_segment)] if only_segment is not None
                   else range(len(initial_plan["segments"])))
        for index in indices:
            plan = read_plan(root, project_id)
            halted = halt_status(plan)
            if halted:
                plan["run_status"] = halted
                write_plan(root, plan)
                return
            row = plan["segments"][index]
            if row.get("job", {}).get("status") == "completed" and not row.get("needs_regeneration"):
                continue
            if not plan.get("approved") or plan.get("approved_fingerprint") != fingerprint(plan):
                raise ValueError("方案已变化，请重新审核确认。")
            if row.get("needs_regeneration") and row.get("job", {}).get("status") == "completed":
                previous_job = copy.deepcopy(row["job"])
                archive_take(row, previous_job)
                row["replacement_previous_job"] = previous_job
                row.pop("job", None)
                write_plan(root, plan)
            job = row.get("job")
            if job and job.get("status") == "failed":
                raise ValueError(f"第 {index+1} 段已失败，请先点击该段重试。")
            if not job:
                prompt_id = str(uuid.uuid4())
                prompt = copy.deepcopy(snapshot["prompt"])
                prompt[snapshot["loader_id"]]["inputs"].update(project_id=project_id, segment_index=index)
                valid = await execution.validate_prompt(prompt_id, prompt, [snapshot["video_id"]])
                if not valid[0]:
                    raise ValueError("视频工作流校验失败："+str(valid[1]))
                # Save intent before enqueue; uncertain interrupted jobs are never blindly resubmitted.
                row["job"] = job = {"prompt_id": prompt_id, "status": "queued"}
                write_plan(root, plan)
                extra = {
                    "extra_pnginfo": {"workflow": snapshot.get("workflow", {})},
                    # Match ComfyUI's /prompt metadata contract. The modern task
                    # queue UI uses this timestamp to register and order jobs.
                    "create_time": int(time.time() * 1000),
                }
                client_id = str(snapshot.get("client_id") or "").strip()
                if client_id:
                    extra["client_id"] = client_id
                number = server.number
                server.number += 1
                server.prompt_queue.put((number, prompt_id, prompt, extra, valid[2], {}))
            prompt_id = job["prompt_id"]
            while True:
                history = server.prompt_queue.get_history(prompt_id=prompt_id).get(prompt_id)
                if history:
                    break
                if prompt_id not in queued_ids(server):
                    history = server.prompt_queue.get_history(prompt_id=prompt_id).get(prompt_id)
                    if history:
                        break
                    raise RuntimeError(f"第 {index+1} 段任务记录不在队列/历史中，可能曾中断。不会自动重复提交；请确认后点该段重试。")
                await asyncio.sleep(.5)
            plan = read_plan(root, project_id)
            row = plan["segments"][index]
            try:
                video = video_from_history(history, snapshot["video_id"], directory, folder_paths.get_output_directory())
                row["job"].update(status="completed", video=video,
                                  input_fingerprint=segment_fingerprint(row))
                final_prompt = final_prompt_from_history(history)
                if final_prompt:
                    row["job"]["final_prompt"] = final_prompt
                row.pop("needs_regeneration", None)
                row.pop("regeneration_reason", None)
                row.pop("replacement_previous_job", None)
            except Exception as exc:
                row["job"].update(status="failed", error=str(exc))
                previous_job = row.pop("replacement_previous_job", None)
                if previous_job:
                    row.setdefault("failed_attempts", []).append(copy.deepcopy(row["job"]))
                    row["job"] = previous_job
                    row["needs_regeneration"] = True
                write_plan(root, plan)
                raise
            write_plan(root, plan)
            preview = output_preview(folder_paths.get_output_directory(), video)
            if preview and callable(getattr(server, "send_sync", None)):
                server.send_sync("h3lv-segment", {
                    "project_id": project_id, "segment_index": index, "preview": preview})
        plan = read_plan(root, project_id)
        if only_segment is not None:
            plan.pop("run_only_segment", None)
            plan["run_status"] = halt_status(plan) or "paused"
            write_plan(root, plan)
            return
        halted = halt_status(plan)
        if halted:
            plan["run_status"] = halted
        else:
            plan["run_status"] = "merging"
            write_plan(root, plan)
            final = await asyncio.to_thread(assemble, root, project_id)
            plan = read_plan(root, project_id)
            previous_final = plan.get("final_video")
            if previous_final and previous_final != final:
                versions = plan.setdefault("final_versions", [])
                if previous_final not in versions:
                    versions.append(previous_final)
            plan.update(run_status="completed", final_video=final, final_stale=False)
        write_plan(root, plan)
        preview = output_preview(folder_paths.get_output_directory(), plan.get("final_video"))
        if preview and callable(getattr(server, "send_sync", None)):
            server.send_sync("h3lv-final", {"project_id": project_id, "preview": preview})
    except Exception as exc:
        plan = read_plan(root, project_id)
        plan.pop("run_only_segment", None)
        plan.update(run_status="failed", error=str(exc))
        write_plan(root, plan)
    finally:
        TASKS.pop(project_id, None)


def start(root, project_id, payload, server):
    with LOCK:
        if project_id in TASKS:
            raise ValueError("该项目已经在生成中。")
        plan = read_plan(root, project_id)
        if not plan.get("approved") or plan.get("approved_fingerprint") != fingerprint(plan):
            raise ValueError("请先保存并确认分段方案。")
        directory = project_path(root, project_id)
        snapshot_file = directory/"state"/"queue_snapshot.json"
        replace_snapshot = bool(payload.get("replace_snapshot"))
        if not any(row.get("job") for row in plan["segments"]) or replace_snapshot:
            prompt = apply_bundled_prompt_rule(copy.deepcopy(payload.get("prompt", {})))
            loader, video = str(payload.get("loader_id", "")), str(payload.get("video_id", ""))
            if prompt.get(loader, {}).get("class_type") not in SEGMENT_NODE_TYPES:
                raise ValueError("请打开含 H3 分段读取节点或一体化节点的视频工作流。")
            if prompt.get(video, {}).get("class_type") != "VHS_VideoCombine":
                raise ValueError("请选择此工作流的 VHS Video Combine 输出节点。")
            snapshot = normalize_output_contract({"prompt": prompt, "loader_id": loader,
                                                  "video_id": video, "workflow": payload.get("workflow", {}),
                                                  "client_id": str(payload.get("client_id") or "").strip()})
            snapshot_file.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        elif not snapshot_file.is_file():
            raise ValueError("缺少原工作流快照，无法安全继续。")
        else:
            snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))
            client_id = str(payload.get("client_id") or "").strip()
            if client_id and snapshot.get("client_id") != client_id:
                snapshot["client_id"] = client_id
                snapshot_file.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        only_segment = payload.get("only_segment_index")
        if only_segment is None:
            plan.pop("run_only_segment", None)
        else:
            only_segment = int(only_segment)
            if not 0 <= only_segment < len(plan["segments"]):
                raise ValueError("要重新生成的片段编号无效。")
            plan["run_only_segment"] = only_segment
        plan.update(run_status="running", pause_requested=False, stop_requested=False, error="")
        write_plan(root, plan)
        TASKS[project_id] = asyncio.create_task(execute_project(root, project_id, server))


def command(args, timeout=1800):
    result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name=="nt" else 0)
    if result.returncode:
        raise RuntimeError("视频合并工具失败："+result.stderr[-2000:])
    return result.stdout


def reveal_command(path, os_name=None, platform=None):
    if (os_name or os.name) == "nt":
        return ["explorer.exe", "/select,", str(path)]
    if (platform or sys.platform) == "darwin":
        return ["open", "-R", str(path)]
    return ["xdg-open", str(Path(path).parent)]


def reveal_file(path):
    """Open the platform file manager and reveal one completed output file."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    args = reveal_command(path)
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(path)


def probe_video(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ValueError("未找到 ffprobe，请配置现有 FFmpeg 到 PATH。插件不会自动安装。")
    data = json.loads(command([ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,nb_read_frames,r_frame_rate,avg_frame_rate,duration", "-of", "json", str(path)]))
    if not data.get("streams"):
        raise ValueError("文件没有视频轨道。")
    return data["streams"][0]


def assemble(root, project_id):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("未找到 FFmpeg，请配置已有 FFmpeg 到 PATH。")
    plan = read_plan(root, project_id)
    directory = project_path(root, project_id)
    work_root = directory / "work"
    work_root.mkdir(exist_ok=True)
    work = work_root / ("assembly_"+uuid.uuid4().hex[:8])
    work.mkdir()
    cache = directory / "cache"
    cache.mkdir(exist_ok=True)
    size, clips = None, []
    for row in plan["segments"]:
        job = row.get("job", {})
        if job.get("status") != "completed":
            raise ValueError("仍有片段未完成，暂不能合并。")
        path = inside(directory, job["video"])
        info = probe_video(path)
        dimensions = (info["width"], info["height"])
        if size and dimensions != size:
            raise ValueError("片段分辨率不一致，请检查工作流。")
        size = dimensions
        rate = info["r_frame_rate"].split("/")
        fps = float(rate[0])/float(rate[1])
        if abs(fps-24) > .001 or int(info["nb_read_frames"]) < row["edit_frames"]:
            raise ValueError("片段必须为 24fps 且有足够帧数，不能靠补帧掩盖缺失内容。")
        stat = path.stat()
        cache_key = hashlib.sha256(json.dumps({
            "path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "edit_frames": row["edit_frames"], "width": dimensions[0], "height": dimensions[1],
            "fps": 24, "codec": "libx264-crf18-yuv420p-v1",
        }, sort_keys=True).encode()).hexdigest()[:16]
        normalized = cache/f"{row['index']:04d}_{cache_key}.mp4"
        if not normalized.is_file():
            temporary = cache/f".{normalized.stem}-{uuid.uuid4().hex}.tmp.mp4"
            # Normalize timestamps, packet duration and frame count once per take.
            # Later assemblies reuse this clip without re-encoding it.
            command([ffmpeg, "-v", "error", "-n", "-i", str(path), "-an", "-vf",
                     f"trim=end_frame={row['edit_frames']},setpts=N/(24*TB)", "-r", "24", "-fps_mode", "cfr",
                     "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                     "-video_track_timescale", "24000", "-movie_timescale", "24000", str(temporary)])
            temporary.replace(normalized)
        linked = work/f"{row['index']:04d}.mp4"
        try:
            os.link(normalized, linked)
        except OSError:
            shutil.copyfile(normalized, linked)
        clips.append(linked)
    listing = work/"concat.txt"
    # Use safe relative generated names, never user-supplied concat directives.
    listing.write_text("\n".join(f"file '{p.name}'" for p in clips), encoding="utf-8")
    temporary_final = work/"final.mp4"
    command([ffmpeg, "-v", "error", "-n", "-f", "concat", "-safe", "1", "-i", str(listing),
             "-i", str(audio_file(directory, "source.wav")), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "320k", "-t", f"{plan['duration']:.9f}",
             "-video_track_timescale", "24000", "-movie_timescale", "24000", "-movflags", "+faststart", str(temporary_final)])
    created = time.localtime(float(plan.get("created") or time.time()))
    stamp = time.strftime("%Y%m%d_%H%M%S", created)
    mode = "speaking" if plan.get("mode") == "speaking" else "singing"
    final_directory = Path(root).resolve().parent / "final_videos"
    final_directory.mkdir(parents=True, exist_ok=True)
    stem = f"{stamp}_{mode}_{project_id[:8]}"
    final = final_directory/f"{stem}.mp4"
    version = 2
    while final.exists():
        final = final_directory/f"{stem}_v{version}.mp4"
        version += 1
    os.replace(temporary_final, final)
    shutil.rmtree(work, ignore_errors=True)
    return str(final)
