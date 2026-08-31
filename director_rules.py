"""Editable director rules with a fixed, validated runtime contract."""

import copy
import hashlib
import json
from pathlib import Path
import uuid


AI_RULE_NAME = "ai_director_rule.txt"
CONFIG_NAME = "director_config.json"


def _default_directory():
    return Path(__file__).with_name("defaults")


def _read_defaults():
    directory = _default_directory()
    return {
        "ai_rule": (directory / AI_RULE_NAME).read_text(encoding="utf-8").strip(),
        "config": json.loads((directory / CONFIG_NAME).read_text(encoding="utf-8")),
    }


def validate_config(value):
    if not isinstance(value, dict) or int(value.get("schema", 0)) != 1:
        raise ValueError("导演规则配置必须使用 schema 1。")
    result = {"schema": 1}
    singing = value.get("singing")
    speaking = value.get("speaking")
    if not isinstance(singing, dict) or not isinstance(speaking, dict):
        raise ValueError("导演规则必须同时包含 singing 和 speaking。")

    framing_values = {"medium close-up", "medium shot"}
    angle_values = {"front", "front three-quarter left", "front three-quarter right"}
    movement_values = {"steady", "dolly_in", "truck_left", "truck_right"}

    def string_list(source, name, allowed, unique=True):
        supplied = source.get(name)
        if not isinstance(supplied, list) or not supplied:
            raise ValueError(f"导演规则 {name} 必须是非空数组。")
        cleaned = []
        for item in supplied:
            item = str(item).strip()
            if item not in allowed:
                raise ValueError(f"导演规则 {name} 包含不支持的值：{item}")
            if not unique or item not in cleaned:
                cleaned.append(item)
        return cleaned

    singing_result = {
        "allowed_framings": string_list(singing, "allowed_framings", framing_values),
        "allowed_angles": string_list(singing, "allowed_angles", angle_values),
        "allowed_movements": string_list(singing, "allowed_movements", movement_values),
        # A shot rhythm is an ordered sequence. Repeated directions are
        # meaningful because adjacent segments can deliberately continue the
        # same screen motion before changing direction.
        "movement_pattern": string_list(
            singing, "movement_pattern", movement_values, unique=False),
        "every_segment_moves": bool(singing.get("every_segment_moves", True)),
        "constant_subject_scale": bool(singing.get("constant_subject_scale", True)),
    }
    if any(item not in singing_result["allowed_movements"]
           for item in singing_result["movement_pattern"]):
        raise ValueError("movement_pattern 只能使用 allowed_movements 中已允许的运镜。")
    if singing_result["every_segment_moves"] and "steady" in singing_result["movement_pattern"]:
        raise ValueError("every_segment_moves 为 true 时，movement_pattern 不能包含 steady。")
    if "dolly_in" in singing_result["allowed_movements"] \
            and "medium shot" not in singing_result["allowed_framings"]:
        raise ValueError("允许 dolly_in 时必须同时允许 medium shot。")
    if singing_result["constant_subject_scale"] and "dolly_in" in singing_result["movement_pattern"]:
        raise ValueError("constant_subject_scale 为 true 时不能在 movement_pattern 中使用 dolly_in。")

    framing = str(speaking.get("framing", "")).strip()
    angle = str(speaking.get("angle", "")).strip()
    movement = str(speaking.get("movement", "")).strip()
    if framing not in framing_values or angle not in angle_values or movement not in movement_values:
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
    result.update(singing=singing_result, speaking=speaking_result)
    return result


def _write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def ensure_rules(directory):
    directory = Path(directory)
    defaults = _read_defaults()
    ai_path, config_path = directory / AI_RULE_NAME, directory / CONFIG_NAME
    if not ai_path.is_file():
        _write_atomic(ai_path, defaults["ai_rule"] + "\n")
    if not config_path.is_file():
        _write_atomic(config_path, json.dumps(
            validate_config(defaults["config"]), ensure_ascii=False, indent=2) + "\n")
    return directory


def read_rules(directory):
    directory = ensure_rules(directory)
    ai_rule = (directory / AI_RULE_NAME).read_text(encoding="utf-8").strip()
    if not ai_rule:
        raise ValueError("AI 导演规则不能为空。")
    if len(ai_rule) > 30000:
        raise ValueError("AI 导演规则超过 30000 字符，请精简后保存。")
    try:
        config = json.loads((directory / CONFIG_NAME).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("导演规则 JSON 格式无效。") from exc
    config = validate_config(config)
    serialized = json.dumps(config, ensure_ascii=False, sort_keys=True)
    revision = hashlib.sha256((ai_rule + "\n" + serialized).encode("utf-8")).hexdigest()[:12]
    return {"ai_rule": ai_rule, "config": config, "revision": revision,
            "directory": str(directory)}


def public_rules(directory):
    result = read_rules(directory)
    return {"ai_rule": result["ai_rule"],
            "config_text": json.dumps(result["config"], ensure_ascii=False, indent=2),
            "revision": result["revision"], "directory": result["directory"]}


def write_rules(directory, payload):
    current = read_rules(directory)
    ai_rule = str(payload.get("ai_rule", current["ai_rule"])).strip()
    if not ai_rule:
        raise ValueError("AI 导演规则不能为空。")
    if len(ai_rule) > 30000:
        raise ValueError("AI 导演规则超过 30000 字符，请精简后保存。")
    config_text = payload.get("config_text")
    try:
        config = (current["config"] if config_text is None
                  else json.loads(str(config_text)))
    except json.JSONDecodeError as exc:
        raise ValueError("导演规则 JSON 格式无效。") from exc
    config = validate_config(config)
    directory = Path(directory)
    _write_atomic(directory / AI_RULE_NAME, ai_rule + "\n")
    _write_atomic(directory / CONFIG_NAME,
                  json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return public_rules(directory)


def reset_rules(directory):
    defaults = _read_defaults()
    directory = Path(directory)
    _write_atomic(directory / AI_RULE_NAME, defaults["ai_rule"] + "\n")
    _write_atomic(directory / CONFIG_NAME,
                  json.dumps(validate_config(defaults["config"]), ensure_ascii=False, indent=2) + "\n")
    return public_rules(directory)


def default_config():
    return copy.deepcopy(validate_config(_read_defaults()["config"]))
