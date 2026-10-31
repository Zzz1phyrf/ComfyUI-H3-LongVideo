"""单段时长不再封顶 15 秒、以及项目时长上限可改的回归测试。"""
import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("h3lv_duration")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("h3lv_duration", package)
core = importlib.import_module("h3lv_duration.core")
nodes = importlib.import_module("h3lv_duration.nodes")
director_rules = importlib.import_module("h3lv_duration.director_rules")


def silent_voice(seconds, sr=100):
    return np.zeros((int(seconds * sr), 2), dtype=np.float32)


def make_plan(spans=((0, 1400),), samples=None, max_seconds=15, target_seconds=10,
              mode="singing", project="c" * 32):
    spans = list(spans)
    samples = samples or spans[-1][1]
    return core.decorate({
        "id": project, "sample_rate": 100, "samples": samples, "duration": samples / 100,
        "mode": mode, "max_seconds": max_seconds, "target_seconds": target_seconds,
        "director": {"performance_intensity": "auto", "note": "",
                     "rule_config": director_rules.default_config(),
                     "schedule_seed": "long-audio", "rule_revision": "test-rules"},
        "approved": False, "revision": 1, "run_status": "draft", "created": 0,
        "segments": [{"start_sample": start, "end_sample": end, "energy_db": -30,
                      "text": f"第{i+1}段", "refs": [], "material_note": ""}
                     for i, (start, end) in enumerate(spans)],
    })


class WidgetCeilingTests(unittest.TestCase):
    def test_widgets_accept_long_segments(self):
        required = nodes.Analyze.INPUT_TYPES()["required"]
        for name in ("max_seconds", "target_seconds"):
            _, spec = required[name]
            self.assertEqual(spec["max"], core.MAX_SEGMENT_SECONDS, name)
            self.assertEqual(spec["min"], core.MIN_ANALYSIS_SECONDS, name)
            self.assertGreater(spec["max"], 15, name)

    def test_unified_node_inherits_the_same_ceiling(self):
        required = nodes.Unified.INPUT_TYPES()["required"]
        self.assertEqual(required["max_seconds"][1]["max"], core.MAX_SEGMENT_SECONDS)

    def test_ceiling_stays_inside_the_sampler_frame_cap(self):
        # The H3 sampler's length input tops out at 3600 frames.
        self.assertLessEqual(core.frames_for(core.MAX_SEGMENT_SECONDS, 0), 3600)


class LongSegmentTests(unittest.TestCase):
    def test_segmentation_builds_segments_longer_than_15_seconds(self):
        rows = core.segmentation(silent_voice(60), 100, {}, "singing", 60, 30)
        durations = [(row["end_sample"] - row["start_sample"]) / 100 for row in rows]
        self.assertTrue(all(value <= 60 + 1e-6 for value in durations), durations)
        self.assertGreater(max(durations), 15, durations)
        self.assertEqual(rows[0]["start_sample"], 0)
        self.assertEqual(rows[-1]["end_sample"], 6000)

    def test_segmentation_still_caps_each_segment_at_max_seconds(self):
        rows = core.segmentation(silent_voice(90), 100, {}, "singing", 20, 18)
        durations = [(row["end_sample"] - row["start_sample"]) / 100 for row in rows]
        self.assertTrue(all(value <= 20 + 1e-6 for value in durations), durations)

    def test_target_longer_than_maximum_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "不超过最长时长"):
            core.segmentation(silent_voice(60), 100, {}, "singing", 20, 30)

    def test_target_shorter_than_five_seconds_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "不小于 5 秒"):
            core.segmentation(silent_voice(60), 100, {}, "singing", 15, 4)


class LongSegmentPlanTests(unittest.TestCase):
    def test_decorate_accepts_a_forty_second_segment(self):
        plan = make_plan(spans=[(0, 4000)], samples=4000, max_seconds=60, target_seconds=40)
        row = plan["segments"][0]
        self.assertAlmostEqual(row["duration"], 40, places=3)
        self.assertGreater(row["generation_frames"], 24 * 15)
        self.assertEqual(row["edit_frames"], 960)

    def test_saving_a_segment_beyond_max_seconds_is_rejected(self):
        plan = make_plan(spans=[(0, 800), (800, 1600)], samples=1600, max_seconds=15,
                         target_seconds=8, project="d" * 32)
        with self.assertRaisesRegex(ValueError, "不超过最长时长"):
            core.edit_plan(plan, [
                {"end": 15.5, "prompt": plan["segments"][0]["prompt"]},
                {"end": 16, "prompt": plan["segments"][1]["prompt"]}], ROOT)


class UpdateLimitsTests(unittest.TestCase):
    def test_raising_the_cap_unlocks_longer_segments(self):
        plan = make_plan(spans=[(0, 1400), (1400, 2000)], samples=2000,
                         max_seconds=15, target_seconds=10)
        stretched = [{"end": 18, "prompt": plan["segments"][0]["prompt"]},
                     {"end": 20, "prompt": plan["segments"][1]["prompt"]}]
        # 15 秒上限下，把第一段拉到 18 秒会被拒绝。
        with self.assertRaisesRegex(ValueError, "不超过最长时长"):
            core.edit_plan(copy.deepcopy(plan), stretched, ROOT)
        updated = core.update_limits(plan, 20, 10)
        self.assertEqual(updated["max_seconds"], 20)
        self.assertEqual(updated["target_seconds"], 10)
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(len(updated["segments"]), 2)
        # 上限提高后，同一份提交能保存 18 秒的分段。
        core.edit_plan(updated, stretched, ROOT)
        self.assertAlmostEqual(updated["segments"][0]["duration"], 18, places=3)

    def test_unchanged_values_do_not_bump_the_revision(self):
        plan = make_plan(spans=[(0, 1400)], max_seconds=15, target_seconds=10)
        self.assertIs(core.update_limits(plan, 15, 10), plan)
        self.assertEqual(plan["revision"], 1)

    def test_target_above_maximum_is_rejected(self):
        plan = make_plan(spans=[(0, 1400)], max_seconds=15, target_seconds=10)
        with self.assertRaisesRegex(ValueError, "5 ≤ 目标时长 ≤ 最长时长"):
            core.update_limits(plan, 20, 30)

    def test_cap_above_the_code_ceiling_is_rejected(self):
        plan = make_plan(spans=[(0, 1400)], max_seconds=15, target_seconds=10)
        with self.assertRaisesRegex(ValueError, "最多"):
            core.update_limits(plan, core.MAX_SEGMENT_SECONDS + 1, 10)

    def test_cap_below_an_existing_segment_is_rejected(self):
        plan = make_plan(spans=[(0, 1400)], max_seconds=15, target_seconds=10)
        with self.assertRaisesRegex(ValueError, "不能低于"):
            core.update_limits(plan, 12, 10)

    def test_non_numeric_values_are_rejected(self):
        plan = make_plan(spans=[(0, 1400)], max_seconds=15, target_seconds=10)
        with self.assertRaisesRegex(ValueError, "必须是数字"):
            core.update_limits(plan, "abc", 10)


class LimitRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_limits_route_persists_the_new_cap(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        routes_module = importlib.import_module("h3lv_duration.routes")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = "e" * 32
            state = root / project / "state"
            state.mkdir(parents=True)
            plan = make_plan(spans=[(0, 1400)], max_seconds=15, target_seconds=10,
                             project=project)
            (state / "segments.json").write_text(
                json.dumps(plan, ensure_ascii=False), encoding="utf-8")
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
                    stale = await client.post(f"/h3lv/project/{project}/limits",
                                              json={"revision": 99, "max_seconds": 30})
                    self.assertEqual(stale.status, 400)
                    response = await client.post(f"/h3lv/project/{project}/limits",
                                                 json={"revision": plan["revision"],
                                                       "max_seconds": 30, "target_seconds": 20})
                    self.assertEqual(response.status, 200)
                    data = await response.json()
                    self.assertEqual(data["max_seconds"], 30)
                    self.assertEqual(data["target_seconds"], 20)
                    saved = json.loads((state / "segments.json").read_text(encoding="utf-8"))
                    self.assertEqual(saved["max_seconds"], 30)
                    self.assertEqual(saved["segments"][0]["end_sample"], 1400)


if __name__ == "__main__":
    unittest.main()
