import asyncio
import copy
import io
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
director_rules = importlib.import_module("h3lv_test.director_rules")
routes = importlib.import_module("h3lv_test.routes")


def wide_rule_config():
    return director_rules.validate_config({
        "schema": 2,
        "singing": {
            "allowed_framings": ["medium close-up", "medium shot"],
            "allowed_angles": ["front", "front three-quarter right", "front three-quarter left"],
            "energy_movements": {
                "low": ["micro_reframe", "arc_left", "arc_right"],
                "medium": ["arc_left", "arc_right", "truck_left", "truck_right"],
                "high": ["dolly_in", "dolly_out", "arc_left", "arc_right"],
            },
            "every_segment_moves": True,
            "no_adjacent_same_family": True,
            "alternate_lateral_direction": True,
            "avoid_direct_axis_cross": True,
        },
        "speaking": {"framing": "medium close-up", "angle": "front", "movement": "steady",
                     "keep_composition_across_segments": True},
    })


def sample_plan():
    return core.decorate({"id": "a"*32, "sample_rate": 100, "samples": 3000, "duration": 30,
        "mode": "singing", "max_seconds": 15, "target_seconds": 11,
        "director": {"performance_intensity": "auto", "note": "",
                      "rule_config": director_rules.default_config(),
                      "schedule_seed": "sample-audio", "rule_revision": "test-rules"},
        "approved": False, "revision": 1, "run_status": "draft", "created": 0,
        "segments": [{"start_sample": i*1000, "end_sample": (i+1)*1000, "energy_db": -30+i*10,
                      "text": "未校对歌词"} for i in range(3)]})


class CoreTests(unittest.TestCase):
    def test_visual_type_preview_rewrites_brief_without_mutating_plan(self):
        plan = sample_plan()
        original = plan["segments"][0]["prompt"]
        expected = {
            "performance": "画面任务：人物演唱",
            "atmosphere": "画面任务：人物氛围表演",
            "environment": "画面任务：空镜环境",
        }
        for visual_type, first_line in expected.items():
            with self.subTest(visual_type=visual_type):
                preview = core.preview_segment_brief(plan, 0, visual_type)
                self.assertTrue(preview.startswith(first_line))
        self.assertEqual(plan["segments"][0]["prompt"], original)
        with self.assertRaisesRegex(ValueError, "画面类型无效"):
            core.preview_segment_brief(plan, 0, "invalid")

    def test_dynamic_brief_is_preserved_when_visual_type_is_saved(self):
        plan = sample_plan()
        plan.update(materials_version=1, default_refs=[], default_material_note="")
        preview = core.preview_segment_brief(plan, 0, "environment")
        updates = [dict(row) for row in plan["segments"]]
        updates[0].update(prompt=preview, visual_type="environment",
                          reference_source="default", brief_matches_visual_type=True)
        core.edit_plan(plan, updates)
        self.assertEqual(plan["segments"][0]["visual_type"], "environment")
        self.assertEqual(plan["segments"][0]["prompt"], preview)

    def test_new_analysis_enables_material_editor_without_canvas_connections(self):
        import torch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = {"waveform": torch.zeros((1, 1, 1000)), "sample_rate": 100}
            rows = [{"start_sample": 0, "end_sample": 1000, "energy_db": -30,
                     "text": ""}]
            analysis = {"sections": [], "rhythm": {}}
            rules = {"config": director_rules.default_config(), "revision": "test-rules"}
            with patch.object(nodes, "data_root", return_value=root), \
                 patch.object(nodes, "rules_path", return_value=root/"rules"), \
                 patch.object(nodes.director_rules, "read_rules", return_value=rules), \
                 patch.object(nodes, "run_asr", return_value={"segments": [], "words": []}), \
                 patch.object(nodes, "segmentation", return_value=(rows, analysis)):
                result = nodes.Analyze().analyze(
                    audio, "speaking", 15, 11, "", "", vocals=audio)
            plan = core.read_plan(root, result["result"][0])
        self.assertEqual(plan["materials_version"], 1)
        self.assertEqual(plan["default_refs"], [])
        self.assertEqual(plan["default_material_note"], "")
        self.assertEqual(plan["segments"][0]["reference_source"], "default")

    def test_reveal_file_opens_windows_explorer_on_exact_output(self):
        with tempfile.TemporaryDirectory() as directory:
            final = Path(directory)/"final video.mp4"
            final.write_bytes(b"video")
            command = ["explorer.exe", "/separate,", "/select,", str(final.resolve())]
            self.assertEqual(controller.reveal_command(
                final.resolve(), os_name="nt", platform="win32"), command)
            with patch.object(controller, "reveal_command", return_value=command), \
                 patch.object(controller, "explorer_windows", side_effect=[{10}, {10, 20}]), \
                 patch.object(controller, "activate_explorer_window") as activate, \
                 patch.object(controller.subprocess, "Popen") as launch:
                self.assertEqual(controller.reveal_file(final), str(final.resolve()))
            launch.assert_called_once_with(
                command, stdout=controller.subprocess.DEVNULL,
                stderr=controller.subprocess.DEVNULL)
            activate.assert_called_once_with(20)

    def test_review_uses_compact_editor_and_versioned_previews(self):
        script = (ROOT/"web"/"h3lv.js").read_text(encoding="utf-8")
        styles = (ROOT/"web"/"h3lv.css").read_text(encoding="utf-8")
        routes_source = (ROOT/"routes.py").read_text(encoding="utf-8")
        self.assertIn('actionButton(promptActions, "编辑本段导演简报"', script)
        self.assertIn('"手写提示词（segment_prompt 直连，可选）"', script)
        self.assertIn('final_prompt: row.finalPrompt.value', script)
        self.assertIn("插件不扩写、不校验、不回退", script)
        self.assertNotIn('element("label", "结束时间（秒）", metrics)', script)
        self.assertIn("width: min(1440px, 100%)", styles)
        self.assertIn(
            "grid-template-columns: minmax(260px, 1.2fr) minmax(180px, 1fr) 96px",
            styles,
        )
        self.assertIn(".h3lv-chip { justify-self: end;", styles)
        self.assertIn("video.src = outputPreviewUrl(plan.final_preview);", script)
        self.assertIn("row.video_preview?.filename", script)
        self.assertIn('request(endpoint("/reveal-final"), {})', script)
        self.assertIn('@routes.post("/h3lv/project/{project_id}/reveal-final")', routes_source)
        self.assertNotIn("confidenceLabel(", script)
        self.assertNotIn("cutLabel(", script)
        self.assertNotIn('`剪辑：${', script)
        self.assertIn("async function analyzeOnly(owner)", script)
        self.assertIn("async function startApprovedSequence(owner, plan)", script)
        self.assertIn("const startingProjects = new Set()", script)
        self.assertIn('toast("已开始顺序生成"', script)
        self.assertIn('toast("顺序生成正在进行"', script)
        self.assertNotIn("await openReview(owner);", script)
        self.assertIn('title: `第 ${failedIndex + 1} 段上次没有生成完成`', script)
        self.assertIn('confirmText: `重跑第 ${failedIndex + 1} 段并继续`', script)
        self.assertIn("failureReason", script)
        self.assertNotIn('title: `第 ${failedIndex + 1} 段未完成`', script)
        self.assertNotIn("段未完成`", script)
        self.assertEqual(script.count("段上次没有生成完成`"), 2)
        self.assertIn("if (plan.approved)", script)
        self.assertIn("const requestedTargets = Array.isArray(options)", script)
        self.assertIn("options?.partialExecutionTargets", script)
        self.assertIn("const selectedItems = app.canvas?.selectedItems", script)
        self.assertIn("const nodeSelected = Boolean", script)
        self.assertIn("partialTargets.includes(String(node.id)) || nodeSelected", script)
        self.assertIn("partialTargets.includes(String(node.id))", script)
        self.assertIn('actionButton(controls, "重新分析分段"', script)
        self.assertIn("async function reanalyzeProject(owner", script)
        self.assertIn('secondaryText: "重新分析分段"', script)
        self.assertEqual(script.count('secondaryText: "重新分析分段"'), 2)
        self.assertIn('this.addWidget("button", "重新分析并分段"', script)
        self.assertIn('title: "无法重新分析分段"', script)
        self.assertNotIn('请在提示词小助手的“用户提示词”中填写素材说明', script)
        self.assertIn("async function openDirectorRules(owner)", script)
        self.assertIn('const selectedMode = nodeMode === "speaking"', script)
        self.assertIn('fullConfig[selectedMode] = JSON.parse(config.value)', script)
        self.assertIn('{mode: selectedMode}', script)
        self.assertNotIn("formatters.length", script)
        self.assertNotIn("segment_brief 输出连接到提示词小助手", script)
        self.assertIn('api.addEventListener("h3lv-model-download"', script)
        self.assertIn("正在下载人声分离模型", script)
        self.assertIn("oldActivities.has(values[6])", script)
        self.assertIn("values.splice(6, 2)", script)
        self.assertIn('"director_mode", "project_id", "segment_index"', script)

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
        self.assertEqual(set(nodes.NODE_CLASS_MAPPINGS), {"H3LVUnified", "H3LVPromptExpand"})
        self.assertNotIn("H3LVAnalyze", nodes.NODE_DISPLAY_NAME_MAPPINGS)
        self.assertNotIn("H3LVLoadSegment", nodes.NODE_DISPLAY_NAME_MAPPINGS)
        self.assertEqual(nodes.Unified.RETURN_TYPES, nodes.LoadSegment.RETURN_TYPES)
        self.assertEqual(nodes.Unified.RETURN_NAMES, nodes.LoadSegment.RETURN_NAMES)
        self.assertEqual(len(nodes.Unified.RETURN_NAMES), 12)
        self.assertEqual(nodes.LoadSegment.RETURN_NAMES[:5], (
            "original_audio_padded", "vocals_padded", "generation_frames",
            "filename_prefix", "segment_material"))
        self.assertNotIn("segment_brief", nodes.LoadSegment.RETURN_NAMES)
        self.assertEqual(nodes.LoadSegment.RETURN_NAMES[5:11], tuple(f"image_{i}" for i in range(1, 7)))
        self.assertEqual(nodes.LoadSegment.RETURN_NAMES[-1], "segment_prompt")
        self.assertNotIn("edit_frames", nodes.LoadSegment.RETURN_NAMES)
        self.assertNotIn("fps", nodes.LoadSegment.RETURN_NAMES)
        required = nodes.Unified.INPUT_TYPES()["required"]
        self.assertIn("audio", required)
        self.assertNotIn("camera_activity", required)
        self.assertNotIn("widest_framing", required)
        self.assertEqual(required["director_mode"][0], "STRING")
        self.assertEqual(required["asr_device"][0], ["auto", "cuda", "cpu"])
        optional = nodes.Unified.INPUT_TYPES()["optional"]
        self.assertEqual(set(optional), {"vocals"})
        for removed in ("visual_brief", "performance_intensity", "director_note",
                        "reference_layout", "vocal_assignment", "reference_roles_json"):
            self.assertNotIn(removed, required)
        self.assertIn("project_id", required)
        self.assertIn("segment_index", required)
        self.assertIn("H3LVUnified", controller.SEGMENT_NODE_TYPES)

    def test_frozen_snapshot_drops_removed_camera_controls(self):
        snapshot = {"loader_id": "1", "video_id": "2", "prompt": {
            "1": {"class_type": "H3LVUnified", "inputs": {
                "camera_activity": "dynamic", "widest_framing": "full shot",
                "target_seconds": 8}},
            "2": {"class_type": "Sampler", "inputs": {"steps": 12}},
        }}
        controller.normalize_output_contract(snapshot)
        inputs = snapshot["prompt"]["1"]["inputs"]
        self.assertNotIn("camera_activity", inputs)
        self.assertNotIn("widest_framing", inputs)
        self.assertEqual(inputs["target_seconds"], 8)
        self.assertEqual(snapshot["prompt"]["2"]["inputs"]["steps"], 12)
        self.assertEqual(snapshot["node_control_contract_version"], 1)

    def test_frozen_snapshot_shifts_outputs_after_segment_brief_removal(self):
        snapshot = {"loader_id": "1", "video_id": "9", "output_contract_version": 2,
                    "prompt": {
            "1": {"class_type": "H3LVUnified", "inputs": {}},
            "2": {"class_type": "H3LVPromptExpand", "inputs": {"material": ["1", 5]}},
            "3": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
                "generation_frames": ["1", 3], "ref_images.ref_image_0": ["1", 6]}},
            "4": {"class_type": "PromptExpand", "inputs": {"source_text": ["1", 2]}},
            "9": {"class_type": "VHS_VideoCombine", "inputs": {
                "filename_prefix": ["1", 4]}},
        }}
        controller.normalize_output_contract(snapshot)
        self.assertEqual(snapshot["prompt"]["2"]["inputs"]["material"], ["1", 4])
        self.assertEqual(snapshot["prompt"]["3"]["inputs"]["generation_frames"], ["1", 2])
        self.assertEqual(snapshot["prompt"]["3"]["inputs"]["ref_images.ref_image_0"], ["1", 5])
        self.assertNotIn("source_text", snapshot["prompt"]["4"]["inputs"])
        self.assertEqual(snapshot["prompt"]["9"]["inputs"]["filename_prefix"], ["1", 3])
        self.assertEqual(snapshot["output_contract_version"], 3)

    def test_unified_node_has_no_reference_image_or_prompt_assembly_surface(self):
        inputs = nodes.Unified.INPUT_TYPES()
        names = set(inputs["required"]) | set(inputs.get("optional", {}))
        self.assertFalse(any(name.startswith("reference_image_") for name in names))
        self.assertFalse(any(name.startswith("reference_image_") for name in nodes.Unified.RETURN_NAMES))
        self.assertNotIn("h3_prompt", nodes.Unified.RETURN_NAMES)
        for removed in ("collect_reference_images", "save_reference_snapshots",
                        "load_reference_snapshots"):
            self.assertFalse(hasattr(nodes, removed))

    def test_asr_uses_current_comfyui_python_and_managed_model_directory(self):
        source = (ROOT/"nodes.py").read_text(encoding="utf-8")
        worker_source = (ROOT/"worker.py").read_text(encoding="utf-8")
        self.assertIn("sys.executable", source)
        self.assertIn('"--download-root"', source)
        self.assertIn('parser.add_argument("--download-root", required=True)', worker_source)
        self.assertIn("retrying on CPU", worker_source)
        self.assertNotIn("resolve_asr_settings", source)

    def test_hdemucs_download_uses_managed_model_path_and_verified_atomic_file(self):
        payload = b"verified model payload"

        class Response(io.BytesIO):
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/"models"/"H3LongVideo"/"hdemucs_high_trained.pt"
            legacy = Path(directory)/"legacy-missing.pt"
            comfy = types.ModuleType("comfy")
            comfy.__path__ = []
            management = types.ModuleType("comfy.model_management")
            management.throw_exception_if_processing_interrupted = lambda: None
            utils = types.ModuleType("comfy.utils")
            utils.ProgressBar = lambda _total: types.SimpleNamespace(
                update_absolute=lambda _value: None)
            comfy.model_management = management
            comfy.utils = utils
            with patch.object(nodes, "HDEMUCS_MODEL_SIZE", len(payload)), \
                 patch.object(nodes, "HDEMUCS_MODEL_SHA256", nodes.hashlib.sha256(payload).hexdigest()), \
                 patch.object(nodes, "hdemucs_model_path", return_value=target), \
                 patch.object(nodes, "legacy_hdemucs_model_path", return_value=legacy), \
                 patch.object(nodes, "urlopen", return_value=Response(payload)) as open_url, \
                 patch.object(nodes, "_download_notice"), \
                 patch.dict(sys.modules, {"comfy": comfy,
                                          "comfy.model_management": management,
                                          "comfy.utils": utils}):
                self.assertEqual(nodes.ensure_hdemucs_model(), target)
                self.assertEqual(target.read_bytes(), payload)
                self.assertFalse(target.with_name(target.name + ".part").exists())
                self.assertEqual(nodes.ensure_hdemucs_model(), target)
            open_url.assert_called_once()

    def test_hdemucs_reuses_verified_legacy_cache_without_network(self):
        payload = b"legacy verified model"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/"models"/"H3LongVideo"/"hdemucs_high_trained.pt"
            legacy = Path(directory)/"torch-cache"/"hdemucs_high_trained.pt"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(payload)
            with patch.object(nodes, "HDEMUCS_MODEL_SIZE", len(payload)), \
                 patch.object(nodes, "HDEMUCS_MODEL_SHA256", nodes.hashlib.sha256(payload).hexdigest()), \
                 patch.object(nodes, "hdemucs_model_path", return_value=target), \
                 patch.object(nodes, "legacy_hdemucs_model_path", return_value=legacy), \
                 patch.object(nodes, "urlopen") as open_url, \
                 patch.object(nodes, "_download_notice"):
                self.assertEqual(nodes.ensure_hdemucs_model(), target)
            self.assertEqual(target.read_bytes(), payload)
            open_url.assert_not_called()

    def test_hdemucs_download_resumes_a_partial_file(self):
        payload = b"0123456789abcdef"

        class Response(io.BytesIO):
            status = 206
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/"hdemucs_high_trained.pt"
            partial = target.with_name(target.name + ".part")
            partial.write_bytes(payload[:6])
            comfy = types.ModuleType("comfy")
            comfy.__path__ = []
            management = types.ModuleType("comfy.model_management")
            management.throw_exception_if_processing_interrupted = lambda: None
            utils = types.ModuleType("comfy.utils")
            utils.ProgressBar = lambda _total: types.SimpleNamespace(
                update_absolute=lambda _value: None)
            comfy.model_management = management
            comfy.utils = utils
            with patch.object(nodes, "HDEMUCS_MODEL_SIZE", len(payload)), \
                 patch.object(nodes, "HDEMUCS_MODEL_SHA256", nodes.hashlib.sha256(payload).hexdigest()), \
                 patch.object(nodes, "_download_notice"), \
                 patch.object(nodes, "urlopen", return_value=Response(payload[6:])) as open_url, \
                 patch.dict(sys.modules, {"comfy": comfy,
                                          "comfy.model_management": management,
                                          "comfy.utils": utils}):
                nodes._download_hdemucs(target, partial)
            request = open_url.call_args.args[0]
            self.assertEqual(request.get_header("Range"), "bytes=6-")
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(partial.exists())

    def test_director_rules_are_external_editable_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            first = director_rules.public_rules(directory)
            self.assertEqual(json.loads(first["config_text"])["schema"], 2)
            updated = director_rules.write_rules(directory, {"config_text": first["config_text"]})
            self.assertEqual(updated["revision"], first["revision"])
            with self.assertRaisesRegex(ValueError, "JSON 格式无效"):
                director_rules.write_rules(directory, {"config_text": "{"})

    def test_reset_only_changes_the_selected_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            config = json.loads(director_rules.public_rules(directory)["config_text"])
            config["singing"]["allowed_framings"] = ["medium shot"]
            config["speaking"]["framing"] = "medium shot"
            director_rules.write_rules(directory, {"config_text": json.dumps(config)})
            reset = json.loads(director_rules.reset_rules(
                directory, "speaking")["config_text"])
            self.assertEqual(reset["singing"]["allowed_framings"], ["medium shot"])
            self.assertEqual(reset["speaking"]["framing"], "medium close-up")
            with self.assertRaisesRegex(ValueError, "singing 或 speaking"):
                director_rules.reset_rules(directory, "invalid")

    def test_schema_one_rules_are_normalized_without_ai_text(self):
        legacy = {"schema": 1, "singing": {
            "allowed_framings": ["medium close-up"],
            "allowed_angles": ["front", "front three-quarter right", "front three-quarter left"],
            "allowed_movements": ["micro_reframe", "arc_left", "arc_right", "truck_left"],
            "movement_pattern": ["micro_reframe", "arc_right", "truck_left"],
            "every_segment_moves": True, "constant_subject_scale": True},
            "speaking": {"framing": "medium close-up", "angle": "front", "movement": "steady",
                         "keep_composition_across_segments": True}}
        normalized = director_rules.validate_config(legacy)
        self.assertEqual(normalized["schema"], 2)
        self.assertIn("energy_movements", normalized["singing"])
        self.assertNotIn("movement_pattern", normalized["singing"])

    def test_optional_ref2va_template_contract(self):
        expected = (ROOT/"ref2va_performance_rule.txt").read_text(encoding="utf-8")
        self.assertIn("模式 (口播/speaking or 唱歌/singing)", expected)
        self.assertIn("镜头方案 and 表演节奏", expected)
        self.assertIn("user-written material description", expected)
        self.assertIn("legacy seven-field format", expected)
        self.assertIn("Omit undeclared details", expected)
        for forbidden in ("final assembly", "FFmpeg"):
            self.assertNotIn(forbidden, expected)

    def test_generation_preserves_prompt_assistant_rules_in_both_modes(self):
        for mode in ("singing", "speaking"):
            for custom in (False, True):
                with self.subTest(mode=mode, custom=custom), tempfile.TemporaryDirectory() as root:
                    plan = sample_plan()
                    plan["mode"] = mode
                    plan["approved"] = True
                    plan["approved_fingerprint"] = core.fingerprint(plan)
                    core.write_plan(root, plan)
                    prompt = {
                        "231": {"class_type": "H3LVUnified", "inputs": {}},
                        "122": {"class_type": "VHS_VideoCombine", "inputs": {}},
                        "204": {"class_type": "PromptExpand", "inputs": {
                            "rule": "User preset", "custom_rule": custom,
                            "custom_rule_content": "User rule: no subtitles",
                            "user_prompt": "User material", "llm_service": "User model",
                            "source_text": ["231", 2]}},
                    }
                    before = copy.deepcopy(prompt)
                    def capture_task(coroutine):
                        coroutine.close()
                        return MagicMock()
                    try:
                        with patch.object(controller.asyncio, "create_task", side_effect=capture_task):
                            controller.start(root, plan["id"], {
                                "loader_id": "231", "video_id": "122", "prompt": prompt}, MagicMock())
                        snapshot = json.loads(core.state_file(core.project_path(root, plan["id"]),
                            "queue_snapshot.json").read_text(encoding="utf-8"))
                        self.assertEqual(snapshot["prompt"]["204"], before["204"])
                        self.assertEqual(prompt, before)
                        self.assertEqual(snapshot["prompt_rule_source"], "workflow")
                    finally:
                        controller.TASKS.pop(plan["id"], None)

    def test_legacy_resume_restores_only_rule_settings_once(self):
        for custom in (False, True):
            with self.subTest(custom=custom), tempfile.TemporaryDirectory() as root:
                plan = sample_plan()
                plan["approved"] = True
                plan["approved_fingerprint"] = core.fingerprint(plan)
                plan["segments"][0]["job"] = {"status": "completed", "prompt_id": "done"}
                core.write_plan(root, plan)
                old_prompt = {
                    "204": {"class_type": "PromptExpand", "inputs": {
                        "rule": "old", "custom_rule": True, "custom_rule_content": "forced bundled rule",
                        "user_prompt": "saved material", "llm_service": "saved model",
                        "source_text": ["231", 2]}},
                    "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"seed": 42}},
                }
                snapshot_path = core.state_file(core.project_path(root, plan["id"]), "queue_snapshot.json")
                snapshot_path.write_text(json.dumps({"loader_id": "231", "video_id": "122",
                    "prompt": old_prompt}), encoding="utf-8")
                current = copy.deepcopy(old_prompt)
                rules = {"rule": "chosen preset", "custom_rule": custom,
                         "custom_rule_content": "chosen custom rule"}
                current["204"]["inputs"].update(rules, user_prompt="changed material", llm_service="changed model")
                current["136"]["inputs"]["seed"] = 99
                expected = copy.deepcopy(old_prompt)
                expected["204"]["inputs"].update(rules)
                def capture_task(coroutine):
                    coroutine.close()
                    return MagicMock()
                try:
                    for attempt in range(2):
                        with patch.object(controller.asyncio, "create_task", side_effect=capture_task):
                            controller.start(root, plan["id"], {"prompt": current}, MagicMock())
                        saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
                        self.assertEqual(saved["prompt"], expected)
                        self.assertEqual(saved["prompt_rule_source"], "workflow")
                        self.assertEqual(core.read_plan(root, plan["id"])["segments"][0]["job"]["prompt_id"], "done")
                        controller.TASKS.pop(plan["id"], None)
                        current["204"]["inputs"]["custom_rule_content"] = "later unrelated edit"
                finally:
                    controller.TASKS.pop(plan["id"], None)

    def test_legacy_resume_with_changed_rule_wiring_preserves_saved_state(self):
        for current in ({}, {"204": {"class_type": "Other", "inputs": {}}},
                        {"204": {"class_type": "PromptExpand", "inputs": {
                            "custom_rule_content": ["999", 0]}}}):
            with self.subTest(current=current), tempfile.TemporaryDirectory() as root:
                plan = sample_plan()
                plan["approved"] = True
                plan["approved_fingerprint"] = core.fingerprint(plan)
                plan["segments"][0]["job"] = {"status": "completed"}
                core.write_plan(root, plan)
                path = core.state_file(core.project_path(root, plan["id"]), "queue_snapshot.json")
                original = json.dumps({"prompt": {"204": {"class_type": "PromptExpand", "inputs": {
                    "custom_rule": True, "custom_rule_content": "old forced rule"}}}})
                path.write_text(original, encoding="utf-8")
                with patch.object(controller.asyncio, "create_task") as create_task:
                    with self.assertRaisesRegex(ValueError, "重新生成本段"):
                        controller.start(root, plan["id"], {"prompt": current}, MagicMock())
                create_task.assert_not_called()
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                self.assertEqual(core.read_plan(root, plan["id"])["run_status"], plan["run_status"])

    def test_legacy_rule_restore_keeps_absent_fields_absent(self):
        snapshot = {"prompt": {"204": {"class_type": "PromptExpand", "inputs": {
            "custom_rule": True, "custom_rule_content": "old forced rule"}}}}
        controller.restore_legacy_prompt_rules(snapshot, {
            "204": {"class_type": "PromptExpand", "inputs": {"rule": "chosen preset"}}})
        self.assertEqual(snapshot["prompt"]["204"]["inputs"], {"rule": "chosen preset"})

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
            self.assertNotIn("协议：", row["prompt"])
            self.assertNotIn("片段：", row["prompt"])
            self.assertTrue(row["prompt"].startswith("画面任务：人物口播\n"))
            self.assertIn("沿用参考画面的原有构图", row["prompt"])
            self.assertIn("固定机位单一连续镜头", row["prompt"])
            self.assertIn("声音关系：人物跟随本段人声做自然可见口型", row["prompt"])
            self.assertIn("主体动作：自然口播", row["prompt"])
            for reframing in ("胸部以上", "人物居中", "上三分之一", "正面"):
                self.assertNotIn(reframing, row["prompt"])
            self.assertNotIn("生成时长：", row["prompt"])
            self.assertNotIn("主唱", row["prompt"])
            self.assertNotIn("音乐表演", row["prompt"])
            self.assertNotIn("麦克风", row["prompt"])
            self.assertNotIn("构图范围：", row["prompt"])
            self.assertLess(len(row["prompt"]), 700)

    def test_edit_invalidates_approval_and_duration_prompt(self):
        p = sample_plan()
        p["approved"] = True
        old = core.fingerprint(p)
        old_frames = p["segments"][0]["generation_frames"]
        updates = [{"end": s["end"], "prompt": s["prompt"]} for s in p["segments"]]
        updates[0]["end"] = 9
        core.edit_plan(p, updates)
        self.assertFalse(p["approved"])
        self.assertNotEqual(old, core.fingerprint(p))
        self.assertEqual(p["segments"][1]["start"], 9)
        self.assertNotEqual(old_frames, p["segments"][0]["generation_frames"])
        self.assertEqual(p["segments"][0]["prompt"].count("\n"), 6)

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
        self.assertIn("镜头运动：", p["segments"][2]["prompt"])
        self.assertIn(core._zh_framing(p["segments"][2]["camera_start"]),
                      p["segments"][2]["prompt"])

    def test_singing_has_visible_camera_motion_without_material_claims(self):
        p = sample_plan()
        self.assertNotIn("dolly out", p["segments"][0]["camera"])
        for row in p["segments"]:
            self.assertNotIn("协议：", row["prompt"])
            self.assertNotIn("片段：", row["prompt"])
            self.assertTrue(row["prompt"].startswith("画面任务：人物演唱\n"))
            self.assertIn("镜头运动：", row["prompt"])
            self.assertIn("主体动作：", row["prompt"])
            self.assertEqual(row["prompt"].count("\n"), 6)
            self.assertNotIn("<Picture", row["prompt"])
            self.assertNotIn("<Subject", row["prompt"])
            self.assertNotIn("<Audio", row["prompt"])
            self.assertNotIn("构图范围：", row["prompt"])
            self.assertNotIn("有效时长", row["prompt"])
            self.assertNotIn("导演控制：", row["prompt"])
            self.assertNotIn("音频结构依据：", row["prompt"])
            self.assertLess(len(row["prompt"]), 700)
        self.assertIn(p["segments"][0]["camera_start"],
                      p["director"]["rule_config"]["singing"]["allowed_framings"])
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

    def test_dynamic_manifest_never_infers_role_from_picture_count(self):
        with self.assertRaisesRegex(ValueError, "尚未指定"):
            core.dynamic_reference_manifest([{"picture": 1, "role": ""}], 1)
        with self.assertRaisesRegex(ValueError, "只能指定1张"):
            core.dynamic_reference_manifest([
                {"picture": 1, "role": "environment", "name": "户外"},
                {"picture": 2, "role": "appearance", "name": "衣服"},
            ], 2)
        manifest = core.dynamic_reference_manifest([
            {"picture": 1, "role": "performer", "name": "主唱"},
            {"picture": 2, "role": "environment", "name": "海边"},
            {"picture": 3, "role": "appearance", "name": "黑色演出服"},
        ], 3)
        self.assertEqual(manifest["layout"], "dynamic")
        self.assertEqual(manifest["picture_count"], 3)
        self.assertEqual(manifest["performers"][0]["pictures"], [1, 3])
        self.assertEqual(manifest["environment"]["pictures"], [2])

    def test_reference_manifest_does_not_affect_camera_brief(self):
        plain = sample_plan()
        with_legacy_references = sample_plan()
        with_legacy_references["references"] = core.reference_manifest("solo_scene")
        core.decorate(with_legacy_references)
        self.assertEqual(plain["segments"][0]["prompt"],
                         with_legacy_references["segments"][0]["prompt"])

    def test_decorate_discards_legacy_final_prompt_fields(self):
        p = sample_plan()
        for row in p["segments"]:
            row["h3_prompt"] = "legacy"
            row["h3_prompt_mode"] = "custom"
        core.decorate(p, regenerate_prompts=False)
        self.assertTrue(all("h3_prompt" not in row and "h3_prompt_mode" not in row
                            for row in p["segments"]))

    def test_new_generation_snapshot_keeps_current_output_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = sample_plan()
            plan["approved"] = True
            plan["approved_fingerprint"] = core.fingerprint(plan)
            core.write_plan(directory, plan)
            payload = {"loader_id": "231", "video_id": "122", "prompt": {
                "231": {"class_type": "H3LVUnified", "inputs": {}},
                "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
                    "positive": ["231", 2]}},
                "122": {"class_type": "VHS_VideoCombine", "inputs": {}},
            }}
            task = MagicMock()
            def capture_task(coroutine):
                coroutine.close()
                return task
            with patch.object(controller.asyncio, "create_task", side_effect=capture_task):
                controller.start(directory, plan["id"], payload, MagicMock())
            snapshot = json.loads(core.state_file(
                core.project_path(directory, plan["id"]), "queue_snapshot.json"
            ).read_text(encoding="utf-8"))
            self.assertNotIn("204", snapshot["prompt"])
            self.assertEqual(snapshot["prompt"]["136"]["inputs"]["positive"], ["231", 2])
            controller.TASKS.pop(plan["id"], None)

    def test_segment_brief_is_material_agnostic(self):
        prompt = sample_plan()["segments"][0]["prompt"]
        for metadata in ("协议：", "片段："):
            self.assertNotIn(metadata, prompt)
        for token in ("<Picture", "<Subject", "<Audio", "参考角色", "手持道具", "穿戴配饰"):
            self.assertNotIn(token, prompt)
        labels = [line.split("：", 1)[0] for line in prompt.splitlines()]
        self.assertEqual(labels, ["画面任务", "声音关系", "开场构图", "主体动作", "镜头运动", "结束构图"])
        self.assertEqual(core.validate_segment_brief(prompt), prompt.strip())

    def test_long_intro_marks_every_covered_segment_as_atmosphere(self):
        plan = sample_plan()
        plan["audio_structure"] = {"sections": [{"start": 0, "end": 20, "kind": "intro",
                                                   "reason": "无识别文字且分离人声活动较低"}]}
        for index, row in enumerate(plan["segments"]):
            row.pop("prompt", None)
            row.pop("visual_type", None)
            row["text"] = "" if index < 2 else "主歌"
        core.decorate(plan)
        self.assertEqual([row["visual_type"] for row in plan["segments"]],
                         ["atmosphere", "atmosphere", "performance"])
        self.assertTrue(all(row["audio_section"] == "intro" for row in plan["segments"][:2]))
        self.assertIn("疑似前奏", plan["segments"][0]["prompt"])
        self.assertIn("人物不张口演唱", plan["segments"][1]["prompt"])

    def test_handwritten_prompt_changes_fingerprint_without_format_validation(self):
        plan = sample_plan()
        original = core.fingerprint(plan)
        updates = [dict(row) for row in plan["segments"]]
        updates[0]["final_prompt"] = "ordinary free-form H3 prompt"
        core.edit_plan(plan, updates)
        self.assertEqual(plan["segments"][0]["final_prompt"], "ordinary free-form H3 prompt")
        self.assertNotEqual(core.fingerprint(plan), original)

    def test_existing_two_field_camera_brief_remains_valid(self):
        prompt = "镜头方案：向右环绕人物\n表演节奏：克制的音乐表演\n"
        self.assertEqual(core.validate_segment_brief(prompt), prompt.strip())

    def test_camera_brief_accepts_material_tokens(self):
        prompt = sample_plan()["segments"][0]["prompt"] + "素材：<Picture 1>\n"
        self.assertEqual(core.validate_segment_brief(prompt), prompt.strip())

    def test_camera_brief_accepts_a_complete_ref2va_prompt(self):
        full = ("subject_definitions:\n<Subject 1> is the performer from <Picture 1>.\n"
                "summary:\n[reference generation]\nretention_analysis:\nfully_preserved\n"
                "detailed_description:\n[Shot 1] <Subject 1> sings.\noverall_soundscape:\nN/A\n"
                "non_diegetic_music:\nN/A\n")
        self.assertEqual(core.validate_segment_brief(full), full.strip())

    def test_brief_text_prefixes_the_per_segment_material_note(self):
        row = {"prompt": "模式：唱歌\n镜头方案：固定\n表演节奏：自然", "material_note": "图1是人物。"}
        self.assertEqual(core.brief_text(row),
                         "素材说明：图1是人物。\n模式：唱歌\n镜头方案：固定\n表演节奏：自然")
        row["material_note"] = ""
        self.assertEqual(core.brief_text(row), row["prompt"])
        row["material_note"] = "图1是人物。"
        row["prompt"] = "素材说明：已在文本里。\n镜头方案：固定"
        self.assertEqual(core.brief_text(row), row["prompt"])

    def test_legacy_seven_field_camera_brief_remains_valid(self):
        prompt = ("生成时长：10.125 秒\n开场构图：中近景正面\n段内运镜：固定机位\n"
                  "结束构图：中近景正面\n入口衔接：开场\n出口运动状态：静止\n"
                  "表演节奏：自然口型和克制的小幅动作\n")
        self.assertEqual(core.validate_segment_brief(prompt), prompt.strip())

    def test_arc_camera_plan_changes_to_compatible_ending_angle(self):
        angles = ["front", "front three-quarter right", "front three-quarter left"]
        self.assertEqual(core._arc_ending_angle("front", "right", angles),
                         "front three-quarter right")
        row = {"camera_start_angle": "front", "camera_end_angle": "front three-quarter right",
               "camera_move_family": "arc", "camera_move_direction": "right",
               "performance_direction": "restrained music-driven expression"}
        prompt = core.segment_brief(
            {"mode": "singing"}, row, "medium close-up", "medium close-up", "", "medium close-up")
        self.assertIn("实体向右侧环绕人物约20度", prompt)
        self.assertIn("不是原地摇镜", prompt)

    def test_local_schedule_is_reproducible_and_removes_legacy_ai_state(self):
        first = sample_plan()
        second = sample_plan()
        first["director"]["mode"] = "ai"
        first["ai_shot_plan"] = [{"index": 0, "movement": "steady"}]
        core.decorate(first)
        self.assertEqual(first["director"]["mode"], "rule")
        self.assertNotIn("ai_shot_plan", first)
        self.assertEqual(first["shot_plan_version"], 14)
        self.assertEqual(
            [row["camera_move_type"] for row in first["segments"]],
            [row["camera_move_type"] for row in second["segments"]])

    def test_adjacent_singing_segments_never_repeat_movement_family(self):
        p = sample_plan()
        p.update(samples=8000, duration=80)
        p["segments"] = [{"start_sample": i * 1000, "end_sample": (i + 1) * 1000,
                          "energy_db": (-30, -15, -5)[i % 3], "text": "vocal"}
                         for i in range(8)]
        core.decorate(p)
        families = [core._movement_type_family(row["camera_move_type"]) for row in p["segments"]]
        self.assertTrue(all(first != second for first, second in zip(families, families[1:])))
        self.assertTrue(all(row["camera_move_family"] != "steady" for row in p["segments"]))

    def test_reused_lateral_moves_alternate_direction(self):
        config = director_rules.default_config()
        for band in ("low", "medium", "high"):
            config["singing"]["energy_movements"][band] = [
                "truck_left", "truck_right", "micro_reframe"]
        rows = [{"energy_db": -20, "text": "vocal"} for _ in range(12)]
        prefs = {"performance_intensity": "auto", "note": "",
                 "schedule_seed": "lateral-test", "rule_revision": "one",
                 "rule_config": config}
        states = core.camera_sequence("singing", rows, prefs)
        directions = [row["camera_move_direction"] for row in states
                      if row["camera_move_family"] == "lateral"]
        self.assertGreaterEqual(len(directions), 2)
        self.assertTrue(all(first != second for first, second in zip(directions, directions[1:])))

    def test_plugin_has_no_ai_director_surface_or_settings_route(self):
        self.assertFalse((ROOT / "ai_director.py").exists())
        script = (ROOT / "web" / "h3lv.js").read_text(encoding="utf-8")
        routes_source = (ROOT / "routes.py").read_text(encoding="utf-8")
        nodes_source = (ROOT / "nodes.py").read_text(encoding="utf-8")
        for token in ("AI导演", "导演模型 API Key", "/h3lv/settings"):
            self.assertNotIn(token, script)
            self.assertNotIn(token, routes_source)
        self.assertNotIn("ai_director", nodes_source)

    def test_adjacent_high_energy_shots_do_not_repeat_push_in(self):
        rows = [{"energy_db": energy, "text": "vocal"} for energy in [-30, -5, -4, -3, -20]]
        states = core.camera_sequence("singing", rows, core.director_preferences(sample_plan()))
        for first, second in zip(states, states[1:]):
            self.assertFalse(first["camera_move_family"] == second["camera_move_family"] == "dolly in")

    def test_legacy_camera_controls_are_ignored_and_note_is_not_emitted(self):
        p = sample_plan()
        baseline = copy.deepcopy(p)
        p["director"].update(performance_intensity="energetic", camera_activity="steady",
                             widest_framing="close-up", note="Keep gestures compact.")
        core.decorate(p)
        self.assertTrue(all(row["camera_move_family"] != "steady" for row in p["segments"]))
        self.assertEqual([(row["camera_start"], row["camera_end"], row["camera_move_type"])
                          for row in p["segments"]],
                         [(row["camera_start"], row["camera_end"], row["camera_move_type"])
                          for row in baseline["segments"]])
        self.assertNotIn("Keep gestures compact.", p["segments"][0]["prompt"])
        self.assertIn("投入而受控的音乐表演", p["segments"][0]["prompt"])

    def test_auto_plan_can_cut_while_motion_remains_active(self):
        p = sample_plan()
        moving = [row for row in p["segments"] if row["camera_move_family"] != "steady"]
        self.assertTrue(moving)
        self.assertTrue(all("active at the cut" in row["exit_motion_state"] for row in moving))
        self.assertTrue(any("镜头运动：" in row["prompt"] and "至片段结束" in row["prompt"]
                            for row in moving))

    def test_equal_energy_sequence_still_varies_composition_and_motion(self):
        rows = [{"energy_db": -20, "text": "vocal"} for _ in range(4)]
        prefs = {"performance_intensity": "auto", "note": "",
                 "rule_config": wide_rule_config()}
        states = core.camera_sequence("singing", rows, prefs)
        self.assertTrue(all(state["camera_move_family"] != "steady" for state in states))
        for previous, current in zip(states, states[1:]):
            self.assertNotEqual(
                (previous["camera_end"], previous["camera_end_angle"]),
                (current["camera_start"], current["camera_start_angle"]))

    def test_ref2va_rule_does_not_force_a_settle_before_each_cut(self):
        rule = (ROOT/"ref2va_performance_rule.txt").read_text(encoding="utf-8")
        self.assertNotIn("settle before the explicitly stated padding", rule)
        self.assertIn("ending motion state", rule)

    def test_ref2va_rule_maps_the_versioned_brief_without_redirection(self):
        rule = (ROOT/"ref2va_performance_rule.txt").read_text(encoding="utf-8")
        self.assertNotIn("H3LV_CAMERA_V1", rule)
        self.assertIn("模式 (口播/speaking or 唱歌/singing)", rule)
        self.assertIn("Speaking mode only:", rule)
        self.assertIn("Singing mode only:", rule)
        self.assertIn("not a pixel-exact first-frame guarantee", rule)
        self.assertIn("镜头方案 and 表演节奏", rule)
        self.assertIn("legacy seven-field format", rule)
        self.assertIn("user-written material description", rule)
        self.assertIn("authority for reference roles and relationships", rule)
        self.assertNotIn("MISSING MATERIAL DESCRIPTION", rule)
        self.assertIn("exactly one [Shot 1]", rule)
        self.assertIn("Omit undeclared details", rule)

    def test_every_singing_boundary_has_an_explicit_non_jump_cut(self):
        p = sample_plan()
        for previous, current in zip(p["segments"], p["segments"][1:]):
            same_size = current["camera_start"] == previous["camera_end"]
            same_angle = current["camera_start_angle"] == previous["camera_end_angle"]
            self.assertFalse(same_size and same_angle)
            self.assertIn(current["entry_cut_strategy"], {
                "shot-size cut", "30-degree angle cut", "shot-size plus angle cut"})
            self.assertIn("镜头运动：", previous["prompt"])
            self.assertIn("镜头运动：", current["prompt"])

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
            self.assertEqual(loaded[3], f"H3LongVideo/projects/{plan['id']}/takes/seg_0000")

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
            second_final = controller.assemble(root, p["id"])
            self.assertNotEqual(final, second_final)
            self.assertTrue(second_final.endswith("_v2.mp4"))
            self.assertEqual(Path(final).parent, Path(d)/"H3LongVideo"/"final_videos")
            stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(p["created"]))
            self.assertEqual(Path(final).name, f"{stamp}_singing_aaaaaaaa.mp4")
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


class PlaybackTests(unittest.TestCase):
    def test_byte_range_parsing(self):
        self.assertEqual(routes.byte_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(routes.byte_range("bytes=100-", 1000), (100, 999))
        self.assertEqual(routes.byte_range("bytes=-100", 1000), (900, 999))
        self.assertEqual(routes.byte_range(" bytes=0-5000 ", 1000), (0, 999))
        self.assertEqual(routes.byte_range("bytes=0-", 1), (0, 0))
        self.assertIsNone(routes.byte_range(None, 1000))
        self.assertIsNone(routes.byte_range("", 1000))
        self.assertIsNone(routes.byte_range("items=0-10", 1000))
        self.assertIsNone(routes.byte_range("bytes=1000-", 1000))
        self.assertIsNone(routes.byte_range("bytes=500-400", 1000))
        self.assertIsNone(routes.byte_range("bytes=-0", 1000))
        self.assertIsNone(routes.byte_range("bytes=0-10", 0))

    def test_audio_route_advertises_seekable_ranges(self):
        source = (ROOT/"routes.py").read_text(encoding="utf-8")
        self.assertIn('"Accept-Ranges": "bytes"', source)
        self.assertIn('headers["Content-Range"]', source)
        self.assertIn("status=206", source)

    def test_top_player_switches_between_original_and_vocals(self):
        script = (ROOT/"web"/"h3lv.js").read_text(encoding="utf-8")
        styles = (ROOT/"web"/"h3lv.css").read_text(encoding="utf-8")
        self.assertIn('[["original", "原曲"], ["vocals", "人声"]]', script)
        self.assertIn("function syncTrackSwitch()", script)
        self.assertIn("function selectTrack(value)", script)
        self.assertIn("function selectedPreviewUrl(index)", script)
        self.assertIn('return previewUrl(index, selectedTrack === "vocals");', script)
        self.assertIn("selectedAudio.src = selectedPreviewUrl(selected);", script)
        self.assertIn('当前试听（${selectedTrack === "vocals" ? "人声" : "原曲"}）', script)
        self.assertIn(".h3lv-track-button.is-active", styles)
        self.assertIn(".h3lv-selected-info", styles)

    def test_segment_player_auditions_the_original_mix(self):
        script = (ROOT/"web"/"h3lv.js").read_text(encoding="utf-8")
        styles = (ROOT/"web"/"h3lv.css").read_text(encoding="utf-8")
        self.assertIn("本段原曲试听（包含伴奏、前奏和间奏，可拖动进度条）", script)
        self.assertIn('vocals ? "&vocals=1"', script)
        self.assertIn("rows[index].audio.src = previewUrl(index)", script)
        self.assertIn('segmentAudio.src = api.apiURL(endpoint(`/audio?index=${row.index}&revision=${plan.revision}`))', script)
        self.assertNotIn("boundary=1", script)
        self.assertIn(".h3lv-segment-audio", styles)
        self.assertNotIn("h3lv-cut-audio", styles)


class ExportTests(unittest.TestCase):
    def test_redundant_review_controls_are_removed(self):
        script = (ROOT/"web"/"h3lv.js").read_text(encoding="utf-8")
        styles = (ROOT/"web"/"h3lv.css").read_text(encoding="utf-8")
        routes_source = (ROOT/"routes.py").read_text(encoding="utf-8")
        self.assertNotIn("导出分段时长", script)
        self.assertNotIn("segmentTimingMarkdown", script)
        self.assertNotIn("h3lv-export", styles)
        self.assertNotIn("复制成片目录", script)
        self.assertNotIn("/h3lv/final-folder", routes_source)


class ReferenceImageTests(unittest.TestCase):
    def reference_project(self, directory, names=("a.png", "b.png"), segments=3):
        plan = sample_plan()
        if segments != len(plan["segments"]):
            plan["samples"] = segments*1000
            plan["duration"] = segments*10
            plan["segments"] = [{"start_sample": i*1000, "end_sample": (i+1)*1000,
                                 "energy_db": -30+i*10, "text": "未校对歌词"}
                                for i in range(segments)]
            core.decorate(plan)
        plan["approved"] = True
        plan["approved_fingerprint"] = core.fingerprint(plan)
        core.write_plan(directory, plan)
        root = core.project_path(directory, plan["id"])
        (root/"refs").mkdir(parents=True, exist_ok=True)
        for name in names:
            (root/"refs"/name).write_bytes(b"image")
        return plan, root

    def test_store_and_remove_reference_files(self):
        with tempfile.TemporaryDirectory() as d:
            directory = core.project_path(d, "a"*32)
            from PIL import Image
            buffer = io.BytesIO()
            Image.new("RGB", (8, 8)).save(buffer, format="PNG")
            name = core.store_reference(directory, 2, "PIC.PNG", buffer.getvalue())
            self.assertTrue(name.startswith("seg0002_"))
            self.assertTrue(name.endswith(".png"))
            self.assertTrue((core.reference_directory(directory)/name).is_file())
            core.remove_reference(directory, name)
            self.assertFalse((core.reference_directory(directory)/name).is_file())
            with self.assertRaisesRegex(ValueError, "只支持"):
                core.store_reference(directory, 0, "notes.txt", b"payload")

    def test_reference_limit_and_missing_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            directory = core.project_path(d, "a"*32)
            with self.assertRaisesRegex(ValueError, "最多 6 张"):
                core.normalize_reference_names([f"{i}.png" for i in range(7)])
            with self.assertRaisesRegex(ValueError, "已丢失"):
                core.normalize_reference_names(["missing.png"], directory)
            with self.assertRaisesRegex(ValueError, "文件名无效"):
                core.normalize_reference_names(["../escape.png"], directory)

    def test_segment_references_replace_the_load_image_files(self):
        with tempfile.TemporaryDirectory() as d:
            plan, root = self.reference_project(d)
            plan["segments"][0]["refs"] = ["a.png", "b.png"]
            prompt = {
                "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
                    "ref_images.ref_image_0": ["137", 0],
                    "ref_images.ref_image_1": ["217", 0],
                    "ref_images.ref_image_2": ["218", 0]}},
                "137": {"class_type": "LoadImage", "inputs": {"image": "person.png"}},
                "217": {"class_type": "LoadImage", "inputs": {"image": "scene.png"}},
                "218": {"class_type": "LoadImage", "inputs": {"image": "extra.png"}},
            }
            inputs = Path(d)/"input"
            inputs.mkdir()
            with patch.dict(sys.modules, {"folder_paths": types.SimpleNamespace(
                    get_input_directory=lambda: str(inputs))}):
                controller.apply_segment_references(prompt, plan, plan["segments"][0], root)
            self.assertEqual(prompt["137"]["inputs"]["image"], f"H3LV/{plan['id']}/a.png")
            self.assertEqual(prompt["217"]["inputs"]["image"], f"H3LV/{plan['id']}/b.png")
            self.assertNotIn("ref_images.ref_image_2", prompt["136"]["inputs"])
            self.assertNotIn("218", prompt)
            self.assertTrue((inputs/"H3LV"/plan["id"]/"a.png").is_file())

    def test_segment_references_never_exceed_the_connected_slots(self):
        with tempfile.TemporaryDirectory() as d:
            plan, root = self.reference_project(d, ("a.png", "b.png", "c.png"))
            plan["segments"][1]["refs"] = ["a.png", "b.png", "c.png"]
            prompt = {"136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
                "ref_images.ref_image_0": ["137", 0]}},
                "137": {"class_type": "LoadImage", "inputs": {"image": "person.png"}}}
            with self.assertRaisesRegex(ValueError, "只接出了 1 个"):
                controller.apply_segment_references(prompt, plan, plan["segments"][1], root)

    def test_segment_without_references_leaves_the_graph_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            plan, root = self.reference_project(d)
            prompt = {"136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
                "ref_images.ref_image_0": ["137", 0]}},
                "137": {"class_type": "LoadImage", "inputs": {"image": "person.png"}}}
            before = copy.deepcopy(prompt)
            controller.apply_segment_references(prompt, plan, plan["segments"][0], root)
            self.assertEqual(prompt, before)

    def canvas_prompt(self):
        return {
            "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
                "ref_images.ref_image_0": ["137", 0],
                "ref_images.ref_image_1": ["217", 0],
                "ref_images.ref_image_2": ["218", 0]}},
            "137": {"class_type": "LoadImage", "inputs": {"image": "person.png"}},
            "217": {"class_type": "LoadImage", "inputs": {"image": "scene.png"}},
            "218": {"class_type": "LoadImage", "inputs": {"image": "extra.png"}},
        }

    def test_default_reference_count_trims_canvas_slots(self):
        with tempfile.TemporaryDirectory() as d:
            plan, root = self.reference_project(d)
            plan["reference_default_count"] = 2
            prompt = self.canvas_prompt()
            controller.apply_segment_references(prompt, plan, plan["segments"][0], root)
            self.assertEqual(prompt["137"]["inputs"]["image"], "person.png")
            self.assertEqual(prompt["217"]["inputs"]["image"], "scene.png")
            self.assertNotIn("ref_images.ref_image_2", prompt["136"]["inputs"])
            self.assertNotIn("218", prompt)

    def test_zero_default_reference_count_feeds_no_picture(self):
        with tempfile.TemporaryDirectory() as d:
            plan, root = self.reference_project(d)
            plan["reference_default_count"] = 0
            prompt = self.canvas_prompt()
            controller.apply_segment_references(prompt, plan, plan["segments"][0], root)
            self.assertEqual(prompt["136"]["inputs"], {})
            for node_id in ("137", "217", "218"):
                self.assertNotIn(node_id, prompt)

    def test_configured_segments_ignore_the_default_count(self):
        with tempfile.TemporaryDirectory() as d:
            plan, root = self.reference_project(d)
            plan["reference_default_count"] = 0
            plan["segments"][0]["refs"] = ["a.png"]
            prompt = self.canvas_prompt()
            inputs = Path(d)/"inputs"
            inputs.mkdir()
            with patch.dict(sys.modules, {"folder_paths": types.SimpleNamespace(
                    get_input_directory=lambda: str(inputs))}):
                controller.apply_segment_references(prompt, plan, plan["segments"][0], root)
            self.assertEqual(prompt["137"]["inputs"]["image"], f"H3LV/{plan['id']}/a.png")
            self.assertNotIn("ref_images.ref_image_1", prompt["136"]["inputs"])

    def test_default_count_changes_invalidate_approval(self):
        plan = sample_plan()
        original = core.fingerprint(plan)
        plan["reference_default_count"] = 2
        self.assertNotEqual(core.fingerprint(plan), original)
        plan["reference_default_count"] = None
        self.assertEqual(core.fingerprint(plan), original)

    def test_default_count_round_trips_through_edit_plan(self):
        with tempfile.TemporaryDirectory() as d:
            plan, root = self.reference_project(d)
            updates = [{"end": s["end"], "prompt": s["prompt"]} for s in plan["segments"]]
            core.edit_plan(plan, updates, root, 2)
            self.assertEqual(plan["reference_default_count"], 2)
            with self.assertRaisesRegex(ValueError, "0 到 6"):
                core.edit_plan(plan, updates, root, 7)
            with self.assertRaisesRegex(ValueError, "必须是整数"):
                core.edit_plan(plan, updates, root, "两张")
            core.edit_plan(plan, updates, root, None)
            self.assertIsNone(plan["reference_default_count"])
            core.edit_plan(plan, updates, root, 1)
            core.edit_plan(plan, updates, root)
            self.assertEqual(plan["reference_default_count"], 1)

    def test_edit_plan_saves_references_for_one_segment_only(self):
        with tempfile.TemporaryDirectory() as d:
            plan, root = self.reference_project(d)
            for index, row in enumerate(plan["segments"]):
                row["job"] = {"status": "completed", "video": f"old_{index}.mp4"}
            plan["final_video"] = "old_final.mp4"
            updates = [{"end": s["end"], "prompt": s["prompt"]} for s in plan["segments"]]
            updates[1]["refs"] = ["a.png"]
            updates[1]["material_note"] = "图1是人物。"
            core.edit_plan(plan, updates, root)
            self.assertEqual(plan["segments"][1]["refs"], ["a.png"])
            self.assertEqual(plan["segments"][1]["material_note"], "图1是人物。")
            self.assertEqual(plan["segments"][0]["refs"], [])
            self.assertTrue(plan["segments"][1]["needs_regeneration"])
            self.assertEqual(plan["changed_segments"], [1])
            self.assertTrue(plan["final_stale"])

    def test_references_invalidate_the_approved_fingerprint(self):
        plan = sample_plan()
        original = core.fingerprint(plan)
        plan["segments"][0]["refs"] = ["a.png"]
        self.assertNotEqual(core.fingerprint(plan), original)
        plan["segments"][0]["refs"] = []
        self.assertEqual(core.fingerprint(plan), original)
        plan["segments"][0]["material_note"] = "图1是人物。"
        self.assertNotEqual(core.fingerprint(plan), original)

    def test_review_exposes_per_segment_reference_uploads(self):
        script = (ROOT/"web"/"h3lv.js").read_text(encoding="utf-8")
        materials_script = (ROOT/"web"/"materials.js").read_text(encoding="utf-8")
        styles = (ROOT/"web"/"h3lv.css").read_text(encoding="utf-8")
        routes_source = (ROOT/"routes.py").read_text(encoding="utf-8")
        self.assertIn("function referenceSlotLimit()", script)
        self.assertIn("async function uploadReferenceImage(", script)
        self.assertIn("materialControls.getNote() : row.note.value, refs: row.refs", script)
        self.assertIn("h3lv-material-note", styles)
        self.assertIn(".h3lv-reference-item", styles)
        self.assertIn(".h3lv-default-references select", styles)
        self.assertIn("reference_default_count", script)
        self.assertIn("套用到所有分段", script)
        self.assertIn("renderRefs: renderReferences", script)
        self.assertIn('element("label", "本段画面", shotControl)', script)
        self.assertIn("选择后会立即重写本段导演简报", script)
        self.assertIn('request(endpoint("/brief-preview")', script)
        self.assertIn('visualTypeSelect.onchange = async () =>', script)
        self.assertIn("切换画面类型会重写当前导演简报", script)
        self.assertNotIn('actionButton(controls, "保存草稿"', script)
        self.assertIn('title: "放弃未保存的修改？"', script)
        self.assertIn('brief_matches_visual_type:true', script)
        self.assertIn('["atmosphere", "人物氛围表演"]', script)
        self.assertNotIn("画面类型", materials_script)
        self.assertIn("最多 6 张", materials_script)
        self.assertIn(".h3lv-default-materials-header", styles)
        self.assertIn('element("details", undefined, content, "h3lv-default-materials")', script)
        self.assertIn('defaultMaterials.open ? "收起" : "展开"', script)
        self.assertIn('const defaultOpen = defaultMaterials.open', script)
        self.assertIn('defaultMaterials.open = defaultOpen', script)
        self.assertIn("if (!plan.materials_version && !plan.segments.some(row => row.job))", script)
        self.assertNotIn("owner.outputs?.slice(5).some", script)
        self.assertIn("addButton.disabled = !referenceLimit", script)
        self.assertNotIn('throw new Error("当前工作流还没有接出 ref_image 槽位', script)
        self.assertIn('.h3lv-button:disabled.is-inherited', styles)
        self.assertIn('add.classList.toggle("is-inherited"', materials_script)
        self.assertIn(".h3lv-shot-control", styles)
        self.assertIn('@routes.post("/h3lv/project/{project_id}/refs")', routes_source)
        self.assertIn('@routes.post("/h3lv/project/{project_id}/brief-preview")', routes_source)
        self.assertIn('@routes.post("/h3lv/project/{project_id}/refs/remove")', routes_source)
        self.assertIn('@routes.get("/h3lv/project/{project_id}/refs/{name}")', routes_source)


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_serial_queue_and_completion(self):
        with tempfile.TemporaryDirectory() as d:
            plan = sample_plan(); plan["approved"] = True
            plan["approved_fingerprint"] = core.fingerprint(plan)
            core.write_plan(d, plan)
            directory = core.project_path(d, plan["id"])
            assistant_inputs = {"rule": "My preset", "custom_rule": True,
                                "custom_rule_content": "My rule: no subtitles", "source_text": ["1", 2]}
            snapshot = {"loader_id": "1", "video_id": "7", "client_id": "browser-123",
                        "prompt": {"1": {"class_type": "H3LVUnified", "inputs": {}},
                                   "7": {"class_type": "VHS_VideoCombine", "inputs": {}},
                                   "204": {"class_type": "PromptExpand", "inputs": assistant_inputs}}}
            indices, histories, client_ids, create_times, events, submitted_rules = [], {}, [], [], [], []
            class Queue:
                def put(self, item):
                    index = item[2]["1"]["inputs"]["segment_index"]
                    indices.append(index)
                    submitted_rules.append(copy.deepcopy(item[2]["204"]["inputs"]))
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
                controller.start(d, plan["id"], snapshot, server)
                await controller.TASKS[plan["id"]]
            result = core.read_plan(d, plan["id"])
            self.assertEqual(indices, [0, 1, 2])
            self.assertEqual(submitted_rules, [assistant_inputs] * 3)
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

    async def test_queued_prompt_receives_the_segment_reference_images(self):
        with tempfile.TemporaryDirectory() as d:
            plan = sample_plan()
            plan["segments"][1]["refs"] = ["seg.png"]
            plan["approved"] = True
            plan["approved_fingerprint"] = core.fingerprint(plan)
            plan["run_only_segment"] = 1
            directory = core.project_path(d, plan["id"])
            (directory/"refs").mkdir(parents=True)
            (directory/"refs"/"seg.png").write_bytes(b"image")
            core.write_plan(d, plan)
            core.state_file(directory, "queue_snapshot.json").write_text(json.dumps({
                "loader_id": "1", "video_id": "7",
                "prompt": {
                    "1": {"class_type": "H3LVUnified", "inputs": {}},
                    "7": {"class_type": "VHS_VideoCombine", "inputs": {}},
                    "136": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {
                        "ref_images.ref_image_0": ["137", 0],
                        "ref_images.ref_image_1": ["217", 0]}},
                    "137": {"class_type": "LoadImage", "inputs": {"image": "person.png"}},
                    "217": {"class_type": "LoadImage", "inputs": {"image": "scene.png"}}}}),
                encoding="utf-8")
            submitted, histories = {}, {}
            class Queue:
                def put(self, item):
                    submitted.update(item[2])
                    path = directory/"new.mp4"; path.write_bytes(b"new")
                    histories[item[1]] = {"status": {"status_str": "success"}, "outputs": {"7": {"gifs": [
                        {"type": "output", "subfolder": plan["id"], "filename": path.name}]}}}
                def get_history(self, prompt_id): return {prompt_id: histories[prompt_id]}
                def get_current_queue(self): return [], []
            async def validate(*args): return True, None, ["7"], {}
            inputs = Path(d)/"inputs"
            inputs.mkdir()
            server = types.SimpleNamespace(number=0, prompt_queue=Queue())
            folders = types.SimpleNamespace(get_output_directory=lambda: d,
                                            get_input_directory=lambda: str(inputs))
            with patch.dict(sys.modules, {"execution": types.SimpleNamespace(validate_prompt=validate),
                                          "folder_paths": folders}):
                await controller.execute_project(d, plan["id"], server)
            self.assertEqual(submitted["137"]["inputs"]["image"], f"H3LV/{plan['id']}/seg.png")
            self.assertNotIn("ref_images.ref_image_1", submitted["136"]["inputs"])
            self.assertNotIn("217", submitted)
            self.assertTrue((inputs/"H3LV"/plan["id"]/"seg.png").is_file())


if __name__ == "__main__": unittest.main()
