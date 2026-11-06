import json
from pathlib import Path
import unittest


class PublicWorkflowTests(unittest.TestCase):
    def test_examples_are_complete_graphs_without_previous_user_bindings(self):
        root = Path(__file__).resolve().parents[1]
        paths = list((root / 'examples').glob('*.json'))
        self.assertEqual({p.name for p in paths}, {
            'MiniMaxH3MV数字人工作流 By 像素幻想Lab.json',
            'MiniMaxH3口播数字人工作流 By 像素幻想Lab.json',
        })
        for path in paths:
            with self.subTest(workflow=path.name):
                graph = json.loads(path.read_text(encoding='utf-8'))
                nodes = {n['id']: n for n in graph['nodes']}
                self.assertEqual(len(nodes), len(graph['nodes']))
                self.assertGreater(len(nodes), 20)
                links = {link[0]: link for link in graph['links']}
                self.assertEqual(len(links), len(graph['links']))
                for link_id, source, output, target, slot, kind in links.values():
                    self.assertIn(link_id, nodes[source]['outputs'][output]['links'])
                    self.assertEqual(nodes[target]['inputs'][slot]['link'], link_id)
                for node in nodes.values():
                    kind = node['type']
                    values = node.get('widgets_values', [])
                    named = node.get('widgets_values_named', {})
                    for key in ('h3lv_project', 'h3lv_last_project', 'h3lv_preview'):
                        self.assertFalse(node.get('properties', {}).get(key))
                    fields = {'H3LVUnified': [(7, 'project_id'), (10, 'rh_workflow_id')],
                              'H3LVTalkingAvatar': [(4, 'project_id'), (1, 'workflow_id')],
                              'LoadAudio': [(0, 'audio')], 'LoadImage': [(0, 'image')]}
                    for index, key in fields.get(kind, []):
                        self.assertEqual(values[index], '')
                        self.assertFalse(named.get(key))
                    if kind == 'SaveVideo':
                        self.assertEqual(len(node['outputs']), 1)
                    for slot, inp in enumerate(node.get('inputs', [])):
                        if inp.get('link') is not None:
                            self.assertEqual(links[inp['link']][3:5], [node['id'], slot])


if __name__ == '__main__':
    unittest.main()
