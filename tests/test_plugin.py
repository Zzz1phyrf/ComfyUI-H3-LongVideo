import asyncio
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
import shutil
from unittest.mock import MagicMock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("h3lv_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("h3lv_test", package)
core = importlib.import_module("h3lv_test.core")
controller = importlib.import_module("h3lv_test.controller")
nodes = importlib.import_module("h3lv_test.nodes")
ai_director = importlib.import_module("h3lv_test.ai_director")
director_rules = importlib.import_module("h3lv_test.director_rules")


def wide_rule_config(include_steady=False):
    movements = ["truck_left", "truck_right", "dolly_in"]
    if include_steady:
        movements.append("steady")
    return director_rules.validate_config({
        "schema": 1,
        "singing": {
            "allowed_framings": ["medium close-up", "medium shot"],
            "allowed_angles": ["front", "front three-quarter right", "front three-quarter left"],
            "allowed_movements": movements,
            "movement_pattern": ["truck_right", "dolly_in", "truck_left"],
            "every_segment_moves": True,
            "constant_subject_scale": False,
        },
        "speaking": {"framing": "medium close-up", "angle": "front",
                     "movement": "steady", "keep_composition_across_segments": True},
    })


def sample_plan():
    return core.decorate({"id": "a"*32, "sample_rate": 100, "samples": 3000, "duration": 30,
        "mode": "singing", "max_seconds": 15, "target_seconds": 11,
        "director": {"performance_intensity": "auto", "camera_activity": "auto",
                     "widest_framing": "medium close-up", "note": "",
                     "rule_config": director_rules.default_config(),
                     "ai_rule": (ROOT/"defaults"/"ai_director_rule.txt").read_text(encoding="utf-8")},
        "approved": False, "revision": 1, "run_status": "draft", "created": 0,
        "segments": [{"start_sample": i*1000, "end_sample": (i+1)*1000, "energy_db": -30+i*10,
                      "text": "未校对歌词"} for i in range(3)]})


class CoreTests(unittest.TestCase):
    def test_final_output_has_vhs_preview_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            final = output/"H3LongVideo"/"final_videos"/"20260831_130351_singing_ffffffff.mp4"
            final.parent.mkdir(parents=True)
            final.write_bytes(b"video")
            preview = core.output_preview(output, final)
        self.assertEqual(preview["filename"], "20260831_130351_singing_ffffffff.mp4")
        self.assertEqual(preview["subfolder"], "H3LongVideo/final_videos")
        self.assertEqual(preview["type"], "output")
        self.assertEqual(preview["format"], "video/h264-mp4")

    def test_unified_node_preserves_segment_output_contract(self):
        self.assertIs(nodes.NODE_CLASS_MAPPINGS["H3LVUnified"], nodes.Unified)
        self.assertEqual(set(nodes.NODE_CLASS_MAPPINGS), {"H3LVUnified"})
        self.assertNotIn("H3LVAnalyze", nodes.NODE_DISPLAY_NAME_MAPPINGS)
        self.assertNotIn("H3LVLoadSegment", nodes.NODE_DISPLAY_NAME_MAPPINGS)
        self.assertEqual(nodes.Unified.RETURN_TYPES[:5], nodes.LoadSegment.RETURN_TYPES)
        self.assertEqual(nodes.Unified.RETURN_NAMES[:5], nodes.LoadSegment.RETURN_NAMES)
        self.assertEqual(nodes.Unified.RETURN_NAMES[5:],
                         ("reference_image_1", "reference_image_2"))
        self.assertEqual(nodes.LoadSegment.RETURN_NAMES, (
            "original_audio_padded", "vocals_padded", "segment_brief",
            "generation_frames", "filename_prefix"))
        self.assertNotIn("edit_frames", nodes.LoadSegment.RETURN_NAMES)
        self.assertNotIn("fps", nodes.LoadSegment.RETURN_NAMES)
        required = nodes.Unified.INPUT_TYPES()["required"]
        self.assertIn("audio", required)
        self.assertIn("performance_intensity", required)
        self.assertIn("camera_activity", required)
        self.assertNotIn("steady", required["camera_activity"][0])
        self.assertIn("widest_framing", required)
        self.assertEqual(required["widest_framing"][0][0], "medium close-up")
        self.assertEqual(required["asr_python"][1]["default"], "")
        self.assertEqual(required["asr_model"][1]["default"], "")
        self.assertEqual(required["asr_device"][0][0], "auto")
        self.assertIn("director_note", required)
        self.assertIn("director_mode", required)
        self.assertIn("reference_layout", required)
        self.assertEqual(required["vocal_assignment"][0], ["人物1主唱"])
        self.assertEqual(required["reference_layout"][0],
                         ["双图：图1人物，图2场景", "单图：人物+场景"])
        optional = nodes.Unified.INPUT_TYPES()["optional"]
        self.assertIn("reference_image_1", optional)
        self.assertIn("reference_image_2", optional)
        self.assertNotIn("reference_image_3", optional)
        self.assertIn("visual_brief", required)  # hidden compatibility slot for saved workflows
        self.assertIn("project_id", required)
        self.assertIn("segment_index", required)
        self.assertIn("H3LVUnified", controller.SEGMENT_NODE_TYPES)

    def test_reference_layout_controls_actual_h3_image_routing(self):
        image_1, image_2 = object(), object()
        self.assertEqual(
            nodes.route_reference_images("单图：人物+场景", image_1, image_2),
            (image_1, None))
        self.assertEqual(
            nodes.route_reference_images("双图：图1人物，图2场景", image_1, image_2),
            (image_1, image_2))
        with self.assertRaisesRegex(ValueError, "缺少图1"):
            nodes.route_reference_images("单图：人物+场景", None, image_2)
        with self.assertRaisesRegex(ValueError, "连接图2场景"):
            nodes.route_reference_images("双图：图1人物，图2场景", image_1, None)

    def test_snapshot_uses_current_bundled_ref2va_rule(self):
        prompt = {"204": {"class_type": "PromptExpand", "inputs": {
            "custom_rule": False, "custom_rule_content": "old embedded rule"}},
            "7": {"class_type": "Other", "inputs": {}}}
        updated = controller.apply_bundled_prompt_rule(prompt)
        expected = (ROOT/"ref2va_performance_rule.txt").read_text(encoding="utf-8")
        self.assertTrue(updated["204"]["inputs"]["custom_rule"])
        self.assertEqual(updated["204"]["inputs"]["custom_rule_content"], expected)
        self.assertEqual(updated["7"]["inputs"], {})
        self.assertIn("[reference generation + audio reference]", expected)
        self.assertIn("H3LV_SEGMENT_V1", expected)
        self.assertIn("出口运动状态", expected)
        self.assertIn("omit that category", expected)
        for forbidden in ("partially_copy", "fully_copy", "audio reuse", "final assembly", "FFmpeg"):
            self.assertNotIn(forbidden, expected)

    def test_legacy_project_reconstructs_missing_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript, reconstructed = core.read_project_transcript(directory, sample_plan())
        self.assertTrue(reconstructed)
        self.assertEqual(len(transcript["segments"]), 3)
        self.assertEqual(transcript["segments"][0]["text"], "未校对歌词")

    def test_missing_transcript_without_saved_text_has_friendly_error(self):
        plan = sample_plan()
        for row in plan["segments"]:
            row["text"] = ""
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "重新运行音频分析"):
                core.read_project_transcript(directory, plan)

    def test_existing_transcript_is_preferred(self):
        expected = {"segments": [{"start": 1, "end": 2, "text": "原始识别"}], "words": []}
        with tempfile.TemporaryDirectory() as directory:
            path = core.state_file(directory, "transcript.json")
            path.parent.mkdir()
            path.write_text(
                json.dumps(expected, ensure_ascii=False), encoding="utf-8")
            transcript, reconstructed = core.read_project_transcript(directory, sample_plan())
        self.assertFalse(reconstructed)
        self.assertEqual(transcript, expected)

    def test_contiguous_and_frame_grid(self):
        p = sample_plan()
        self.assertEqual(sum(s["edit_frames"] for s in p["segments"]), round(p["duration"]*24))
        for s in p["segments"]:
            self.assertEqual((s["generation_frames"]-5)%17, 0)
            self.assertGreaterEqual(s["generation_frames"]/24, s["duration"])

    def test_no_asr_lyrics_in_prompt(self):
        for s in sample_plan()["segments"]:
            self.assertNotIn("未校对歌词", s["prompt"])

    def test_speaking_camera_is_fixed(self):
        p = sample_plan()
        p["mode"] = "speaking"
        core.decorate(p)
        self.assertTrue(all(s["camera"]=="medium close-up; a steady locked-off camera" for s in p["segments"]))
        for row in p["segments"]:
            self.assertIn("模式：口播", row["prompt"])
            self.assertIn("段内运镜：固定机位", row["prompt"])
            self.assertIn("出口运动状态：静止", row["prompt"])
            self.assertIn("<Audio 1>=<Subject 1> 的说话参考", row["prompt"])
            self.assertNotIn("主唱", row["prompt"])
            self.assertNotIn("音乐表演", row["prompt"])
            self.assertNotIn("麦克风", row["prompt"])
            self.assertNotIn("构图范围：", row["prompt"])
            self.assertLess(len(row["prompt"]), 700)

    def test_edit_invalidates_approval_and_duration_prompt(self):
        p = sample_plan()
        p["approved"] = True
        old = core.fingerprint(p)
        updates = [{"end": s["end"], "prompt": s["prompt"]} for s in p["segments"]]
        updates[0]["end"] = 9
        core.edit_plan(p, updates)
        self.assertFalse(p["approved"])
        self.assertNotEqual(old, core.fingerprint(p))
        self.assertEqual(p["segments"][1]["start"], 9)
        self.assertRegex(p["segments"][0]["prompt"], r"生成时长：\d+\.\d{3} 秒")

    def test_prompt_edit_invalidates_only_changed_segment(self):
        p = sample_plan()
        for index, row in enumerate(p["segments"]):
            row["job"] = {"status": "completed", "video": f"old_{index}.mp4"}
        p["final_video"] = "old_final.mp4"
        updates = [{"end": s["end"], "prompt": s["prompt"]} for s in p["segments"]]
        updates[1]["prompt"] += "\nuser_camera_note: slower lateral move"
        core.edit_plan(p, updates)
        self.assertEqual(p["changed_segments"], [1])
        self.assertTrue(p["segments"][1]["needs_regeneration"])
        self.assertFalse(p["segments"][0].get("needs_regeneration", False))
        self.assertFalse(p["segments"][2].get("needs_regeneration", False))
        self.assertTrue(all(row.get("job", {}).get("status") == "completed" for row in p["segments"]))
        self.assertTrue(p["final_stale"])

    def test_boundary_edit_invalidates_only_two_adjacent_segments(self):
        p = sample_plan()
        for index, row in enumerate(p["segments"]):
            row["job"] = {"status": "completed", "video": f"old_{index}.mp4"}
        updates = [{"end": s["end"], "prompt": s["prompt"]} for s in p["segments"]]
        updates[0]["end"] = 9
        core.edit_plan(p, updates)
        self.assertEqual(p["changed_segments"], [0, 1])
        self.assertTrue(p["segments"][0]["needs_regeneration"])
        self.assertTrue(p["segments"][1]["needs_regeneration"])
        self.assertFalse(p["segments"][2].get("needs_regeneration", False))

    def test_first_generation_boundary_edit_is_not_labeled_regeneration(self):
        p = sample_plan()
        updates = [{"end": s["end"], "prompt": s["prompt"]} for s in p["segments"]]
        updates[1]["end"] = 19
        core.edit_plan(p, updates)
        self.assertEqual(p["changed_segments"], [1, 2])
        self.assertFalse(any(row.get("needs_regeneration", False) for row in p["segments"]))
        self.assertTrue(all("job" not in row for row in p["segments"]))

    def test_invalid_and_running_edits_rejected(self):
        p = sample_plan()
        updates = [{"end": 20, "prompt": ""}, {"end": 10, "prompt": ""}, {"end": 30, "prompt": ""}]
        with self.assertRaises(ValueError): core.edit_plan(p, updates)
        p = sample_plan(); p["run_status"] = "running"
        with self.assertRaises(ValueError): core.edit_plan(p, updates)

    def test_edited_prompt_remembers_unchanged_previous_camera(self):
        p = sample_plan()
        p.update(samples=4000, duration=40)
        p["segments"] = [{"start_sample": i*1000, "end_sample": (i+1)*1000,
                          "energy_db": energy} for i, energy in enumerate([-30, -5, 0, -30])]
        core.decorate(p)
        updates = [{"end": s["end"], "prompt": s["prompt"]} for s in p["segments"]]
        updates[2]["end"] = 31
        core.edit_plan(p, updates)
        self.assertIn("入口剪辑：", p["segments"][2]["prompt"])
        self.assertIn(core._zh_framing(p["segments"][2]["previous_end_framing"]),
                      p["segments"][2]["prompt"])

    def test_singing_has_visible_camera_motion_and_environment_binding(self):
        p = sample_plan()
        self.assertNotIn("dolly out", p["segments"][0]["camera"])
        for row in p["segments"]:
            self.assertIn("参考角色：双图；<Picture 1>=人物 <Subject 1>", row["prompt"])
            self.assertIn("<Picture 2>=环境 <Subject 2>", row["prompt"])
            self.assertIn("<Audio 1>", row["prompt"])
            self.assertIn("入口剪辑：", row["prompt"])
            self.assertIn("段内运镜：", row["prompt"])
            self.assertIn("出口运动状态：", row["prompt"])
            self.assertNotIn("构图范围：", row["prompt"])
            self.assertNotIn("有效时长", row["prompt"])
            self.assertNotIn("导演控制：", row["prompt"])
            self.assertNotIn("音频结构依据：", row["prompt"])
            self.assertLess(len(row["prompt"]), 700)
        self.assertEqual(p["segments"][0]["camera_start"], "medium close-up")
        self.assertNotIn("wide", p["segments"][-1]["camera_end"])

    def test_reference_layouts_create_stable_subject_and_picture_roles(self):
        single = core.reference_manifest("single_composite")
        self.assertEqual(single["performers"][0]["picture"], 1)
        self.assertEqual(single["environment"], {"id": "environment", "subject": 2, "picture": 1})
        separate = core.reference_manifest("双图：图1人物，图2场景")
        self.assertEqual(separate["picture_count"], 2)
        self.assertEqual(separate["environment"], {"id": "environment", "subject": 2, "picture": 2})
        with self.assertRaisesRegex(ValueError, "双人图片组合已停用"):
            core.reference_manifest("duo_scene")

    def test_segment_brief_follows_single_singer_scene_manifest(self):
        p = sample_plan()
        p["references"] = core.reference_manifest("solo_scene")
        core.decorate(p)
        prompt = p["segments"][0]["prompt"]
        self.assertIn("<Picture 1>=人物 <Subject 1>", prompt)
        self.assertIn("<Picture 2>=环境 <Subject 2>", prompt)
        self.assertIn("<Audio 1>=<Subject 1> 的唱歌参考", prompt)
        self.assertNotIn("表演者 2", prompt)
        self.assertNotIn("参考对象映射", prompt)
        self.assertNotIn("音频结构依据", prompt)
        self.assertIn("手持道具：以 <Picture 1> 中清晰可见项目为准", prompt)
        self.assertIn("穿戴配饰：以 <Picture 1> 中清晰可见项目为准", prompt)

    def test_single_picture_brief_maps_person_and_environment_to_picture_one(self):
        p = sample_plan()
        p["references"] = core.reference_manifest("single_composite")
        core.decorate(p)
        prompt = p["segments"][0]["prompt"]
        self.assertIn("参考角色：单图；<Picture 1>=人物 <Subject 1> 与同图可见环境", prompt)
        self.assertIn("环境不单独编号", prompt)
        self.assertNotIn("<Subject 2>", prompt)
        self.assertNotIn("<Picture 2>", prompt)

    def test_ai_camera_sequence_is_validated_and_expanded(self):
        p = sample_plan()
        p["director"]["mode"] = "ai"
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "truck_right", "performance": "restrained"},
            {"index": 1, "opening_framing": "medium close-up", "opening_angle": "front three-quarter right",
             "movement": "truck_right", "performance": "natural"},
            {"index": 2, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "truck_left", "performance": "energetic"},
        ]
        core.decorate(p)
        self.assertEqual(p["segments"][0]["camera_move_family"], "lateral")
        self.assertEqual(p["segments"][1]["camera_move_family"], "lateral")
        self.assertEqual(p["segments"][2]["camera_move_direction"], "left")

    def test_same_direction_lateral_segments_allow_motion_match(self):
        p = sample_plan()
        p["director"]["mode"] = "ai"
        p["ai_shot_plan"] = [
            {"index": index, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "truck_right", "performance": "natural"} for index in range(3)]
        core.decorate(p)
        self.assertEqual(p["segments"][1]["entry_cut_strategy"], "matched-action cut")
        self.assertIn("screen-direction camera motion remains compatible",
                      p["segments"][1]["entry_cut_reason"])
        self.assertEqual(p["segments"][2]["camera_move_direction"], "right")

    def test_ai_direct_side_crossing_is_repaired_through_front(self):
        p = sample_plan()
        p["director"]["mode"] = "ai"
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium close-up",
             "opening_angle": "front three-quarter left", "movement": "truck_right"},
            {"index": 1, "opening_framing": "medium close-up",
             "opening_angle": "front three-quarter right", "movement": "truck_left"},
            {"index": 2, "opening_framing": "medium close-up",
             "opening_angle": "front three-quarter left", "movement": "truck_right"},
        ]
        core.decorate(p)
        self.assertEqual(p["ai_shot_plan"][1]["opening_angle"], "front")
        self.assertNotEqual(
            p["segments"][1]["camera_start_angle"],
            p["segments"][0]["camera_start_angle"],
        )

    def test_ai_same_axis_size_only_cut_is_rejected(self):
        p = sample_plan()
        p["director"]["mode"] = "ai"
        p["director"]["widest_framing"] = "medium shot"
        p["director"]["rule_config"] = wide_rule_config()
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "truck_right", "performance": "natural"},
            {"index": 1, "opening_framing": "medium shot", "opening_angle": "front",
             "movement": "dolly_in", "performance": "natural"},
            {"index": 2, "opening_framing": "medium shot", "opening_angle": "front three-quarter right",
             "movement": "dolly_in", "performance": "natural"},
        ]
        with self.assertRaisesRegex(ValueError, "必须同时改变景别和前侧机位"):
            core.decorate(p)

    def test_ai_angle_only_cut_is_rejected_when_two_shot_sizes_are_allowed(self):
        p = sample_plan()
        p["director"]["mode"] = "ai"
        p["director"]["widest_framing"] = "medium shot"
        p["director"]["rule_config"] = wide_rule_config()
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "truck_right", "performance": "natural"},
            {"index": 1, "opening_framing": "medium close-up", "opening_angle": "front three-quarter right",
             "movement": "truck_left", "performance": "natural"},
            {"index": 2, "opening_framing": "medium shot", "opening_angle": "front",
             "movement": "dolly_in", "performance": "natural"},
        ]
        with self.assertRaisesRegex(ValueError, "必须同时改变景别和前侧机位"):
            core.decorate(p)

    def test_ai_dolly_out_is_rejected_after_real_crop_overshoot(self):
        p = sample_plan()
        p["director"]["mode"] = "ai"
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "dolly_out", "performance": "natural"},
            {"index": 1, "opening_framing": "medium close-up", "opening_angle": "front three-quarter right",
             "movement": "truck_right", "performance": "natural"},
            {"index": 2, "opening_framing": "medium shot", "opening_angle": "front",
             "movement": "dolly_in", "performance": "natural"},
        ]
        with self.assertRaisesRegex(ValueError, "不支持的景别、角度或运镜"):
            core.decorate(p)

    def test_legacy_ai_dolly_out_is_migrated_to_moving_shot(self):
        p = sample_plan()
        p["director"]["mode"] = "ai"
        p["shot_plan_version"] = 5
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "steady", "performance": "natural"},
            {"index": 1, "opening_framing": "medium shot", "opening_angle": "front three-quarter right",
             "movement": "dolly_out", "performance": "natural"},
            {"index": 2, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "steady", "performance": "natural"},
        ]
        core.decorate(p)
        self.assertEqual(p["shot_plan_version"], 10)
        self.assertEqual(p["ai_shot_plan"][1]["opening_framing"], "medium close-up")
        self.assertEqual(p["ai_shot_plan"][1]["movement"], "arc_right")
        self.assertEqual(p["segments"][1]["camera_move_family"], "arc")
        self.assertTrue(any("后拉镜头已改为稳定镜头" in value for value in p["warnings"]))
        self.assertTrue(any("固定机位已转换为轻运镜" in value for value in p["warnings"]))

    def test_legacy_medium_shot_lateral_is_migrated_to_dolly(self):
        p = sample_plan()
        p["director"]["mode"] = "ai"
        p["shot_plan_version"] = 6
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium shot", "opening_angle": "front",
             "movement": "steady", "performance": "natural"},
            {"index": 1, "opening_framing": "medium close-up", "opening_angle": "front three-quarter right",
             "movement": "truck_right", "performance": "natural"},
            {"index": 2, "opening_framing": "medium shot", "opening_angle": "front",
             "movement": "truck_left", "performance": "natural"},
        ]
        core.decorate(p)
        self.assertEqual(p["shot_plan_version"], 10)
        self.assertEqual(p["ai_shot_plan"][2]["opening_framing"], "medium close-up")
        self.assertEqual(p["ai_shot_plan"][2]["movement"], "micro_reframe")
        self.assertEqual(p["segments"][2]["camera_move_family"], "micro reframe")
        self.assertTrue(any("中景横移已改为稳定镜头" in value for value in p["warnings"]))

    def test_new_ai_medium_shot_lateral_is_rejected(self):
        p = sample_plan()
        p["director"]["mode"] = "ai"
        p["director"]["widest_framing"] = "medium shot"
        p["director"]["rule_config"] = wide_rule_config()
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium shot", "opening_angle": "front",
             "movement": "dolly_in", "performance": "natural"},
            {"index": 1, "opening_framing": "medium close-up", "opening_angle": "front three-quarter right",
             "movement": "truck_right", "performance": "natural"},
            {"index": 2, "opening_framing": "medium shot", "opening_angle": "front",
             "movement": "truck_left", "performance": "natural"},
        ]
        with self.assertRaisesRegex(ValueError, "横移只允许使用 medium close-up"):
            core.decorate(p)

    def test_ai_settings_keep_key_out_of_public_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"settings.json"
            public = ai_director.write_settings(path, {
                "base_url": "https://example.com/v1", "model": "director-model", "api_key": "secret"})
            self.assertTrue(public["api_key_configured"])
            self.assertNotIn("api_key", public)
            self.assertEqual(ai_director.read_settings(path)["api_key"], "secret")

    def test_director_rules_are_external_editable_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            first = director_rules.public_rules(directory)
            self.assertEqual(first["directory"], directory)
            self.assertIn("相邻片段不采用完全相同", first["ai_rule"])
            self.assertIn("间隔片段或重复音乐段落中复用", first["ai_rule"])
            self.assertEqual(json.loads(first["config_text"])["singing"]["allowed_framings"],
                             ["medium close-up"])
            updated = director_rules.write_rules(directory, {
                "ai_rule": first["ai_rule"] + "\n保持真实环境光线。",
                "config_text": first["config_text"],
            })
            self.assertIn("保持真实环境光线", updated["ai_rule"])
            self.assertNotEqual(updated["revision"], first["revision"])
            with self.assertRaisesRegex(ValueError, "JSON 格式无效"):
                director_rules.write_rules(directory, {"config_text": "{"})
            reset = director_rules.reset_rules(directory)
            self.assertEqual(reset["revision"], first["revision"])
            self.assertEqual(
                json.loads(reset["config_text"])["singing"]["movement_pattern"],
                ["micro_reframe", "arc_right", "micro_reframe", "truck_left", "arc_left"],
            )

    def test_faster_whisper_is_declared_without_a_second_runtime(self):
        self.assertEqual((ROOT/"requirements.txt").read_text(encoding="utf-8").strip(),
                         "faster-whisper==1.2.1")
        source = (ROOT/"nodes.py").read_text(encoding="utf-8")
        self.assertIn("sys.executable", source)
        self.assertIn('"--download-root"', source)

    def test_adjacent_high_energy_shots_do_not_repeat_push_in(self):
        rows = [{"energy_db": energy, "text": "vocal"} for energy in [-30, -5, -4, -3, -20]]
        states = core.camera_sequence("singing", rows, core.director_preferences(sample_plan()))
        for first, second in zip(states, states[1:]):
            self.assertFalse(first["camera_move_family"] == second["camera_move_family"] == "dolly in")

    def test_legacy_performance_override_remains_readable_but_note_is_not_emitted(self):
        p = sample_plan()
        p["director"].update(performance_intensity="energetic", camera_activity="steady",
                             widest_framing="medium close-up", note="Keep gestures compact.")
        core.decorate(p)
        self.assertEqual(p["director"]["camera_activity"], "moderate")
        self.assertTrue(all(row["camera_move_family"] != "steady" for row in p["segments"]))
        self.assertNotIn("Keep gestures compact.", p["segments"][0]["prompt"])
        self.assertIn("投入而受控的音乐表演", p["segments"][0]["prompt"])

    def test_auto_plan_can_cut_while_motion_remains_active(self):
        p = sample_plan()
        moving = [row for row in p["segments"] if row["camera_move_family"] != "steady"]
        self.assertTrue(moving)
        self.assertTrue(all("active at the cut" in row["exit_motion_state"] for row in moving))
        self.assertTrue(any("出口运动状态：" in row["prompt"] and "保持进行" in row["prompt"]
                            for row in moving))

    def test_equal_energy_sequence_keeps_safe_medium_close_up_scale(self):
        rows = [{"energy_db": -20, "text": "vocal"} for _ in range(4)]
        prefs = core.director_preferences(sample_plan())
        states = core.camera_sequence("singing", rows, prefs)
        self.assertEqual({state["camera_start"] for state in states}, {"medium close-up"})
        self.assertEqual({state["camera_end"] for state in states}, {"medium close-up"})

    def test_ref2va_rule_does_not_force_a_settle_before_each_cut(self):
        rule = (ROOT/"ref2va_performance_rule.txt").read_text(encoding="utf-8")
        self.assertNotIn("settle before the explicitly stated padding", rule)
        self.assertIn("still moving at the final frame", rule)

    def test_ref2va_rule_maps_the_versioned_brief_without_redirection(self):
        rule = (ROOT/"ref2va_performance_rule.txt").read_text(encoding="utf-8")
        self.assertIn("H3LV_SEGMENT_V1", rule)
        self.assertIn("段内运镜", rule)
        self.assertIn("出口运动状态", rule)
        self.assertIn("<Subject 1>", rule)
        self.assertIn("Do not create <Subject 2>", rule)
        self.assertIn("exactly one [Shot 1]", rule)
        self.assertIn("do not redesign the shot", rule)
        self.assertIn("omit that category", rule)
        self.assertIn('Use the neutral term "visible performer"', rule)
        self.assertIn("Never infer sex, gender, age, ethnicity, nationality", rule)

    def test_every_singing_boundary_has_an_explicit_non_jump_cut(self):
        p = sample_plan()
        self.assertTrue(all(row["camera_move_family"] != "steady" for row in p["segments"]))
        self.assertEqual([row["camera_move_family"] for row in p["segments"]],
                         ["micro reframe", "arc", "micro reframe"])
        for previous, current in zip(p["segments"], p["segments"][1:]):
            same_size = current["camera_start"] == previous["camera_end"]
            same_angle = current["camera_start_angle"] == previous["camera_end_angle"]
            self.assertFalse(same_angle)
            self.assertTrue(same_size)
            self.assertEqual(current["entry_cut_strategy"], "30-degree angle cut")
            self.assertIn("出口运动状态：", previous["prompt"])
            self.assertIn("承接上一段", current["prompt"])

    def test_default_singing_pattern_uses_lateral_as_an_occasional_accent(self):
        rows = [{"energy_db": -20+i, "text": "vocal"} for i in range(5)]
        states = core.camera_sequence("singing", rows, core.director_preferences(sample_plan()))
        self.assertEqual([state["camera_move_family"] for state in states],
                         ["micro reframe", "arc", "micro reframe", "lateral", "arc"])
        self.assertEqual(sum(state["camera_move_family"] == "lateral" for state in states), 1)

    def test_new_ai_motion_contract_rejects_any_steady_shot(self):
        p = sample_plan()
        p.update(samples=4000, duration=40)
        p["segments"] = [
            {"start_sample": i*1000, "end_sample": (i+1)*1000,
             "energy_db": -30+i*5, "text": "vocal"} for i in range(4)]
        p["director"].update(mode="ai", camera_activity="auto")
        p["director"]["widest_framing"] = "medium shot"
        p["director"]["rule_config"] = wide_rule_config(include_steady=True)
        p["ai_motion_contract"] = 1
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "steady"},
            {"index": 1, "opening_framing": "medium shot", "opening_angle": "front three-quarter right",
             "movement": "steady"},
            {"index": 2, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "truck_right"},
            {"index": 3, "opening_framing": "medium shot", "opening_angle": "front three-quarter left",
             "movement": "dolly_in"},
        ]
        with self.assertRaisesRegex(ValueError, "使用了固定机位"):
            core.decorate(p)

    def test_new_ai_dynamic_contract_requires_motion_in_every_segment(self):
        p = sample_plan()
        p["director"].update(mode="ai", camera_activity="dynamic")
        p["director"]["widest_framing"] = "medium shot"
        p["director"]["rule_config"] = wide_rule_config(include_steady=True)
        p["ai_motion_contract"] = 1
        p["ai_shot_plan"] = [
            {"index": 0, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "truck_right"},
            {"index": 1, "opening_framing": "medium shot", "opening_angle": "front three-quarter right",
             "movement": "steady"},
            {"index": 2, "opening_framing": "medium close-up", "opening_angle": "front",
             "movement": "steady"},
        ]
        with self.assertRaisesRegex(ValueError, "使用了固定机位"):
            core.decorate(p)

    def test_ai_director_is_singing_only(self):
        p = sample_plan()
        p["mode"] = "speaking"
        with self.assertRaisesRegex(ValueError, "固定机位口播使用本地连续性规则"):
            ai_director.plan_shots(p, [], {"base_url": "https://example.com/v1",
                "model": "director", "api_key": "secret"})

    def test_ai_director_request_is_plain_shot_planning_without_vague_vocal_state(self):
        p = sample_plan()
        p["references"] = {"picture_count": 1, "layout": "single"}
        response_items = [
            {"index": index, "opening_framing": "medium close-up",
             "opening_angle": "front", "movement": "steady"}
            for index in range(len(p["segments"]))
        ]
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": json.dumps({"segments": response_items})}}]
        }).encode("utf-8")
        with patch.object(ai_director, "_image_data_url", return_value="data:image/jpeg;base64,x"), \
             patch.object(ai_director.urllib.request, "urlopen") as urlopen:
            urlopen.return_value = response
            ai_director.plan_shots(p, [np.zeros((8, 8, 3), dtype=np.float32)], {
                "base_url": "https://example.com/v1", "model": "director", "api_key": "secret"})
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        prompt = json.dumps(payload["messages"], ensure_ascii=False)
        self.assertNotIn("H3", prompt)
        self.assertNotIn("vocal_state", prompt)
        self.assertNotIn("uncertain evidence", prompt)
        self.assertIn("recognized_phrase_hint", prompt)

    def test_paths_and_atomic_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): core.project_path(d, "../bad")
            with self.assertRaises(ValueError): core.inside(d, Path(d).parent/"secret")
            p = sample_plan(); core.write_plan(d, p)
            self.assertTrue(core.state_file(core.project_path(d, p["id"]), "segments.json").is_file())
            self.assertFalse((core.project_path(d, p["id"])/"segments.json").exists())
            self.assertEqual(core.read_plan(d, p["id"]), p)

    def test_storage_roots_and_segment_take_prefix_are_separated(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as d, patch.dict(sys.modules, {
                "folder_paths": types.SimpleNamespace(get_output_directory=lambda: d)}):
            output = Path(d)
            self.assertEqual(nodes.storage_root(), output/"H3LongVideo")
            self.assertEqual(nodes.data_root(), output/"H3LongVideo"/"projects")
            self.assertEqual(nodes.final_root(), output/"H3LongVideo"/"final_videos")
            plan = sample_plan(); plan["approved"] = True
            directory = core.project_path(nodes.data_root(), plan["id"])
            (directory/"audio").mkdir(parents=True)
            audio = np.zeros((plan["samples"], 1), dtype=np.float32)
            sf.write(core.audio_file(directory, "source.wav"), audio, plan["sample_rate"])
            sf.write(core.audio_file(directory, "vocals.wav"), audio, plan["sample_rate"])
            core.write_plan(nodes.data_root(), plan)
            fake_torch = types.SimpleNamespace(from_numpy=lambda value: MagicMock())
            with patch.dict(sys.modules, {"torch": fake_torch}):
                loaded = nodes.LoadSegment().load(plan["id"], 0)
            self.assertEqual(loaded[4], f"H3LongVideo/projects/{plan['id']}/takes/seg_0000")

    def test_silence_and_short_audio_coverage(self):
        for samples in [70, 1300, 3680]:
            rows = core.segmentation(np.zeros((samples, 2)), 100, {}, "singing", 15, 11)
            self.assertEqual(rows[0]["start_sample"], 0)
            self.assertEqual(rows[-1]["end_sample"], samples)
            self.assertEqual(sum(r["end_sample"]-r["start_sample"] for r in rows), samples)

    def test_analysis_metadata_and_exact_coverage(self):
        sr = 1000
        voice = np.zeros((24000, 1), dtype=np.float32)
        voice[1000:8200] = .45
        voice[9200:17000] = .4
        voice[18500:23000] = .35
        transcript = {"segments": [
            {"start": 1, "end": 8.2, "text": "第一句"},
            {"start": 9.2, "end": 17, "text": "第二句"},
            {"start": 18.5, "end": 23, "text": "第三句"}],
            "words": [{"start": 2, "end": 3, "word": "第一"},
                      {"start": 10, "end": 11, "word": "第二"}]}
        rows, analysis = core.segmentation(voice, sr, transcript, "singing", 15, 11,
                                            mix_audio=voice, return_analysis=True)
        self.assertEqual(sum(r["end_sample"]-r["start_sample"] for r in rows), len(voice))
        self.assertEqual(analysis["schema"], 2)
        self.assertTrue(analysis["waveform"]["original"])
        self.assertTrue(analysis["waveform"]["vocals"])
        self.assertEqual(len(analysis["phrases"]), 3)
        self.assertTrue(any(r["boundary_kind"] in {"phrase_gap", "quiet", "section"} for r in rows[:-1]))

    def test_word_interior_is_avoided_when_safe_cut_exists(self):
        sr = 1000
        voice = np.full((18000, 1), .3, dtype=np.float32)
        voice[9950:10050] = 0
        voice[10950:11050] = 0
        transcript = {"segments": [{"start": 0, "end": 18, "text": "长句"}],
            "words": [{"start": 9.5, "end": 10.5, "word": "保护字"}]}
        rows = core.segmentation(voice, sr, transcript, "singing", 15, 11)
        cuts = [row["end"] for row in core.decorate({"id": "b"*32, "sample_rate": sr,
            "samples": len(voice), "duration": len(voice)/sr, "mode": "singing",
            "max_seconds": 15, "target_seconds": 11, "visual_brief": "test",
            "segments": rows})["segments"][:-1]]
        self.assertTrue(cuts)
        self.assertTrue(all(not 9.5 < cut < 10.5 for cut in cuts))

    def test_manual_boundary_clears_automatic_confidence(self):
        plan = sample_plan()
        plan["segments"][0].update(boundary_kind="phrase_gap", boundary_confidence=.92,
                                   reason="分句附近的持续低能量区")
        updates = [{"end": s["end"], "prompt": s["prompt"]} for s in plan["segments"]]
        updates[0]["end"] = 9
        core.edit_plan(plan, updates)
        self.assertEqual(plan["segments"][0]["boundary_kind"], "manual")
        self.assertIsNone(plan["segments"][0]["boundary_confidence"])
        self.assertEqual(plan["segments"][0]["reason"], "手动调整切点")

    def test_unsaved_preview_bounds_follow_draft_times(self):
        plan = sample_plan()
        self.assertEqual(core.preview_bounds(plan, 1), (1000, 2000))
        self.assertEqual(core.preview_bounds(plan, 1, 9.25, 19.75), (925, 1975))
        self.assertEqual(core.preview_bounds(plan, 1, 9.25, 19.75, True), (1775, 2175))
        with self.assertRaises(ValueError):
            core.preview_bounds(plan, 1, 9, None)
        with self.assertRaises(ValueError):
            core.preview_bounds(plan, 1, 9, 25)

    def test_vhs_history_rejects_outside_project(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)/("a"*32); project.mkdir()
            outside = Path(d)/"outside.mp4"; outside.write_bytes(b"x")
            history = {"status": {"status_str": "success"}, "outputs": {"7": {"gifs": [
                {"type": "output", "filename": "outside.mp4", "subfolder": ""}]}}}
            with self.assertRaises(ValueError): controller.video_from_history(history, "7", project, d)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_real_ffmpeg_assembly_with_synthetic_video(self):
        import soundfile as sf
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/"H3LongVideo"/"projects"
            p = sample_plan()
            p["created"] = 1788138231
            p["sample_rate"] = 48000; p["samples"] = 30*48000
            # Real lyric cuts do not land on whole seconds.
            frame_cuts = (0, 323, 480, 720)
            for i, row in enumerate(p["segments"]):
                row.update(start_sample=frame_cuts[i]*2000, end_sample=frame_cuts[i+1]*2000)
            core.decorate(p)
            directory = core.project_path(root, p["id"]); directory.mkdir(parents=True)
            core.audio_file(directory, "source.wav").parent.mkdir()
            sf.write(core.audio_file(directory, "source.wav"), np.zeros((p["samples"], 2), dtype=np.float32), 48000)
            takes = directory/"takes"; takes.mkdir()
            for row in p["segments"]:
                path = takes/f"test_{row['index']}.mp4"
                controller.command([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x96:r=24", "-frames:v", str(row["generation_frames"]),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
                row["job"] = {"status": "completed", "video": str(path)}
            core.write_plan(root, p)
            final = controller.assemble(root, p["id"])
            normalized = sorted((directory/"cache").glob("*.mp4"))
            cached_mtimes = {path.name: path.stat().st_mtime_ns for path in normalized}
            # A browser may still be previewing the previous result on Windows.
            with Path(final).open("rb"):
                second_final = controller.assemble(root, p["id"])
            self.assertNotEqual(final, second_final)
            self.assertEqual(Path(final).parent, Path(d)/"H3LongVideo"/"final_videos")
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(p["created"]))
            self.assertEqual(Path(final).name, f"{stamp}_singing_aaaaaaaa.mp4")
            self.assertEqual(Path(second_final).name, f"{stamp}_singing_aaaaaaaa_v2.mp4")
            self.assertTrue(Path(final).is_file())
            self.assertTrue(Path(second_final).is_file())
            self.assertEqual(list((directory/"work").iterdir()), [])
            self.assertEqual(cached_mtimes, {path.name: path.stat().st_mtime_ns
                                             for path in (directory/"cache").glob("*.mp4")})
            info = controller.probe_video(final)
            self.assertEqual(int(info["nb_read_frames"]), 720)
            self.assertAlmostEqual(float(info["duration"]), 30, delta=1/24000)
            self.assertEqual(info["avg_frame_rate"], "24/1")
            audio = json.loads(controller.command([shutil.which("ffprobe"), "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=duration", "-of", "json", final]))
            self.assertAlmostEqual(float(audio["streams"][0]["duration"]), 30, delta=.05)


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_serial_queue_and_completion(self):
        with tempfile.TemporaryDirectory() as d:
            plan = sample_plan(); plan["approved"] = True
            plan["approved_fingerprint"] = core.fingerprint(plan)
            core.write_plan(d, plan)
            directory = core.project_path(d, plan["id"])
            snapshot = {"loader_id": "1", "video_id": "7", "client_id": "browser-123",
                        "prompt": {"1": {"class_type": "H3LVUnified", "inputs": {}}}}
            core.state_file(directory, "queue_snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
            indices, histories, client_ids, create_times, events = [], {}, [], [], []
            class Queue:
                def put(self, item):
                    index = item[2]["1"]["inputs"]["segment_index"]
                    indices.append(index)
                    client_ids.append(item[3].get("client_id"))
                    create_times.append(item[3].get("create_time"))
                    path = directory/f"{index}.mp4"; path.write_bytes(b"test")
                    histories[item[1]] = {"status": {"status_str": "success"}, "outputs": {"7": {"gifs": [
                        {"type": "output", "subfolder": plan["id"], "filename": path.name}]}}}
                def get_history(self, prompt_id): return {prompt_id: histories[prompt_id]}
                def get_current_queue(self): return [], []
            server = types.SimpleNamespace(number=0, prompt_queue=Queue(),
                                           send_sync=lambda name, data: events.append((name, data)))
            async def validate(*args): return True, None, ["7"], {}
            execution = types.SimpleNamespace(validate_prompt=validate)
            folders = types.SimpleNamespace(get_output_directory=lambda: d)
            with patch.dict(sys.modules, {"execution": execution, "folder_paths": folders}), patch.object(controller, "assemble", return_value=str(directory/"final.mp4")):
                await controller.execute_project(d, plan["id"], server)
            result = core.read_plan(d, plan["id"])
            self.assertEqual(indices, [0, 1, 2])
            self.assertEqual(client_ids, ["browser-123"] * 3)
            self.assertTrue(all(isinstance(value, int) and value > 0
                                for value in create_times))
            self.assertEqual([data["segment_index"] for name, data in events
                              if name == "h3lv-segment"], [0, 1, 2])
            self.assertEqual(result["run_status"], "completed")
            self.assertTrue(all(s["job"]["status"]=="completed" for s in result["segments"]))

    async def test_completed_segment_can_be_replaced_without_rerunning_others(self):
        with tempfile.TemporaryDirectory() as d:
            plan = sample_plan(); plan["approved"] = True
            plan["approved_fingerprint"] = core.fingerprint(plan)
            directory = core.project_path(d, plan["id"]); directory.mkdir()
            for index, row in enumerate(plan["segments"]):
                old = directory/f"old_{index}.mp4"; old.write_bytes(b"old")
                row["job"] = {"status": "completed", "video": str(old), "prompt_id": f"old-{index}"}
            core.request_regeneration(plan["segments"][1], "test replacement")
            core.write_plan(d, plan)
            core.state_file(directory, "queue_snapshot.json").write_text(json.dumps({
                "loader_id": "1", "video_id": "7",
                "prompt": {"1": {"class_type": "H3LVUnified", "inputs": {}}}
            }), encoding="utf-8")
            indices, histories = [], {}
            class Queue:
                def put(self, item):
                    index = item[2]["1"]["inputs"]["segment_index"]
                    indices.append(index)
                    path = directory/f"new_{index}.mp4"; path.write_bytes(b"new")
                    histories[item[1]] = {"status": {"status_str": "success"}, "outputs": {"7": {"gifs": [
                        {"type": "output", "subfolder": plan["id"], "filename": path.name}]}}}
                def get_history(self, prompt_id): return {prompt_id: histories[prompt_id]}
                def get_current_queue(self): return [], []
            async def validate(*args): return True, None, ["7"], {}
            server = types.SimpleNamespace(number=0, prompt_queue=Queue())
            with patch.dict(sys.modules, {"execution": types.SimpleNamespace(validate_prompt=validate),
                                           "folder_paths": types.SimpleNamespace(get_output_directory=lambda: d)}), \
                 patch.object(controller, "assemble", return_value=str(directory/"new_final.mp4")):
                await controller.execute_project(d, plan["id"], server)
            result = core.read_plan(d, plan["id"])
            self.assertEqual(indices, [1])
            self.assertEqual(Path(result["segments"][1]["job"]["video"]).name, "new_1.mp4")
            self.assertEqual(len(result["segments"][1]["takes"]), 1)
            self.assertEqual(result["segments"][0]["job"]["prompt_id"], "old-0")
            self.assertEqual(result["segments"][2]["job"]["prompt_id"], "old-2")

    async def test_single_segment_regeneration_does_not_continue_into_unfinished_rows(self):
        with tempfile.TemporaryDirectory() as d:
            plan = sample_plan(); plan["approved"] = True
            plan["approved_fingerprint"] = core.fingerprint(plan)
            directory = core.project_path(d, plan["id"]); directory.mkdir()
            for index in (0, 1):
                old = directory/f"old_{index}.mp4"; old.write_bytes(b"old")
                plan["segments"][index]["job"] = {
                    "status": "completed", "video": str(old), "prompt_id": f"old-{index}"}
            core.request_regeneration(plan["segments"][1], "single replacement")
            plan["run_only_segment"] = 1
            core.write_plan(d, plan)
            core.state_file(directory, "queue_snapshot.json").write_text(json.dumps({
                "loader_id": "1", "video_id": "7",
                "prompt": {"1": {"class_type": "H3LVUnified", "inputs": {}}}
            }), encoding="utf-8")
            indices, histories = [], {}
            class Queue:
                def put(self, item):
                    index = item[2]["1"]["inputs"]["segment_index"]
                    indices.append(index)
                    path = directory/f"new_{index}.mp4"; path.write_bytes(b"new")
                    histories[item[1]] = {"status": {"status_str": "success"}, "outputs": {"7": {"gifs": [
                        {"type": "output", "subfolder": plan["id"], "filename": path.name}]}}}
                def get_history(self, prompt_id): return {prompt_id: histories[prompt_id]}
                def get_current_queue(self): return [], []
            async def validate(*args): return True, None, ["7"], {}
            server = types.SimpleNamespace(number=0, prompt_queue=Queue())
            with patch.dict(sys.modules, {"execution": types.SimpleNamespace(validate_prompt=validate),
                                           "folder_paths": types.SimpleNamespace(get_output_directory=lambda: d)}):
                await controller.execute_project(d, plan["id"], server)
            result = core.read_plan(d, plan["id"])
            self.assertEqual(indices, [1])
            self.assertEqual(result["run_status"], "paused")
            self.assertNotIn("run_only_segment", result)
            self.assertNotIn("job", result["segments"][2])

    async def test_stop_request_finishes_current_segment_and_submits_no_more(self):
        with tempfile.TemporaryDirectory() as d:
            plan = sample_plan(); plan["approved"] = True
            plan["approved_fingerprint"] = core.fingerprint(plan)
            core.write_plan(d, plan)
            directory = core.project_path(d, plan["id"])
            core.state_file(directory, "queue_snapshot.json").write_text(json.dumps({
                "loader_id": "1", "video_id": "7",
                "prompt": {"1": {"class_type": "H3LVUnified", "inputs": {}}}
            }), encoding="utf-8")
            indices, histories = [], {}
            class Queue:
                def put(self, item):
                    index = item[2]["1"]["inputs"]["segment_index"]
                    indices.append(index)
                    path = directory/f"{index}.mp4"; path.write_bytes(b"video")
                    histories[item[1]] = {"status": {"status_str": "success"}, "outputs": {"7": {"gifs": [
                        {"type": "output", "subfolder": plan["id"], "filename": path.name}]}}}
                    latest = core.read_plan(d, plan["id"])
                    latest["stop_requested"] = True
                    core.write_plan(d, latest)
                def get_history(self, prompt_id): return {prompt_id: histories[prompt_id]}
                def get_current_queue(self): return [], []
            async def validate(*args): return True, None, ["7"], {}
            server = types.SimpleNamespace(number=0, prompt_queue=Queue())
            with patch.dict(sys.modules, {"execution": types.SimpleNamespace(validate_prompt=validate),
                                           "folder_paths": types.SimpleNamespace(get_output_directory=lambda: d)}):
                await controller.execute_project(d, plan["id"], server)
            result = core.read_plan(d, plan["id"])
            self.assertEqual(indices, [0])
            self.assertEqual(result["run_status"], "stopped")
            self.assertEqual(result["segments"][0]["job"]["status"], "completed")

    async def test_failed_replacement_restores_previous_completed_take(self):
        with tempfile.TemporaryDirectory() as d:
            plan = sample_plan(); plan["approved"] = True
            plan["approved_fingerprint"] = core.fingerprint(plan)
            directory = core.project_path(d, plan["id"]); directory.mkdir()
            old = directory/"old_1.mp4"; old.write_bytes(b"old")
            plan["segments"][0]["job"] = {"status": "completed", "video": str(directory/"old_0.mp4")}
            plan["segments"][1]["job"] = {"status": "completed", "video": str(old), "prompt_id": "old"}
            plan["segments"][2]["job"] = {"status": "completed", "video": str(directory/"old_2.mp4")}
            core.request_regeneration(plan["segments"][1], "test failure")
            core.write_plan(d, plan)
            core.state_file(directory, "queue_snapshot.json").write_text(json.dumps({
                "loader_id": "1", "video_id": "7",
                "prompt": {"1": {"class_type": "H3LVUnified", "inputs": {}}}
            }), encoding="utf-8")
            histories = {}
            class Queue:
                def put(self, item):
                    histories[item[1]] = {"status": {"status_str": "error"}, "outputs": {}}
                def get_history(self, prompt_id): return {prompt_id: histories[prompt_id]}
                def get_current_queue(self): return [], []
            async def validate(*args): return True, None, ["7"], {}
            server = types.SimpleNamespace(number=0, prompt_queue=Queue())
            with patch.dict(sys.modules, {"execution": types.SimpleNamespace(validate_prompt=validate),
                                           "folder_paths": types.SimpleNamespace(get_output_directory=lambda: d)}):
                await controller.execute_project(d, plan["id"], server)
            result = core.read_plan(d, plan["id"])
            row = result["segments"][1]
            self.assertEqual(result["run_status"], "failed")
            self.assertEqual(row["job"]["prompt_id"], "old")
            self.assertEqual(row["job"]["status"], "completed")
            self.assertTrue(row["needs_regeneration"])
            self.assertEqual(len(row["failed_attempts"]), 1)

    async def test_unknown_job_is_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as d:
            p = sample_plan(); p["approved"] = True; p["approved_fingerprint"] = core.fingerprint(p)
            p["segments"][0]["job"] = {"status": "queued", "prompt_id": "unknown"}
            core.write_plan(d, p)
            core.state_file(core.project_path(d, p["id"]), "queue_snapshot.json").write_text(json.dumps({"loader_id": "1", "video_id": "7", "prompt": {}}))
            calls = []
            q = types.SimpleNamespace(get_history=lambda **kw: {}, get_current_queue=lambda: ([], []), put=lambda x: calls.append(x))
            with patch.dict(sys.modules, {"execution": types.SimpleNamespace(), "folder_paths": types.SimpleNamespace()}):
                await controller.execute_project(d, p["id"], types.SimpleNamespace(prompt_queue=q))
            self.assertEqual(calls, [])
            self.assertEqual(core.read_plan(d, p["id"])["run_status"], "failed")


if __name__ == "__main__": unittest.main()
