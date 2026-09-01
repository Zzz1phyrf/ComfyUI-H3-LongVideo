"""Editable local singing and speaking rules with a validated runtime contract."""

import copy
import hashlib
import json
from pathlib import Path
import uuid


CONFIG_NAME = "director_config.json"
MOVEMENTS = {
    "steady", "dolly_in", "dolly_out", "truck_left", "truck_right",
    "micro_reframe", "arc_left", "arc_right",
}
FRAMINGS = {"medium close-up", "medium shot"}
ANGLES = {"front", "front three-quarter left", "front three-quarter right"}


def _default_directory():
    return Path(__file__).with_name("defaults")


def _read_default_config():
    return json.loads((_default_directory() / CONFIG_NAME).read_text(encoding="utf-8"))


def _string_list(source, name, allowed, *, minimum=1):
    supplied = source.get(name)
    if not isinstance(supplied, list) or len(supplied) < minimum:
        raise ValueError(f"导演规则 {name} 至少需要 {minimum} 个值。")
    cleaned = []
    for item in supplied:
        item = str(item).strip()
        if item not in allowed:
            raise ValueError(f"导演规则 {name} 包含不支持的值：{item}")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def _movement_family(value):
    if value.startswith("truck_"):
        return "lateral"
    if value.startswith("arc_"):
        return "arc"
    if value.startswith("dolly_"):
        return "dolly"
    if value == "micro_reframe":
        return "micro"
    return value


def _upgrade_schema_1(value):
    """Preserve an old movement vocabulary while adopting constrained pools."""
    singing = value.get("singing") or {}
    allowed = [str(item) for item in singing.get("allowed_movements") or []]
    allowed = [item for item in allowed if item in MOVEMENTS and item != "steady"]
    if len({_movement_family(item) for item in allowed}) < 2:
        allowed = ["micro_reframe", "arc_left", "arc_right", "truck_left", "truck_right"]

    def pool(preferred):
        result = [item for item in preferred if item in allowed]
        if len({_movement_family(item) for item in result}) < 2:
            result = list(allowed)
        return result

    return {
        "schema": 2,
        "singing": {
            "allowed_framings": singing.get("allowed_framings") or ["medium close-up"],
            "allowed_angles": singing.get("allowed_angles") or [
                "front", "front three-quarter right", "front three-quarter left"],
            "energy_movements": {
                "low": pool(["micro_reframe", "arc_left", "arc_right", "dolly_out"]),
                "medium": pool(["arc_left", "arc_right", "truck_left", "truck_right", "micro_reframe"]),
                "high": pool(["dolly_in", "dolly_out", "arc_left", "arc_right", "truck_left", "truck_right"]),
            },
            "every_segment_moves": bool(singing.get("every_segment_moves", True)),
            "no_adjacent_same_family": True,
            "alternate_lateral_direction": True,
            "avoid_direct_axis_cross": True,
        },
        "speaking": value.get("speaking") or {
            "framing": "medium close-up", "angle": "front", "movement": "steady",
            "keep_composition_across_segments": True,
        },
    }


def validate_config(value):
    if not isinstance(value, dict):
        raise ValueError("导演规则配置必须是 JSON 对象。")
    schema = int(value.get("schema", 0))
    if schema == 1:
        value = _upgrade_schema_1(value)
    elif schema != 2:
        raise ValueError("导演规则配置必须使用 schema 2。")

    singing = value.get("singing")
    speaking = value.get("speaking")
    if not isinstance(singing, dict) or not isinstance(speaking, dict):
        raise ValueError("运镜规则必须同时包含 singing 和 speaking。")

    energy = singing.get("energy_movements")
    if not isinstance(energy, dict):
        raise ValueError("唱歌规则必须包含 energy_movements。")
    energy_result = {}
    for band in ("low", "medium", "high"):
        values = _string_list(energy, band, MOVEMENTS - {"steady"}, minimum=2)
        if len({_movement_family(item) for item in values}) < 2:
            raise ValueError(f"唱歌规则 energy_movements.{band} 至少需要两类不同运镜。")
        energy_result[band] = values

    singing_angles = _string_list(singing, "allowed_angles", ANGLES, minimum=2)
    if "front" not in singing_angles:
        raise ValueError("唱歌规则 allowed_angles 必须包含 front 以保持可读表演轴线。")
    singing_result = {
        "allowed_framings": _string_list(singing, "allowed_framings", FRAMINGS),
        "allowed_angles": singing_angles,
        "energy_movements": energy_result,
        "every_segment_moves": bool(singing.get("every_segment_moves", True)),
        "no_adjacent_same_family": bool(singing.get("no_adjacent_same_family", True)),
        "alternate_lateral_direction": bool(singing.get("alternate_lateral_direction", True)),
        "avoid_direct_axis_cross": bool(singing.get("avoid_direct_axis_cross", True)),
    }
    if not singing_result["every_segment_moves"]:
        raise ValueError("当前唱歌规则要求每段都有运镜，every_segment_moves 必须为 true。")
    if not singing_result["no_adjacent_same_family"]:
        raise ValueError("当前唱歌规则要求相邻片段运镜不重复，no_adjacent_same_family 必须为 true。")

    framing = str(speaking.get("framing", "")).strip()
    angle = str(speaking.get("angle", "")).strip()
    movement = str(speaking.get("movement", "")).strip()
    if framing not in FRAMINGS or angle not in ANGLES or movement not in MOVEMENTS:
        raise ValueError("口播规则包含不支持的景别、角度或运镜。")
    if movement != "steady":
        raise ValueError("当前口播模式使用固定机位，speaking.movement 必须为 steady。")
    speaking_result = {
        "framing": framing,
        "angle": angle,
        "movement": movement,
        "keep_composition_across_segments": bool(
            speaking.get("keep_composition_across_segments", True)),
    }
    return {"schema": 2, "singing": singing_result, "speaking": speaking_result}


def _write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def ensure_rules(directory):
    directory = Path(directory)
    config_path = directory / CONFIG_NAME
    if not config_path.is_file():
        _write_atomic(config_path, json.dumps(
            validate_config(_read_default_config()), ensure_ascii=False, indent=2) + "\n")
    return directory


def read_rules(directory):
    directory = ensure_rules(directory)
    try:
        raw = json.loads((directory / CONFIG_NAME).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("运镜规则 JSON 格式无效。") from exc
    config = validate_config(raw)
    serialized = json.dumps(config, ensure_ascii=False, sort_keys=True)
    revision = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    return {"config": config, "revision": revision, "directory": str(directory)}


def public_rules(directory):
    result = read_rules(directory)
    return {"config_text": json.dumps(result["config"], ensure_ascii=False, indent=2),
            "revision": result["revision"], "directory": result["directory"]}


def write_rules(directory, payload):
    current = read_rules(directory)
    config_text = payload.get("config_text")
    try:
        config = current["config"] if config_text is None else json.loads(str(config_text))
    except json.JSONDecodeError as exc:
        raise ValueError("运镜规则 JSON 格式无效。") from exc
    config = validate_config(config)
    directory = Path(directory)
    _write_atomic(directory / CONFIG_NAME,
                  json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return public_rules(directory)


def reset_rules(directory, mode=None):
    directory = Path(directory)
    defaults = validate_config(_read_default_config())
    if mode is not None:
        mode = str(mode).strip()
        if mode not in {"singing", "speaking"}:
            raise ValueError("只能恢复 singing 或 speaking 的默认规则。")
        config = read_rules(directory)["config"]
        config[mode] = copy.deepcopy(defaults[mode])
        defaults = validate_config(config)
    _write_atomic(directory / CONFIG_NAME,
                  json.dumps(defaults, ensure_ascii=False, indent=2) + "\n")
    return public_rules(directory)


def default_config():
    return copy.deepcopy(validate_config(_read_default_config()))
