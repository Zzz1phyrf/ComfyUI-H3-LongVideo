import importlib
import inspect
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("h3lv_test")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("h3lv_test", package)
diagnostics = importlib.import_module("h3lv_test.diagnostics")


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        paths = types.SimpleNamespace(get_output_directory=lambda: self.directory.name)
        self.patcher = patch.dict(sys.modules, {"folder_paths": paths})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.key = patch.object(diagnostics, "configured_key", return_value="private-configured-key")
        self.key.start()
        self.addCleanup(self.key.stop)

    def events(self):
        return [json.loads(line) for line in diagnostics.download_log().decode("utf-8").splitlines()]

    def test_exception_context_and_stack_without_locals_or_source_lines(self):
        secret_local = "LOCAL-SHOULD-NOT-APPEAR"
        try:
            raise ValueError("中文测试错误")
        except ValueError as error:
            diagnostics.record_error("prompt_expansion", error, project_id="abc", segment_index=2,
                                     node_id="7", node_type="H3LVPromptExpand")
        event = self.events()[0]
        self.assertEqual(event["message"], "中文测试错误")
        self.assertEqual(event["segment_number"], 3)
        self.assertEqual(event["exception_type"], "ValueError")
        self.assertEqual(event["project_id"], "abc")
        self.assertEqual(event["node_id"], "7")
        self.assertIn("test_exception_context", event["traceback"])
        self.assertNotIn(secret_local, str(event))
        self.assertNotIn('raise ValueError(', event["traceback"])
        self.assertIn("T", event["timestamp"])

    def test_redacts_keys_tokens_urls_and_inline_media(self):
        message = ('private-configured-key Bearer token-123 sk-testsecret '
                   'api_key="other-private-key" password=private-password '
                   'https://user:secret@example.test/v1?signature=secret '
                   'data:image/png;base64,c2VjcmV0')
        diagnostics.record_error("test", RuntimeError(message), request_body=message)
        event = self.events()[0]
        text = json.dumps(event)
        for secret in ("private-configured-key", "token-123", "sk-testsecret", "other-private-key",
                       "private-password", "user:secret", "signature=secret", "c2VjcmV0"):
            self.assertNotIn(secret, text)
        self.assertNotIn("request_body", event)
        self.assertIn("REDACTED", text)

    def test_decorator_preserves_signature_success_and_original_exception(self):
        failure = ValueError("test failure")

        @diagnostics.logged("prompt_expansion")
        def operation(material, model="demo", fail=False):
            if fail:
                raise failure
            return material["project_id"]

        material = {"project_id": "abc", "segment_index": 0, "brief": "PRIVATE-PROMPT"}
        self.assertEqual(operation(material), "abc")
        self.assertEqual(list(inspect.signature(operation).parameters), ["material", "model", "fail"])
        self.assertFalse(diagnostics.error_log_path().exists())
        with self.assertRaises(ValueError) as result:
            operation(material, fail=True)
        self.assertIs(result.exception, failure)
        event = self.events()[0]
        self.assertEqual(event["project_id"], "abc")
        self.assertEqual(event["segment_index"], 0)
        self.assertNotIn("PRIVATE-PROMPT", json.dumps(event))

    def test_logging_io_failure_does_not_replace_original_error(self):
        failure = ValueError("original failure")

        @diagnostics.logged("test")
        def operation():
            raise failure

        with patch.object(diagnostics, "error_log_path", side_effect=PermissionError("blocked")):
            with self.assertRaises(ValueError) as result:
                operation()
        self.assertIs(result.exception, failure)

    def test_rotation_download_order_and_no_open_windows_handles(self):
        with patch.object(diagnostics, "MAX_BYTES", 700):
            for index in range(12):
                diagnostics.record_error("test", ValueError(f"event-{index}:" + "x" * 120))
        path = diagnostics.error_log_path()
        self.assertTrue(Path(str(path) + ".1").exists())
        self.assertTrue(Path(str(path) + ".2").exists())
        self.assertFalse(Path(str(path) + ".3").exists())
        events = self.events()
        indices = [int(event["message"].split(":")[0].split("-")[1]) for event in events]
        self.assertEqual(indices, sorted(indices))
        self.assertEqual(indices[-1], 11)
        path.unlink()  # Persistent handlers would prevent this on Windows.

    def test_history_error_collects_only_diagnostic_fields(self):
        history = {"status": {"status_str": "error", "messages": [["execution_error", {
            "node_id": "243", "node_type": "SelfLiftAvatar", "exception_type": "RuntimeError",
            "exception_message": "shape mismatch", "traceback": ["sample.py line 42\n"],
            "current_inputs": {"audio": "PRIVATE-AUDIO", "prompt": "PRIVATE-PROMPT"},
            "current_outputs": ["PRIVATE-IMAGE"],
        }]]}}
        diagnostics.record_history_error(history, project_id="abc", segment_index=1, prompt_id="job1")
        event = self.events()[0]
        self.assertEqual(event["stage"], "segment_generation")
        self.assertEqual(event["node_type"], "SelfLiftAvatar")
        self.assertEqual(event["prompt_id"], "job1")
        self.assertIn("sample.py", event["traceback"])
        self.assertNotIn("PRIVATE-", json.dumps(event))

    def test_real_node_expansion_and_assembly_failures_are_logged_without_retry(self):
        nodes = importlib.import_module("h3lv_test.nodes")
        expansion = importlib.import_module("h3lv_test.expansion")
        controller = importlib.import_module("h3lv_test.controller")
        with patch.object(nodes, "audio_array", side_effect=ValueError("invalid audio")):
            with self.assertRaisesRegex(ValueError, "invalid audio"):
                nodes.Unified().process(None, "speaking", 15, 10, "", "", "auto")
        packet = {"project_id": "abc", "segment_index": 2, "paths": ["fixture.png"], "hashes": ["fixture"],
                  "material_note": "", "visual_type": "performance", "mode": "speaking",
                  "brief": "PRIVATE-PROMPT", "duration": 10, "generation_frames": 242,
                  "generation_seconds": 242 / 24, "cache_dir": self.directory.name + "/cache",
                  "audio_role": "vocal", "audio_section": "vocal", "audio_role_reason": "fixture"}
        with patch.object(expansion, "public_settings", return_value={"base_url": "https://example.test/v1"}), \
                patch.object(expansion, "call", side_effect=ValueError("API timeout")) as call:
            with self.assertRaisesRegex(ValueError, "API timeout"):
                expansion.PromptExpand().run(packet, "text", "demo-model", "fixture")
        self.assertEqual(call.call_count, 1)
        with patch.object(controller.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "FFmpeg"):
                controller.assemble(Path(self.directory.name) / "projects", "abc")
        events = self.events()
        self.assertTrue({"audio_analysis", "long_video_node", "prompt_expansion", "video_assembly"}
                        <= {event["stage"] for event in events})
        expansion_event = next(event for event in events if event["stage"] == "prompt_expansion")
        self.assertEqual(expansion_event["segment_number"], 3)
        self.assertEqual(expansion_event["model"], "demo-model")
        self.assertNotIn("PRIVATE-PROMPT", json.dumps(events))

    def test_handler_emit_failure_is_reported_as_not_recorded(self):
        with patch.object(diagnostics.ErrorFileHandler, "shouldRollover", side_effect=OSError("no space")):
            self.assertFalse(diagnostics.record_error("test", ValueError("original error")))

    def test_malformed_history_logging_is_best_effort(self):
        for history in ({"status": None}, {"status": {"messages": [None, ["bad"], [{}, {}]]}}, None):
            diagnostics.record_history_error(history)
        self.assertFalse(diagnostics.error_log_path().exists())


class DiagnosticRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_failure_is_logged_and_releases_running_task(self):
        from test_plugin import core, controller, sample_plan
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "projects"
            plan = sample_plan()
            core.write_plan(root, plan)
            controller.TASKS[plan["id"]] = object()
            paths = types.SimpleNamespace(get_output_directory=lambda: directory)
            with patch.dict(sys.modules, {"execution": types.SimpleNamespace(), "folder_paths": paths}), \
                    patch.object(diagnostics, "configured_key", return_value=""):
                await controller.execute_project(root, plan["id"], object())
                event = json.loads(diagnostics.download_log().decode("utf-8").splitlines()[0])
            self.assertEqual(event["stage"], "workflow_snapshot")
            self.assertEqual(event["exception_type"], "FileNotFoundError")
            self.assertEqual(core.read_plan(root, plan["id"])["run_status"], "failed")
            self.assertNotIn(plan["id"], controller.TASKS)

    async def test_controller_records_actual_failing_node_and_keeps_no_resubmit_policy(self):
        from test_plugin import core, controller, sample_plan
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "projects"
            plan = sample_plan()
            plan["approved"] = True
            plan["approved_fingerprint"] = core.fingerprint(plan)
            plan["segments"][0]["job"] = {"status": "queued", "prompt_id": "failed-job"}
            core.write_plan(root, plan)
            snapshot = {"loader_id": "1", "video_id": "7", "prompt": {
                "1": {"class_type": "H3LVUnified", "inputs": {}},
                "7": {"class_type": "VHS_VideoCombine", "inputs": {}}}}
            core.state_file(core.project_path(root, plan["id"]), "queue_snapshot.json").write_text(
                json.dumps(snapshot), encoding="utf-8")
            history = {"status": {"status_str": "error", "messages": [["execution_error", {
                "node_id": "243", "node_type": "SelfLiftAvatar", "exception_type": "RuntimeError",
                "exception_message": "injected sampler shape mismatch", "traceback": ["sampler.py:42\n"],
                "current_inputs": {"audio": "PRIVATE-AUDIO"}}]]}, "outputs": {}}
            submitted = []
            queue = types.SimpleNamespace(get_history=lambda **kwargs: {"failed-job": history},
                                          put=lambda item: submitted.append(item))
            paths = types.SimpleNamespace(get_output_directory=lambda: directory)
            with patch.dict(sys.modules, {"execution": types.SimpleNamespace(), "folder_paths": paths}), \
                    patch.object(diagnostics, "configured_key", return_value=""):
                await controller.execute_project(root, plan["id"], types.SimpleNamespace(prompt_queue=queue))
                events = [json.loads(line) for line in diagnostics.download_log().decode("utf-8").splitlines()]
            saved = core.read_plan(root, plan["id"])
            self.assertEqual(saved["run_status"], "failed")
            self.assertEqual(saved["segments"][0]["job"]["status"], "failed")
            self.assertEqual(submitted, [])
            self.assertNotIn(plan["id"], controller.TASKS)
            original = next(event for event in events if event.get("node_id") == "243")
            self.assertEqual(original["message"], "injected sampler shape mismatch")
            self.assertEqual(original["project_id"], plan["id"])
            self.assertEqual(original["segment_number"], 1)
            self.assertNotIn("PRIVATE-AUDIO", json.dumps(events))

    async def test_route_error_download_and_frontend_report(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        routes_module = importlib.import_module("h3lv_test.routes")
        with tempfile.TemporaryDirectory() as directory:
            paths = types.SimpleNamespace(get_output_directory=lambda: directory)
            server = types.SimpleNamespace(routes=web.RouteTableDef())
            server_module = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=server))
            with patch.dict(sys.modules, {"folder_paths": paths, "server": server_module}), \
                    patch.object(diagnostics, "configured_key", return_value="private-configured-key"):
                routes_module.register_routes()
                app = web.Application()
                app.add_routes(server.routes)
                async with TestClient(TestServer(app)) as client:
                    empty = await client.get("/h3lv/logs/errors")
                    self.assertEqual(empty.status, 200)
                    self.assertIn("尚未记录错误", await empty.text())
                    self.assertFalse(diagnostics.error_log_path().exists())
                    with patch("h3lv_test.expansion.call", side_effect=ValueError("API 调用失败")):
                        response = await client.get("/h3lv/expansion/models")
                    self.assertEqual(response.status, 400)
                    self.assertEqual((await response.json())["error"], "API 调用失败")
                    report = await client.post("/h3lv/logs/frontend", json={
                        "message": "private-configured-key 前端失败", "stack": "ui.js:12",
                        "source": "h3lv", "action": "顺序生成", "project_id": "abc",
                        "request_body": "PRIVATE-PROMPT"})
                    self.assertEqual(report.status, 200)
                    download = await client.get("/h3lv/logs/errors")
                    self.assertIn("attachment", download.headers["Content-Disposition"])
                    self.assertEqual(download.headers["Cache-Control"], "no-store")
                    text = await download.text()
                    self.assertNotIn("private-configured-key", text)
                    self.assertNotIn("PRIVATE-PROMPT", text)
                    events = [json.loads(line) for line in text.splitlines()]
                    self.assertEqual(events[0]["source"], "expansion_models")
                    self.assertEqual(events[-1]["stage"], "frontend")
                    too_large = await client.post("/h3lv/logs/frontend", json={"message": "x" * 40000})
                    self.assertEqual(too_large.status, 400)


if __name__ == "__main__":
    unittest.main()
