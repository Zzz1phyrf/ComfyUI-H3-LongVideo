"""添加/删除分段与逐段景别覆盖的回归测试。"""
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("h3lv_shots")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("h3lv_shots", package)
core = importlib.import_module("h3lv_shots.core")
director_rules = importlib.import_module("h3lv_shots.director_rules")


def sample_plan(mode="singing", spans=None, max_seconds=15, samples=None):
    """3 段 × 7 秒；两段合并后 14 秒，仍在最长时长以内。"""
    spans = spans or [(0, 700), (700, 1400), (1400, 2100)]
    samples = samples or spans[-1][1]
    return core.decorate({
        "id": "b"*32, "sample_rate": 100, "samples": samples, "duration": samples/100,
        "mode": mode, "max_seconds": max_seconds, "target_seconds": 7,
        "director": {"performance_intensity": "auto", "note": "",
                      "rule_config": director_rules.default_config(),
                      "schedule_seed": "sample-audio", "rule_revision": "test-rules"},
        "approved": False, "revision": 1, "run_status": "draft", "created": 0,
        "segments": [{"start_sample": start, "end_sample": end, "energy_db": -30+i*10,
                      "text": "未校对歌词", "refs": [f"seg{i:04d}_a.png"],
                      "material_note": f"第{i+1}段素材"}
                     for i, (start, end) in enumerate(spans)]})


def other_shot_size(plan, index):
    """A framing that differs from what the director rules already planned."""
    planned = plan["segments"][index]["shot_size"]
    return next(key for key in ("close-up", "medium shot", "full shot", "medium close-up")
                if key != planned)


class ShotSizeVocabularyTests(unittest.TestCase):
    def test_labels_aliases_and_rejection(self):
        self.assertEqual(core.normalize_shot_size("全身景（头顶至双脚完整入画）"), "full shot")
        self.assertEqual(core.normalize_shot_size("中景（腰部以上）"), "medium shot")
        self.assertEqual(core.normalize_shot_size("中近景（胸部以上）"), "medium close-up")
        self.assertEqual(core.normalize_shot_size("近景（头部与肩部）"), "close-up")
        self.assertEqual(core.normalize_shot_size("中近景"), "medium close-up")
        self.assertEqual(core.normalize_shot_size("full shot"), "full shot")
        self.assertEqual(core.normalize_shot_size(""), "")
        self.assertEqual(core.normalize_shot_size(None), "")
        with self.assertRaisesRegex(ValueError, "景别无效"):
            core.normalize_shot_size("超特写")

    def test_reads_shot_size_back_from_brief(self):
        for key, label in core.SHOT_SIZE_LABELS.items():
            brief = f"画面任务：人物演唱\n开场构图：{label}正面开场，依表演朝向留出空间\n"
            self.assertEqual(core.shot_size_from_brief(brief), key, label)
        self.assertEqual(core.shot_size_from_brief("画面任务：人物口播\n开场构图：沿用参考画面的原有构图"), "")
        self.assertEqual(core.shot_size_from_brief(""), "")

    def test_every_planned_segment_exposes_its_shot_size(self):
        plan = sample_plan()
        for row in plan["segments"]:
            self.assertIn(row["shot_size"], core.SHOT_SIZE_LABELS)
            self.assertEqual(core.shot_size_from_brief(row["prompt"]), row["shot_size"])


class ShotSizeOverrideTests(unittest.TestCase):
    def test_override_rewrites_opening_composition(self):
        plan = sample_plan()
        target = other_shot_size(plan, 1)
        plan["segments"][1]["shot_size_manual"] = target
        core.decorate(plan, regenerate_prompts=True)
        row = plan["segments"][1]
        self.assertEqual(row["shot_size"], target)
        self.assertEqual(row["camera_start"], target)
        self.assertIn(f"开场构图：{core.SHOT_SIZE_LABELS[target]}", row["prompt"])

    def test_clearing_override_restores_the_planned_framing(self):
        plan = sample_plan()
        automatic = plan["segments"][1]["shot_size"]
        plan["segments"][1]["shot_size_manual"] = other_shot_size(plan, 1)
        core.decorate(plan, regenerate_prompts=True)
        plan["segments"][1].pop("shot_size_manual")
        core.decorate(plan, regenerate_prompts=True)
        self.assertEqual(plan["segments"][1]["shot_size"], automatic)

    def test_speaking_override_mentions_the_shot_size(self):
        plan = sample_plan(mode="speaking")
        self.assertIn("开场构图：沿用参考画面的原有构图", plan["segments"][0]["prompt"])
        plan["segments"][0]["shot_size_manual"] = "medium shot"
        core.decorate(plan, regenerate_prompts=True)
        self.assertIn("开场构图：中景（腰部以上）构图", plan["segments"][0]["prompt"])

    def test_environment_override_keeps_scene_wording(self):
        plan = sample_plan()
        plan["segments"][0]["visual_type"] = "environment"
        plan["segments"][0]["shot_size_manual"] = "full shot"
        core.decorate(plan, regenerate_prompts=True)
        prompt = plan["segments"][0]["prompt"]
        self.assertIn("空镜环境", prompt)
        self.assertIn("以全身景（头顶至双脚完整入画）沿参考场景既有构图开场", prompt)

    def test_preview_accepts_a_shot_size_without_mutating_the_plan(self):
        plan = sample_plan()
        original = plan["segments"][0]["prompt"]
        preview = core.preview_segment_brief(plan, 0, "performance", "close-up")
        self.assertIn("开场构图：近景（头部与肩部）", preview)
        self.assertEqual(plan["segments"][0]["prompt"], original)
        self.assertNotIn("shot_size_manual", plan["segments"][0])


class EditPlanShotSizeTests(unittest.TestCase):
    def _submitted(self, plan, index=None, value=None):
        payload = []
        for i, row in enumerate(plan["segments"]):
            payload.append({"end": row["end"], "prompt": row["prompt"],
                            "shot_size": value if i == index else ""})
        return payload

    def test_returning_the_automatic_value_does_not_lock_the_segment(self):
        plan = sample_plan()
        automatic = plan["segments"][1]["shot_size"]
        plan = core.edit_plan(plan, self._submitted(plan, 1, automatic))
        self.assertNotIn("shot_size_manual", plan["segments"][1])
        self.assertEqual(plan["changed_segments"], [])

    def test_a_different_value_locks_and_rewrites_the_brief(self):
        plan = sample_plan()
        target = other_shot_size(plan, 1)
        plan = core.edit_plan(plan, self._submitted(plan, 1, target))
        self.assertEqual(plan["segments"][1]["shot_size_manual"], target)
        self.assertEqual(plan["segments"][1]["camera_start"], target)
        self.assertIn(f"开场构图：{core.SHOT_SIZE_LABELS[target]}", plan["segments"][1]["prompt"])
        self.assertIn(1, plan["changed_segments"])
        self.assertFalse(plan["approved"])

    def test_clearing_the_lock_returns_to_the_rule_plan(self):
        plan = sample_plan()
        automatic = plan["segments"][0]["shot_size"]
        plan = core.edit_plan(plan, self._submitted(plan, 0, other_shot_size(plan, 0)))
        self.assertIn("shot_size_manual", plan["segments"][0])
        plan = core.edit_plan(plan, self._submitted(plan, 0, ""))
        self.assertNotIn("shot_size_manual", plan["segments"][0])
        self.assertEqual(plan["segments"][0]["shot_size"], automatic)

    def test_resending_the_locked_value_keeps_the_lock(self):
        plan = sample_plan()
        target = other_shot_size(plan, 1)
        plan = core.edit_plan(plan, self._submitted(plan, 1, target))
        self.assertEqual(plan["segments"][1]["shot_size_manual"], target)
        plan = core.edit_plan(plan, self._submitted(plan, 1, target))
        self.assertEqual(plan["segments"][1]["shot_size_manual"], target)
        self.assertEqual(plan["changed_segments"], [])

    def test_an_invalid_shot_size_is_rejected(self):
        plan = sample_plan()
        with self.assertRaisesRegex(ValueError, "景别无效"):
            core.edit_plan(plan, self._submitted(plan, 0, "超特写"))


class SegmentStructureTests(unittest.TestCase):
    def test_split_inserts_one_segment_and_resets_results(self):
        plan = sample_plan()
        plan["segments"][0]["job"] = {"status": "completed", "video": "a.mp4"}
        plan["segments"][0]["takes"] = [{"video": "a.mp4"}]
        plan = core.split_segment(plan, 0, 3.5)
        rows = plan["segments"]
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["index"] for row in rows], [0, 1, 2, 3])
        self.assertEqual(rows[0]["start_sample"], 0)
        self.assertEqual(rows[0]["end_sample"], 350)
        self.assertEqual(rows[1]["start_sample"], 350)
        self.assertEqual(rows[1]["end_sample"], 700)
        for row in rows:
            self.assertNotIn("job", row)
            self.assertTrue(row["prompt"])
        self.assertEqual(rows[0]["refs"], ["seg0000_a.png"])
        self.assertEqual(rows[1]["material_note"], rows[0]["material_note"])
        self.assertEqual(rows[-1]["end_sample"], plan["samples"])
        self.assertFalse(plan["approved"])
        self.assertEqual(plan["run_status"], "draft")
        self.assertEqual(plan["changed_segments"], [0, 1])

    def test_split_requires_a_second_on_both_sides(self):
        plan = sample_plan()
        for time in (0, .5, 6.5, 7):
            with self.subTest(time=time):
                with self.assertRaisesRegex(ValueError, "至少需要 1 秒"):
                    core.split_segment(copy.deepcopy(plan), 0, time)

    def test_split_rejects_an_out_of_range_index(self):
        with self.assertRaisesRegex(ValueError, "分段编号无效"):
            core.split_segment(sample_plan(), 9, 3)

    def test_split_keeps_a_locked_shot_size(self):
        plan = sample_plan()
        target = other_shot_size(plan, 0)
        plan["segments"][0]["shot_size_manual"] = target
        plan = core.split_segment(plan, 0, 3.5)
        self.assertEqual(plan["segments"][0]["shot_size_manual"], target)
        self.assertEqual(plan["segments"][1]["shot_size_manual"], target)
        self.assertIn(core.SHOT_SIZE_LABELS[target], plan["segments"][1]["prompt"])

    def test_remove_merges_into_the_following_segment(self):
        plan = core.remove_segment(sample_plan(), 0)
        rows = plan["segments"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["start_sample"], 0)
        self.assertEqual(rows[0]["end_sample"], 1400)
        self.assertEqual(rows[0]["end_sample"], rows[1]["start_sample"])
        self.assertEqual(rows[-1]["end_sample"], plan["samples"])
        self.assertTrue(all(row["prompt"] for row in rows))

    def test_remove_last_segment_merges_backwards(self):
        plan = core.remove_segment(sample_plan(), 2)
        self.assertEqual(len(plan["segments"]), 2)
        self.assertEqual(plan["segments"][-1]["start_sample"], 700)
        self.assertEqual(plan["segments"][-1]["end_sample"], plan["samples"])

    def test_remove_refuses_the_last_remaining_segment(self):
        with self.assertRaisesRegex(ValueError, "至少需要保留一个分段"):
            core.remove_segment(sample_plan(spans=[(0, 1000)]), 0)

    def test_remove_refuses_a_merge_over_the_maximum_duration(self):
        with self.assertRaisesRegex(ValueError, "超过最长时长"):
            core.remove_segment(sample_plan(spans=[(0, 900), (900, 1800)]), 0)

    def test_structure_edit_is_blocked_while_generating(self):
        plan = sample_plan()
        plan["run_status"] = "running"
        with self.assertRaisesRegex(ValueError, "等当前任务停止"):
            core.split_segment(plan, 0, 3.5)
        with self.assertRaisesRegex(ValueError, "等当前任务停止"):
            core.remove_segment(plan, 0)

    def test_structure_edit_invalidates_the_approved_fingerprint(self):
        plan = sample_plan()
        plan["approved"] = True
        plan["approved_fingerprint"] = core.fingerprint(plan)
        plan = core.split_segment(plan, 1, 10.5)
        self.assertFalse(plan["approved"])
        self.assertNotEqual(plan["approved_fingerprint"], core.fingerprint(plan))


class SegmentRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_and_remove_routes_rewrite_the_saved_plan(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        routes_module = importlib.import_module("h3lv_shots.routes")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = "b"*32
            state = root/project/"state"
            state.mkdir(parents=True)
            plan = sample_plan()
            plan["id"] = project
            (state/"segments.json").write_text(
                json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            (state/"transcript.json").write_text(json.dumps(
                {"segments": [{"start": 0, "end": 7, "text": "第一句"}], "words": []},
                ensure_ascii=False), encoding="utf-8")
            paths = types.SimpleNamespace(get_output_directory=lambda: str(root))
            server = types.SimpleNamespace(routes=web.RouteTableDef())
            server_module = types.SimpleNamespace(
                PromptServer=types.SimpleNamespace(instance=server))
            with patch.dict(sys.modules, {"folder_paths": paths, "server": server_module}), \
                    patch.object(routes_module, "data_root", lambda: str(root)):
                routes_module.register_routes()
                app = web.Application()
                app.add_routes(server.routes)
                async with TestClient(TestServer(app)) as client:
                    stale = await client.post(f"/h3lv/project/{project}/split",
                                              json={"revision": 99, "index": 0, "time": 3.5})
                    self.assertEqual(stale.status, 400)
                    split = await client.post(f"/h3lv/project/{project}/split",
                                              json={"revision": plan["revision"],
                                                    "index": 0, "time": 3.5})
                    self.assertEqual(split.status, 200)
                    data = await split.json()
                    self.assertEqual(len(data["segments"]), 4)
                    self.assertEqual(data["segments"][1]["start"], 3.5)
                    self.assertEqual(data["segments"][1]["end"], 7.0)
                    self.assertTrue(all(row["prompt"] for row in data["segments"]))
                    self.assertFalse(data["approved"])
                    removed = await client.post(f"/h3lv/project/{project}/remove",
                                                json={"revision": data["revision"], "index": 3})
                    self.assertEqual(removed.status, 200)
                    merged = await removed.json()
                    self.assertEqual(len(merged["segments"]), 3)
                    self.assertEqual(merged["segments"][-1]["end"], plan["duration"])


if __name__ == "__main__":
    unittest.main()