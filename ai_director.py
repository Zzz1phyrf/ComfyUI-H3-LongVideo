"""Optional OpenAI-compatible whole-song director with no raw-audio upload."""

import base64
import io
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.8-max"


def read_settings(path):
    path = Path(path)
    if not path.is_file():
        return {"base_url": DEFAULT_BASE_URL, "model": DEFAULT_MODEL, "api_key": ""}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("AI导演设置文件格式无效。")
    return {"base_url": str(value.get("base_url") or DEFAULT_BASE_URL).strip(),
            "model": str(value.get("model") or DEFAULT_MODEL).strip(),
            "api_key": str(value.get("api_key") or "").strip()}


def public_settings(path):
    settings = read_settings(path)
    return {"base_url": settings["base_url"], "model": settings["model"],
            "api_key_configured": bool(settings["api_key"])}


def validate_settings(settings):
    parsed = urllib.parse.urlparse(settings["base_url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("AI导演服务地址必须是有效的 HTTP(S) 地址，且不能在地址中包含账号或密码。")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程 AI导演服务必须使用 HTTPS；HTTP 仅允许本机地址。")
    if not settings["model"]:
        raise ValueError("请填写 AI导演模型名称。")
    if not settings["api_key"]:
        raise ValueError("尚未配置 AI导演 API Key。请在分段与生成控制界面打开“AI导演设置”。")
    return settings


def write_settings(path, payload):
    path = Path(path)
    current = read_settings(path)
    value = {"base_url": str(payload.get("base_url") or current["base_url"]).strip(),
             "model": str(payload.get("model") or current["model"]).strip(),
             "api_key": str(payload.get("api_key") or current["api_key"]).strip()}
    validate_settings(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return public_settings(path)


def _image_data_url(image):
    import numpy as np
    from PIL import Image
    value = image.detach().cpu().float().numpy() if hasattr(image, "detach") else np.asarray(image)
    if value.ndim == 4:
        if value.shape[0] != 1:
            raise ValueError("AI导演每个参考图接口只支持一张图片，不支持 IMAGE batch。")
        value = value[0]
    if value.ndim != 3 or value.shape[-1] not in {3, 4}:
        raise ValueError("AI导演收到的参考图格式无效。")
    value = np.clip(value[..., :3]*255, 0, 255).astype("uint8")
    picture = Image.fromarray(value, "RGB")
    picture.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    picture.save(stream, format="JPEG", quality=88, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def _endpoint(base_url):
    value = base_url.rstrip("/")
    return value if value.endswith("/chat/completions") else value + "/chat/completions"


def _extract_json(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("AI导演没有返回有效 JSON，未改动本次分镜方案。") from exc
    if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
        raise ValueError("AI导演返回结果缺少 segments 数组。")
    return result["segments"]


def plan_shots(plan, images, settings, timeout=180):
    settings = validate_settings(dict(settings))
    if plan.get("mode") != "singing":
        raise ValueError("固定机位口播使用本地连续性规则，不调用AI镜头导演。")
    manifest = plan["references"]
    required = int(manifest["picture_count"])
    if len(images) < required or any(image is None for image in images[:required]):
        raise ValueError(f"当前图片组合需要把 {required} 张参考图同时连接到 H3 长视频节点的参考图接口。")
    segments = [{"index": int(row["index"]), "start": round(float(row["start"]), 3),
                 "end": round(float(row["end"]), 3), "duration": round(float(row["duration"]), 3),
                 "relative_energy": row.get("relative_energy", "medium"),
                 "recognized_phrase_hint": str(row.get("text", ""))[:500]}
                for row in plan["segments"]]
    creative_rule = str(plan["director"].get("ai_rule") or "").strip()
    if not creative_rule:
        raise ValueError("当前项目缺少可编辑的 AI 导演规则，请打开“导演规则”恢复默认。")
    rule_config = plan["director"].get("rule_config") or {}
    singing_rules = rule_config.get("singing") or {}
    framings = list(singing_rules.get("allowed_framings") or ["medium close-up"])
    angles = list(singing_rules.get("allowed_angles") or ["front"])
    movements = list(singing_rules.get("allowed_movements") or ["truck_right"])
    schema = {"segments": [{"index": 0, "opening_framing": framings[0],
                             "opening_angle": angles[0], "movement": movements[0]}]}
    activity = plan["director"].get("camera_activity", "auto")
    motion_target = {
        "auto": "Give every segment visible physical camera movement; use gentler motion for low-energy segments and stronger but controlled motion for high-energy segments.",
        "moderate": "Give every segment visible, restrained physical camera movement at a slow or moderate pace.",
        "dynamic": "Give every segment visible physical camera movement with energetic but controlled pacing.",
    }.get(activity, "Give every segment visible physical camera movement.")
    instructions = (
        "Editable director rule:\n" + creative_rule + "\n\n"
        "Runtime shot-list contract:\n"
        "Use the supplied reference manifest as the source of performer and environment roles. "
        "Return exactly one continuous-shot item per input segment, in matching index order. "
        f"opening_framing uses one of {json.dumps(framings, ensure_ascii=False)}. "
        f"opening_angle uses one of {json.dumps(angles, ensure_ascii=False)}. "
        f"movement uses one of {json.dumps(movements, ensure_ascii=False)}. "
        + motion_target + " "
        "Consecutive segments that continue the same lateral movement may keep the same opening angle for a motion-matched cut. "
        "Never switch directly between front three-quarter left and front three-quarter right; return through front when changing sides. "
        "The local validator checks segment count, reference roles and supported combinations, and repairs a safe front-angle transition when possible. "
        "Return JSON only. Example shape: " + json.dumps(schema, ensure_ascii=False) + "\n\n" +
        "Project context:\n" + json.dumps({"content_mode": plan.get("mode"),
                                             "references": manifest,
                                             "director": {
                                                 "camera_activity": plan["director"].get("camera_activity"),
                                                 "widest_framing": plan["director"].get("widest_framing"),
                                                 "rule_revision": plan["director"].get("rule_revision"),
                                                 "rule_config": rule_config,
                                             },
                                             "audio_structure": plan.get("audio_structure", {}),
                                             "segments": segments}, ensure_ascii=False))
    content = [{"type": "text", "text": instructions}]
    for index, image in enumerate(images[:required], 1):
        content.append({"type": "text", "text": f"Picture {index} follows the reference manifest role for Picture {index}."})
        content.append({"type": "image_url", "image_url": {"url": _image_data_url(image)}})
    payload = {"model": settings["model"], "temperature": 0.25,
               "messages": [{"role": "system", "content": "You plan concise shot lists for independently generated reference-to-video singing clips. Return strict JSON only."},
                            {"role": "user", "content": content}]}
    request = urllib.request.Request(_endpoint(settings["base_url"]),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + settings["api_key"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"AI导演请求失败（HTTP {exc.code}）。请检查模型权限、额度和服务地址。") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("无法连接 AI导演服务。请检查网络、代理和服务地址。") from exc
    try:
        message = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("AI导演服务返回了无法识别的响应格式。") from exc
    return _extract_json(message)
