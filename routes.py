import asyncio
import copy
import io
import json

from . import controller
from . import ai_director
from . import director_rules
from .core import (LOCK, archive_take, audio_file, edit_plan, fingerprint, inside, preview_bounds,
                   output_preview, project_path, read_plan, read_project_transcript, request_regeneration,
                   segmentation, state_file, write_plan)
from .nodes import data_root, rules_path, settings_path, storage_root


def register_routes():
    from aiohttp import web
    from server import PromptServer
    server = PromptServer.instance
    routes = server.routes

    def endpoint(fn):
        async def wrapped(request):
            try:
                return await fn(request)
            except FileNotFoundError:
                return web.json_response({"error": "项目文件不完整或已被移动，请重新运行音频分析。"}, status=400)
            except (ValueError, KeyError, IndexError) as exc:
                return web.json_response({"error": str(exc)}, status=400)
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=500)
        return wrapped

    @routes.get("/h3lv/settings")
    @endpoint
    async def get_settings(request):
        return web.json_response(ai_director.public_settings(settings_path()))

    @routes.post("/h3lv/settings")
    @endpoint
    async def save_settings(request):
        payload = await request.json()
        return web.json_response(ai_director.write_settings(settings_path(), payload))

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
        return web.json_response(director_rules.reset_rules(rules_path()))

    @routes.get("/h3lv/projects")
    @endpoint
    async def projects(request):
        result = []
        root = data_root()
        if root.exists():
            for file in root.glob("*/state/segments.json"):
                try:
                    plan = read_plan(root, file.parent.parent.name)
                    result.append({"id": plan["id"], "created": plan["created"], "duration": plan["duration"],
                                   "count": len(plan["segments"]), "status": plan["run_status"],
                                   "mode": plan.get("mode", "singing")})
                except (ValueError, OSError):
                    continue
        return web.json_response(sorted(result, key=lambda p: p["created"], reverse=True))

    @routes.get("/h3lv/final-folder")
    @endpoint
    async def final_folder(request):
        path = storage_root()/"final_videos"
        path.mkdir(parents=True, exist_ok=True)
        return web.json_response({"path": str(path)})

    @routes.get("/h3lv/project/{project_id}")
    @endpoint
    async def get_project(request):
        root = data_root()
        plan = read_plan(root, request.match_info["project_id"])
        plan["controller_active"] = plan["id"] in controller.TASKS
        plan["final_preview"] = output_preview(storage_root().parent, plan.get("final_video"))
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
            transcript, _ = read_project_transcript(project_path(root, pid), plan)
            plan = edit_plan(plan, payload["segments"])
            for row in plan["segments"]:
                row["text"] = " / ".join(s["text"] for s in transcript["segments"] if row["start"] <= (s["start"]+s["end"])/2 < row["end"])
            write_plan(root, plan)
        return web.json_response(plan)

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
        return web.Response(body=await asyncio.to_thread(render), content_type="audio/wav", headers={"Cache-Control": "no-store"})

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
        root, pid = data_root(), request.match_info["project_id"]
        with LOCK:
            if pid in controller.TASKS:
                raise ValueError("仍有生成任务运行中。")
            plan = read_plan(root, pid)
            if any(row.get("needs_regeneration") or row.get("job", {}).get("status") != "completed"
                   for row in plan["segments"]):
                raise ValueError("仍有片段需要生成，暂不能只合成。")
            plan["run_status"] = "merging"
            write_plan(root, plan)
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
            preview = output_preview(storage_root().parent, final)
            plan["final_preview"] = preview
            if preview and callable(getattr(server, "send_sync", None)):
                server.send_sync("h3lv-final", {"project_id": pid, "preview": preview})
            return web.json_response(plan)
        except Exception:
            with LOCK:
                plan = read_plan(root, pid)
                plan["run_status"] = "failed"
                write_plan(root, plan)
            raise

    @routes.get("/h3lv/project/{project_id}/final")
    @endpoint
    async def final(request):
        plan = read_plan(data_root(), request.match_info["project_id"])
        file = inside(storage_root()/"final_videos", plan["final_video"])
        return web.FileResponse(file)
