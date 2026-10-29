import copy
import math
import tempfile
import unittest
from unittest.mock import patch

import test_plugin as support


core, nodes = support.core, support.nodes


class RegenerationCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = self.temporary.name
        self.plan = support.sample_plan()
        self.plan['approved'] = True
        self.plan['approved_fingerprint'] = core.fingerprint(self.plan)
        for index, row in enumerate(self.plan['segments']):
            row['job'] = {'status': 'completed', 'video': f'accepted_{index}.mp4', 'prompt_id': f'old-{index}'}
        core.write_plan(self.root, self.plan)
        self.patch = patch.object(nodes, 'data_root', return_value=self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def keys(self, index):
        return tuple(cls.IS_CHANGED(project_id=self.plan['id'], segment_index=index) for cls in (nodes.Unified, nodes.LoadSegment))

    def test_repeated_regeneration_changes_only_selected_segment_keys(self):
        previous = self.keys(1)
        neighbours = self.keys(0), self.keys(2)
        for _ in range(2):
            plan = core.read_plan(self.root, self.plan['id'])
            core.request_regeneration(plan['segments'][1], 'explicit regeneration')
            core.write_plan(self.root, plan)
            current = self.keys(1)
            for before, after in zip(previous, current):
                self.assertNotEqual(before, after)
            self.assertEqual((self.keys(0), self.keys(2)), neighbours)
            previous = current

    def test_regeneration_persists_without_changing_approval_or_accepted_take(self):
        original = copy.deepcopy(self.plan)
        core.request_regeneration(self.plan['segments'][1], 'explicit regeneration')
        core.write_plan(self.root, self.plan)
        restored = core.read_plan(self.root, self.plan['id'])
        self.assertTrue(restored['segments'][1]['regeneration_nonce'])
        self.assertEqual(core.fingerprint(restored), original['approved_fingerprint'])
        self.assertEqual(core.segment_fingerprint(restored['segments'][1]), core.segment_fingerprint(original['segments'][1]))
        self.assertEqual(restored['segments'][1]['job'], original['segments'][1]['job'])

    def test_failed_segment_retries_get_distinct_keys(self):
        self.plan['segments'][1]['job']['status'] = 'failed'
        before = self.keys(1)
        core.request_regeneration(self.plan['segments'][1], 'retry failed segment')
        core.write_plan(self.root, self.plan)
        self.assertNotIn('job', self.plan['segments'][1])
        for a, b in zip(before, self.keys(1)):
            self.assertNotEqual(a, b)

    def test_unresolved_cloud_task_is_not_mutated(self):
        row = {'job': {'backend': 'runninghub', 'status': 'creating_unknown', 'task_id': ''}}
        before = copy.deepcopy(row)
        with self.assertRaisesRegex(ValueError, '未解决'):
            core.request_regeneration(row, 'explicit regeneration')
        self.assertEqual(row, before)

    def test_unapproved_project_still_blocks_native_cached_run(self):
        self.plan['approved'] = False
        core.write_plan(self.root, self.plan)
        self.assertTrue(math.isnan(nodes.Unified.IS_CHANGED(project_id=self.plan['id'], segment_index=1)))


if __name__ == '__main__':
    unittest.main()
