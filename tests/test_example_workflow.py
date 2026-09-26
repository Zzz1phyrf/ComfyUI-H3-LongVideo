import json
from pathlib import Path
import unittest

from test_plugin import nodes


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "04_H3长视频_单节点一体化.json"


class ExampleWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        cls.graph = {node["id"]: node for node in cls.workflow["nodes"]}
        cls.links = {link[0]: link for link in cls.workflow["links"]}

    def test_all_serialized_links_match_node_slots(self):
        self.assertEqual(len(self.links), len(self.workflow["links"]))
        for link_id, source_id, output_index, target_id, input_index, data_type in self.workflow["links"]:
            with self.subTest(link=link_id):
                source = self.graph[source_id]
                target = self.graph[target_id]
                output = source["outputs"][output_index]
                input_ = target["inputs"][input_index]
                self.assertIn(link_id, output["links"])
                self.assertEqual(input_["link"], link_id)
                self.assertEqual(output["type"], data_type)
                self.assertIn(input_["type"], (data_type, "*"))

    def test_unified_outputs_match_current_node_and_example_wiring(self):
        unified = next(node for node in self.graph.values() if node["type"] == "H3LVUnified")
        self.assertEqual([item["name"] for item in unified["outputs"]], list(nodes.Unified.RETURN_NAMES))
        self.assertEqual([item["type"] for item in unified["outputs"]], list(nodes.Unified.RETURN_TYPES))
        self.assertEqual({
            name: (target["type"], target["inputs"][input_index]["name"])
            for name, target, input_index in self._unified_connections(unified)
        }, {
            "original_audio_padded": ("VHS_VideoCombine", "audio"),
            "vocals_padded": ("LTXVAudioVAEEncode", "audio"),
            "generation_frames": ("MiniMaxH3ReferenceToVideo", "length"),
            "filename_prefix": ("VHS_VideoCombine", "filename_prefix"),
            "segment_material": ("H3LVPromptExpand", "material"),
            "image_1": ("MiniMaxH3ReferenceToVideo", "ref_images.ref_image_0"),
            "image_2": ("MiniMaxH3ReferenceToVideo", "ref_images.ref_image_1"),
            "image_3": ("MiniMaxH3ReferenceToVideo", "ref_images.ref_image_2"),
            "image_4": ("MiniMaxH3ReferenceToVideo", "ref_images.ref_image_3"),
            "image_5": ("MiniMaxH3ReferenceToVideo", "ref_images.ref_image_4"),
            "image_6": ("MiniMaxH3ReferenceToVideo", "ref_images.ref_image_5"),
            "fps": ("VHS_VideoCombine", "frame_rate"),
        })

    def _unified_connections(self, unified):
        for output_index, output in enumerate(unified["outputs"]):
            for link_id in output["links"]:
                link = self.links[link_id]
                if link[3] == 136 and output["name"] == "vocals_padded":
                    continue  # The same output also feeds the H3 vocal-reference input.
                yield output["name"], self.graph[link[3]], link[4]

    def test_prompt_path_uses_bundled_expander(self):
        self.assertNotIn("PromptExpand", [node["type"] for node in self.graph.values()])
        expander = next(node for node in self.graph.values() if node["type"] == "H3LVPromptExpand")
        preview = next(node for node in self.graph.values() if node["type"] == "PreviewAny")
        h3 = next(node for node in self.graph.values() if node["type"] == "MiniMaxH3ReferenceToVideo")
        self.assertNotIn("widgets_values_named", expander)
        self.assertEqual(len(expander["widgets_values"]), 4)
        self.assertEqual(expander["inputs"][0]["name"], "material")
        self.assertEqual(self.links[expander["outputs"][0]["links"][0]][3], preview["id"])
        self.assertEqual(self.links[preview["outputs"][0]["links"][0]][3], h3["id"])
        prompt_link = preview["outputs"][0]["links"][0]
        self.assertEqual(h3["inputs"][self.links[prompt_link][4]]["name"], "prompt")


if __name__ == "__main__":
    unittest.main()
