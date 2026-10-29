import asyncio
import copy
import io
import json
from pathlib import Path
import re
import shutil

from . import controller
from . import director_rules
from .diagnostics import download_log, record_error, record_event
from .workflow_errors import failure_report
from .core import (LOCK, REFERENCE_MIRROR_ROOT, UNSET, archive_take, audio_file, edit_plan, fingerprint, inside, preview_bounds,
                   output_preview, project_path, read_plan, read_project_transcript,
                   preview_segment_brief, reference_directory, remove_reference,
                   request_regeneration, segmentation,
                   state_file, store_reference, write_plan)
from .nodes import data_root, final_root, rules_path
from .storage import comfy_output, final_file, project_plans
from .mv_rh import pending, require_settled

RANGE_HEADER = re.compile(r"\s*bytes=(\d*)-(\d*)\s*")


def path_bytes(path):
    path = Path(path)
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if not path.is_dir():
        return 0
    total = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            total += item.stat().st_size
        except OSError:
            continue
    return total


def project_storage_entry(root, project_id, finals, mirrors, active=False):
    directory = project_path(root, project_id)
    plan = read_plan(root, project_id)
    mirror = inside(mirrors, Path(mirrors)/project_id)
    final_files = []
    for value in [plan.get("final_video"), *(plan.get("final_versions") or [])]:
        if not value:
            continue
        try:
            candidate = final_file(root, value)
        except ValueError:
            continue
        if candidate.is_file() and candidate not in final_files:
            final_files.append(candidate)
    return {
        "id": plan["id"], "created": plan["created"], "duration": plan["duration"],
        "count": len(plan["segments"]), "status": plan["run_status"],
        "mode": plan.get("mode", "singing"), "active": bool(active) or any(pending(row.get("job")) for row in plan["segments"]),
        "project_bytes": path_bytes(directory) + path_bytes(mirror),
        "final_bytes": sum(path_bytes(path) for path in final_files),
        "has_final": bool(final_files), "directory": directory, "mirror": mirror,
        "final_files": final_files,
    }


def remove_project_storage(entry, delete_final=False):
    reclaimed = entry["project_bytes"] + (entry["final_bytes"] if delete_final else 0)
    if entry["directory"].is_dir():
        shutil.rmtree(entry["directory"])
    if entry["mirror"].is_dir():
        shutil.rmtree(entry["mirror"])
    if delete_final:
        for path in entry["final_files"]:
            path.unlink(missing_ok=True)
    return reclaimed


def byte_range(header, total):
    """Parse an HTTP Range header into an inclusive (start, end) pair.

    Returns None when the header is absent, malformed or unsatisfiable, so the
    caller serves the whole resource. Without this, browsers treat the audio as
    non-seekable and the player's progress bar cannot be dragged.
    """
    if total <= 0:
        return None
    match = RANGE_HEADER.fullmatch(str(header or ""))
    if not match or (not match.group(1) and not match.group(2)):
        return None
    if match.group(1):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else total - 1
    else:
        length = int(match.group(2))
        if length <= 0:
            return None
        start, end = max(0, total - length), total - 1
    if start >= total or start > end:
        return None
    return start, min(end, total - 1)


def register_routes():
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    routes = server.routes

    def endpoint(fn):
        async def wrapped(request):
            try:
                return await fn(request)
            except Exception as exc:
                record_error("api_route", exc, source=fn.__name__,
                             project_id=request.match_info.get("project_id"))
                if isinstance(exc, FileNotFoundError):
                    return web.json_response({"error": "项目文件不完整或已被移动，请重新运行音频分析。"}, status=400)
                status = 400 if isinstance(exc, (ValueError, KeyError, IndexError)) else 500
                stage = 'video_assembly' if fn.__name__ == 'assemble' else 'api_route'
                return web.json_response({"error": str(exc), "error_info": failure_report(exc, stage)}, status=status)
        return wrapped

    @routes.get('/h3lv/logs/errors')
    @endpoint
    async def error_log_download(request):
        return web.Response(body=await asyncio.to_thread(download_log),
                            content_type="text/plain", charset="utf-8",
                            headers={"Content-Disposition": 'attachment; filename="H3LongVideo-errors.log"',
                                     "Cache-Control": "no-store"})

    @routes.post('/h3lv/rh-check')
    @endpoint
    async def rh_check(request):
        from .mv_rh import check_configuration
        result = await asyncio.to_thread(check_configuration, await request.json())
        return web.json_response(result)

    @routes.post('/h3lv/logs/frontend')
    @endpoint
    async def frontend_error(request):
        try:
            raw = await request.content.readexactly(32769)
        except asyncio.IncompleteReadError as exc:
            raw = exc.partial
        if len(raw) > 32768:
            raise ValueError("错误日志上报内容过大。")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), str):
            raise ValueError("错误日志上报格式无效。")
        context = {name: payload[name] for name in ("source", "action", "project_id", "node_id")
                   if isinstance(payload.get(name), (str, int))}
        recorded = record_event("frontend", payload["message"], "UIError",
                                payload.get("stack") if isinstance(payload.get("stack"), str) else "",
                                **context)
        return web.json_response({"recorded": recorded})

    @routes.get('/h3lv/expansion/settings')
    @endpoint
    async def expansion_settings(request):
        from .expansion import public_settings
        return web.json_response(public_settings())

    @routes.get('/h3lv/rh/settings')
    @endpoint
    async def rh_settings(request):
        from .rh_credentials import public_settings
        return web.json_response(public_settings(request.query.get('base_url', 'https://www.runninghub.ai')), headers={'Cache-Control':'no-store'})

    @routes.post('/h3lv/rh/settings')
    @endpoint
    async def rh_save_settings(request):
        from urllib.parse import urlsplit
        from .rh_credentials import save_settings
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc != request.host:
            raise ValueError('请从当前ComfyUI页面保存RH设置。')
        if request.content_type != 'application/json':
            raise ValueError('RH设置须使用JSON请求。')
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) - {'base_url', 'api_key', 'clear'}:
            raise ValueError('RH设置格式无效。')
        return web.json_response(save_settings(payload.get('base_url'), payload.get('api_key'), payload.get('clear', False)), headers={'Cache-Control':'no-store'})

    @routes.post('/h3lv/expansion/settings')
    @endpoint
    async def expansion_save_settings(request):
        from .expansion import save_settings
        payload = await request.json()
        options = {key: payload[key] for key in ('timeout_seconds', 'response_mode', 'thinking_mode', 'extra_body') if key in payload}
        return web.json_response(save_settings(payload['base_url'], payload.get('api_key'), **options))

    @routes.get('/h3lv/expansion/models')
    @endpoint
    async def expansion_models(request):
        from .expansion import call
        value = await asyncio.to_thread(call, '/models')
        return web.json_response({'models': sorted(str(item['id']) for item in value.get('data', []))})

    @routes.post('/h3lv/project/{project_id}/expand')
    @endpoint
    async def expansion_preview(request):
        from .materials import packet
        from .expansion import expand, cache_key, cache_source, validate_result
        payload = await request.json()
        root, pid = data_root(), request.match_info['project_id']
        with LOCK:
            plan = read_plan(root, pid)
            if plan['revision'] != payload.get('revision'):
                raise ValueError('素材已变化，请刷新后重新扩写。')
            require_settled(plan)
            if plan.get('run_status') in {'running','pausing','stopping','merging'}:
                raise ValueError('请先停止生成任务。')
            index = int(payload['index'])
            if not 0 <= index < len(plan['segments']): raise ValueError('分段编号无效。')
            row = plan['segments'][index]
            material = packet(plan, row, project_path(root, pid))
        args = (material, payload['mode'], payload['model'], payload['rule'], int(payload.get('expansion_revision', 0)))
        key = cache_key(*args)
        if 'text' not in payload:
            source = cache_source(material, key, payload['mode'])
            text = await asyncio.to_thread(expand, *args)
            return web.json_response({'text': text, 'key': key, 'source': source})
        text = validate_result(str(payload['text']), material)
        if payload.get('key') != key: raise ValueError('扩写设置已变化，请重新预览。')
        with LOCK:
            plan = read_plan(root, pid)
            if plan['revision'] != payload['revision'] or plan.get('run_status') in {'running','pausing','stopping','merging'}:
                raise ValueError('项目状态已变化，请刷新。')
            row = plan['segments'][index]
            row.update(expanded_prompt=text, expanded_key=key)
            request_regeneration(row, '扩写提示词已修改')
            plan.update(revision=plan['revision']+1, approved=False, run_status='draft')
            if plan.get('final_video'): plan['final_stale'] = True
            write_plan(root, plan)
        return web.json_response({'revision':plan['revision']})

    @routes.get("/h3lv/rules")
    @endpoint
    async def get_rules(request):
        return web.json_response(director_rules.public_rules(rules_path()))

    @routes.post("/h3lv/rules")
    @endpoint
    async def save_rules(request):
        payload = await request.json()
        return web.json_response(director_rules.write_rules(rules_path(), payload))

    @routes.post("/h3lv/rules/reset")
    @endpoint
    async def reset_rules(request):
        payload = await request.json()
        return web.json_response(director_rules.reset_rules(
            rules_path(), payload.get("mode")))

    @routes.get("/h3lv/projects")
    @endpoint
    async def projects(request):
        result = []
        root = data_root()
        for file in project_plans(root):
            try:
                plan = read_plan(root, file.parent.parent.name)
                result.append({"id": plan["id"], "created": plan["created"], "duration": plan["duration"],
                               "count": len(plan["segments"]), "status": plan["run_status"],
                               "mode": plan.get("mode", "singing")})
            except (ValueError, OSError):
                continue
        return web.json_response(sorted(result, key=lambda p: p["created"], reverse=True))

    @routes.get("/h3lv/projects/manage")
    @endpoint
    async def manage_projects(request):
        import folder_paths
        root, finals = data_root(), final_root()
        mirrors = Path(folder_paths.get_input_directory())/REFERENCE_MIRROR_ROOT

        def scan():
            result = []
            for file in project_plans(root):
                try:
                    entry = project_storage_entry(
                        root, file.parent.parent.name, finals, mirrors,
                        active=file.parent.parent.name in controller.TASKS)
                    result.append({key:value for key, value in entry.items()
                                   if key not in {"directory", "mirror", "final_files"}})
                except (ValueError, OSError):
                    continue
            return sorted(result, key=lambda item:item["created"], reverse=True)

        return web.json_response(await asyncio.to_thread(scan))

    @routes.post("/h3lv/projects/delete")
    @endpoint
    async def delete_projects(request):
        import folder_paths
        payload = await request.json()
        project_ids = list(dict.fromkeys(str(item) for item in payload.get("ids", [])))
        if not project_ids:
            raise ValueError("请至少选择一个要删除的项目。")
        if len(project_ids) > 200:
            raise ValueError("一次最多删除 200 个项目。")
        delete_final = bool(payload.get("delete_final"))
        root, finals = data_root(), final_root()
        mirrors = Path(folder_paths.get_input_directory())/REFERENCE_MIRROR_ROOT
        with LOCK:
            active = [project_id for project_id in project_ids if project_id in controller.TASKS]
            if active:
                raise ValueError("选中的项目仍在生成或合成，请等待任务结束后再删除。")
            entries = [project_storage_entry(root, project_id, finals, mirrors)
                       for project_id in project_ids]
            if any(entry["active"] for entry in entries):
                raise ValueError("项目仍有未解决的RH任务，请先继续查询或绑定任务ID，再删除项目。")
            reclaimed = sum(remove_project_storage(entry, delete_final) for entry in entries)
        return web.json_response({"deleted": project_ids, "delete_final": delete_final,
                                  "reclaimed_bytes": reclaimed})

    @routes.get("/h3lv/project/{project_id}")
    @endpoint
    async def get_project(request):
        root = data_root()
        plan = read_plan(root, request.match_info["project_id"])
        plan["controller_active"] = plan["id"] in controller.TASKS
        if not plan.get("error"):
            plan.pop("error_info", None)
        plan["final_preview"] = output_preview(comfy_output(data_root()), plan.get("final_video"))
        for row in plan["segments"]:
            job = row.get("job") or {}
            preview_job = row.get("replacement_previous_job") if job.get("status") != "completed" else job
            preview_job = preview_job or job
            row["video_preview"] = output_preview(comfy_output(data_root()), preview_job.get("preview_video") or preview_job.get("video"))
        return web.json_response(plan)

    @routes.get("/h3lv/project/{project_id}/analysis")
    @endpoint
    async def get_analysis(request):
        root, pid = data_root(), request.match_info["project_id"]
        directory = project_path(root, pid)
        path = state_file(directory, "analysis.json")
        if path.is_file():
            result = json.loads(path.read_text(encoding="utf-8"))
        else:
            def legacy_analysis():
                import soundfile as sf
                plan = read_plan(root, pid)
                mix, sr = sf.read(audio_file(directory, "source.wav"), dtype="float32", always_2d=True)
                voice, voice_sr = sf.read(audio_file(directory, "vocals.wav"), dtype="float32", always_2d=True)
                if voice_sr != sr or len(voice) != len(mix):
                    raise ValueError("旧项目的原曲与人声文件长度不一致，无法绘制诊断波形。")
                transcript, reconstructed = read_project_transcript(directory, plan)
                _, value = segmentation(voice, sr, transcript, plan["mode"], plan["max_seconds"],
                                        plan["target_seconds"], mix_audio=mix, return_analysis=True)
                value.update(legacy=True,
                    legacy_notice=("旧项目缺少识别缓存，已用现有分段文本恢复预览；不会改写原分段。"
                                   if reconstructed else
                                   "旧项目诊断数据为打开面板时临时计算；不会改写原分段。"))
                return value
            result = await asyncio.to_thread(legacy_analysis)
        result["available"] = True
        return web.json_response(result)

    @routes.post("/h3lv/project/{project_id}/edit")
    @endpoint
    async def edit(request):
        payload = await request.json()
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            plan = read_plan(root, pid)
            if int(payload.get("revision", -1)) != plan["revision"]:
                raise ValueError("方案已被另一个窗口修改，请刷新。")
            if ('split_time' in payload or 'remove_cut' in payload) and pid in controller.TASKS:
                raise ValueError('请等当前任务停止后再增删切点。')
            require_settled(plan)
            transcript, _ = read_project_transcript(project_path(root, pid), plan)
            plan = edit_plan(plan, payload["segments"], project_path(root, pid),
                             payload.get("reference_default_count", UNSET), payload.get("materials", UNSET),
                             split_time=payload.get("split_time", UNSET),
                             remove_cut=payload.get("remove_cut", UNSET))
            for row in plan["segments"]:
                row["text"] = " / ".join(s["text"] for s in transcript["segments"] if row["start"] <= (s["start"]+s["end"])/2 < row["end"])
            write_plan(root, plan)
        return web.json_response(plan)

    @routes.post("/h3lv/project/{project_id}/brief-preview")
    @endpoint
    async def brief_preview(request):
        payload = await request.json()
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            plan = read_plan(root, pid)
            if int(payload.get("revision", -1)) != plan["revision"]:
                raise ValueError("项目已变化，请刷新后重试。")
            text = preview_segment_brief(
                plan, payload.get("index"), str(payload.get("visual_type") or ""))
        return web.json_response({"prompt": text})

    @routes.post("/h3lv/project/{project_id}/approve")
    @endpoint
    async def approve(request):
        payload = await request.json()
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            plan = read_plan(root, pid)
            if plan.get("run_status") in {"running", "pausing", "stopping", "merging"}:
                raise ValueError("任务运行中。")
            if payload.get("revision") != plan["revision"]:
                raise ValueError("请刷新后重新确认。")
            plan.update(approved=True, approved_fingerprint=fingerprint(plan))
            write_plan(root, plan)
        return web.json_response(plan)

    @routes.post("/h3lv/project/{project_id}/refs")
    @endpoint
    async def upload_reference(request):
        root, pid = data_root(), request.match_info["project_id"]
        directory = project_path(root, pid)
        reader = await request.multipart()
        index, filename, content = 0, "", b""
        while (part := await reader.next()) is not None:
            if part.name == "index":
                index = int((await part.text()).strip() or 0)
            elif part.name == "image":
                filename = part.filename or ""
                content = await part.read(decode=False)
        if not filename:
            raise ValueError("没有收到参考图文件。")
        with LOCK:
            plan = read_plan(root, pid)
            if plan.get("run_status") in {"running", "pausing", "stopping", "merging"}:
                raise ValueError("生成任务正在运行，请先停止再修改参考图。")
            require_settled(plan)
            if not 0 <= index < len(plan["segments"]):
                raise ValueError("分段编号超出范围。")
            name = store_reference(directory, index, filename, content)
        return web.json_response({"name": name, "index": index})

    @routes.get("/h3lv/project/{project_id}/refs/{name}")
    @endpoint
    async def reference_file(request):
        root, pid = data_root(), request.match_info["project_id"]
        directory = project_path(root, pid)
        folder = reference_directory(directory)
        path = inside(folder, folder/request.match_info["name"])
        if not path.is_file():
            raise FileNotFoundError(str(path))
        return web.FileResponse(path, headers={"Cache-Control": "no-store"})

    @routes.post("/h3lv/project/{project_id}/refs/remove")
    @endpoint
    async def delete_reference(request):
        payload = await request.json()
        root, pid = data_root(), request.match_info["project_id"]
        directory = project_path(root, pid)
        with LOCK:
            plan = read_plan(root, pid)
            if plan.get("run_status") in {"running", "pausing", "stopping", "merging"}:
                raise ValueError("生成任务正在运行，请先停止再修改参考图。")
            require_settled(plan)
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ValueError("缺少要删除的参考图。")
            if name in plan.get("default_refs", []) or any(name in (row.get("refs") or []) for row in plan["segments"]):
                raise ValueError("该参考图仍被某个分段使用，请先在分段卡片中移除。")
            remove_reference(directory, name)
        return web.json_response({"removed": name})

    @routes.get("/h3lv/project/{project_id}/audio")
    @endpoint
    async def audio(request):
        import soundfile as sf
        plan = read_plan(data_root(), request.match_info["project_id"])
        a, b = preview_bounds(plan, request.query.get("index", 0),
            request.query.get("start"), request.query.get("end"), request.query.get("boundary") == "1")
        name = "vocals.wav" if request.query.get("vocals") == "1" else "source.wav"
        path = audio_file(project_path(data_root(), plan["id"]), name)
        def render():
            samples, sr = sf.read(path, start=a, stop=b, dtype="float32", always_2d=True)
            data = io.BytesIO()
            sf.write(data, samples, sr, format="WAV", subtype="PCM_16")
            return data.getvalue()
        body = await asyncio.to_thread(render)
        headers = {"Cache-Control": "no-store", "Accept-Ranges": "bytes"}
        span = byte_range(request.headers.get("Range"), len(body))
        if span is None:
            return web.Response(body=body, content_type="audio/wav", headers=headers)
        start, end = span
        headers["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
        return web.Response(body=body[start:end+1], status=206,
                            content_type="audio/wav", headers=headers)

    @routes.post("/h3lv/project/{project_id}/run")
    @endpoint
    async def run(request):
        payload = await request.json()
        controller.start(data_root(), request.match_info["project_id"], payload, server)
        return web.json_response({"started": True})

    @routes.post("/h3lv/project/{project_id}/pause")
    @endpoint
    async def pause(request):
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            plan = read_plan(root, pid)
            plan.update(pause_requested=True)
            if pid in controller.TASKS and plan["run_status"] == "running":
                plan["run_status"] = "pausing"
            write_plan(root, plan)
        return web.json_response({"message": "当前片段完成后暂停；不会中断其他任务。"})

    @routes.post("/h3lv/project/{project_id}/rh-recover")
    @endpoint
    async def rh_recover(request):
        payload = await request.json()
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            if pid in controller.TASKS:
                raise ValueError("请等待当前查询停止。")
            plan = read_plan(root, pid)
            index = int(payload["index"])
            if not 0 <= index < len(plan["segments"]):
                raise ValueError("分段编号无效。")
            job = plan["segments"][index].get("job") or {}
            task_id = str(payload.get("task_id") or "").strip()
            if job.get("backend") != "runninghub" or job.get("status") != "creating_unknown" or not task_id.isdigit():
                raise ValueError("仅创建结果未知的RH段可绑定有效任务ID。")
            job.update(task_id=task_id, status="submitted")
            write_plan(root, plan)
        return web.json_response({"recovered": True, "task_id": task_id})

    @routes.post("/h3lv/project/{project_id}/stop")
    @endpoint
    async def stop(request):
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            plan = read_plan(root, pid)
            if plan.get("run_status") == "merging":
                raise ValueError("正在合并最终视频，请等待合并完成。")
            plan["stop_requested"] = True
            if pid in controller.TASKS:
                plan["run_status"] = "stopping"
            else:
                plan["run_status"] = "stopped"
            write_plan(root, plan)
        return web.json_response({"message": "停止请求已记录；当前片段完成后不会再提交后续片段。"})

    @routes.post("/h3lv/project/{project_id}/retry")
    @endpoint
    async def retry(request):
        payload = await request.json()
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            if pid in controller.TASKS:
                raise ValueError("请等待当前任务停止。")
            plan = read_plan(root, pid)
            row = plan["segments"][int(payload["index"])]
            if row.get("job", {}).get("prompt_id") in controller.queued_ids(server):
                raise ValueError("该段仍在队列中，不能重复提交。")
            request_regeneration(row, "用户要求重新生成本段")
            plan.update(run_status="paused", error="")
            if plan.get("final_video"):
                plan["final_stale"] = True
            write_plan(root, plan)
        return web.json_response(plan)

    @routes.post("/h3lv/project/{project_id}/regenerate")
    @endpoint
    async def regenerate(request):
        payload = await request.json()
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            if pid in controller.TASKS:
                raise ValueError("请等待当前任务停止。")
            plan = read_plan(root, pid)
            row = plan["segments"][int(payload["index"])]
            if row.get("job", {}).get("prompt_id") in controller.queued_ids(server):
                raise ValueError("该段仍在队列中，不能重复提交。")
            request_regeneration(row, "用户要求重新生成本段")
            plan.update(run_status="paused", error="")
            if plan.get("final_video"):
                plan["final_stale"] = True
            write_plan(root, plan)
        payload["replace_snapshot"] = True
        payload["only_segment_index"] = int(payload["index"])
        controller.start(root, pid, payload, server)
        return web.json_response({"started": True, "index": int(payload["index"])})

    @routes.post("/h3lv/project/{project_id}/restore")
    @endpoint
    async def restore(request):
        payload = await request.json()
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            if pid in controller.TASKS:
                raise ValueError("请等待当前任务停止。")
            plan = read_plan(root, pid)
            require_settled(plan)
            row = plan["segments"][int(payload["index"])]
            takes = row.setdefault("takes", [])
            if not takes:
                raise ValueError("该段没有可恢复的旧版本。")
            previous = takes.pop()
            current = row.get("job")
            if current and current.get("status") == "completed":
                archive_take(row, current)
            row["job"] = copy.deepcopy(previous)
            row.pop("needs_regeneration", None)
            row.pop("regeneration_reason", None)
            row.pop("replacement_previous_job", None)
            plan.update(run_status="paused", final_stale=True, error="")
            write_plan(root, plan)
        return web.json_response(plan)

    @routes.post("/h3lv/project/{project_id}/assemble")
    @endpoint
    async def assemble_only(request):
        from .progress import report
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            if pid in controller.TASKS:
                raise ValueError("仍有生成任务运行中。")
            plan = read_plan(root, pid)
            if plan.get("run_status") == "merging":
                raise ValueError("正在合并最终视频，请等待合并完成。")
            if any(row.get("needs_regeneration") or row.get("job", {}).get("status") != "completed"
                   for row in plan["segments"]):
                raise ValueError("仍有片段需要生成，暂不能只合成。")
            plan["run_status"] = "merging"
            write_plan(root, plan)
        report(root, pid, 'merge')
        try:
            final = await asyncio.to_thread(controller.assemble, root, pid)
            with LOCK:
                plan = read_plan(root, pid)
                previous_final = plan.get("final_video")
                if previous_final and previous_final != final:
                    versions = plan.setdefault("final_versions", [])
                    if previous_final not in versions:
                        versions.append(previous_final)
                plan.update(run_status="completed", final_video=final, final_stale=False, error="")
                write_plan(root, plan)
            report(root, pid, 'completed')
            plan = read_plan(root, pid)
            preview = output_preview(comfy_output(data_root()), final)
            plan["final_preview"] = preview
            if preview and callable(getattr(server, "send_sync", None)):
                server.send_sync("h3lv-final", {"project_id": pid, "preview": preview,
                                               "video_id": plan.get("video_output_id")})
            return web.json_response(plan)
        except Exception as exc:
            with LOCK:
                plan = read_plan(root, pid)
                plan.update(run_status="failed", error=str(exc))
                write_plan(root, plan)
            report(root, pid, 'failed')
            raise

    @routes.get("/h3lv/project/{project_id}/final")
    @endpoint
    async def final(request):
        plan = read_plan(data_root(), request.match_info["project_id"])
        file = final_file(data_root(), plan["final_video"])
        return web.FileResponse(file, headers={"Cache-Control": "no-store"})

    @routes.post("/h3lv/project/{project_id}/reveal-final")
    @endpoint
    async def reveal_final(request):
        plan = read_plan(data_root(), request.match_info["project_id"])
        file = final_file(data_root(), plan["final_video"])
        revealed = await asyncio.to_thread(controller.reveal_file, file)
        return web.json_response({"path": revealed})
