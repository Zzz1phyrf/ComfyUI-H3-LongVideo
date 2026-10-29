import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

package = types.ModuleType('h3lv_test')
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules.setdefault('h3lv_test', package)
errors = importlib.import_module('h3lv_test.workflow_errors')
controller = importlib.import_module('h3lv_test.controller')


class WorkflowErrorsTests(unittest.TestCase):
    def test_empty_top_error_keeps_node_and_input_evidence(self):
        valid = (False, {'type': 'prompt_outputs_failed_validation', 'details': ''}, [],
                 {'219': {'class_type': 'VHS_VideoCombine', 'errors': [
                     {'type': 'required_input_missing', 'details': 'images', 'extra_info': {'input_name': 'images'}},
                     {'type': 'required_input_missing', 'details': 'frame_rate', 'extra_info': {'input_name': 'frame_rate'}}]}})
        report = errors.failure_report(errors.validation_failure(valid, {}), 'workflow_validation', 0)
        self.assertEqual([x['field'] for x in report['issues']], ['images', 'frame_rate'])
        self.assertEqual(report['issues'][0]['node_id'], '219')
        self.assertIn('第 1 段', report['summary'])

    def test_missing_model_is_not_prompt_expansion(self):
        valid = (False, {}, [], {'127': {'class_type': 'UNETLoader', 'errors': [
            {'type': 'value_not_in_list', 'details': 'model unavailable', 'extra_info': {'input_name': 'unet_name'}}]}})
        report = errors.failure_report(errors.validation_failure(valid, {}), 'workflow_validation')
        self.assertIn('模型', report['issues'][0]['action'])
        self.assertEqual(report['category'], '工作流检查')

    def test_real_execution_error_keeps_node_but_not_inputs(self):
        history = {'status': {'status_str': 'error', 'messages': [['execution_error', {
            'node_id': '55', 'node_type': 'H3LVPromptExpand', 'exception_message': 'service timed out',
            'current_inputs': {'secret': 'never include'}}]]}}
        with self.assertRaises(errors.WorkflowFailure) as caught:
            controller.video_from_history(history, '1', '.', '.')
        report = errors.failure_report(caught.exception, 'segment_generation')
        self.assertEqual(report['issues'][0]['node_id'], '55')
        self.assertEqual(report['category'], '提示词扩写')
        self.assertNotIn('never include', str(report))

    def test_unknown_create_never_suggests_retry(self):
        plan = {'segments': [{'job': {'backend': 'runninghub', 'status': 'creating_unknown'}}]}
        report = errors.failure_report(RuntimeError('network'), 'rh_segment_generation', 1, plan)
        self.assertIn('绑定任务 ID', report['action'])

    def test_expansion_reference_validation_does_not_blame_network_or_credit(self):
        error = errors.ExecutionFailure('节点执行失败', [{'node_type':'H3LVPromptExpand',
            'reason':'多图扩写缺少参考图来源定义：<Picture 2>；尚未提交视频生成。'}])
        report = errors.failure_report(error, 'segment_generation', 5)
        self.assertEqual(report['category'], '提示词扩写')
        self.assertIn('参考图', report['action'])
        self.assertNotIn('网络', report['action'])
        self.assertNotIn('额度', report['action'])

    def test_download_preserves_same_task(self):
        plan = {'segments': [{'job': {'backend': 'runninghub', 'status': 'submitted', 'task_id': '123'}}]}
        report = errors.failure_report(RuntimeError('下载失败'), 'rh_segment_generation', 1, plan)
        self.assertEqual(report['category'], '结果下载')
        self.assertEqual(report['task_ids'], ['123'])
        self.assertIn('同一个任务', report['action'])

    def test_merge_failure_keeps_results(self):
        report = errors.failure_report(OSError('disk full'), 'video_assembly', plan={
            'segments': [{'job': {'status': 'completed'}}, {'replacement_previous_job': {'status': 'completed'}}]})
        self.assertIn('仅重新合成', report['action'])
        self.assertIn('2 段', report['preserved'])

    def test_failed_replacement_reports_failed_task_not_restored_old_take(self):
        plan = {'segments': [{'job': {'status': 'completed', 'task_id': 'old'},
                             'needs_regeneration': True, 'failed_attempts': [{'task_id': 'failed-new'}]}]}
        report = errors.failure_report(RuntimeError('RH任务失败'), 'rh_segment_generation', 0, plan)
        self.assertEqual(report['task_ids'], ['failed-new'])
        self.assertIn('保留 1 段', report['preserved'])

    def test_no_node_details_does_not_invent_cause(self):
        report = errors.failure_report(errors.validation_failure((False, {}, [], {}), {}))
        self.assertIn('未返回', report['issues'][0]['reason'])

    def test_secrets_and_urls_are_redacted(self):
        with patch.dict('os.environ', {'RUNNINGHUB_API_KEY': 'private-key-value'}):
            report = errors.failure_report(RuntimeError('private-key-value https://host/x?token=secret'))
        self.assertNotIn('private-key-value', str(report))
        self.assertNotIn('https://host', str(report))


if __name__ == '__main__': unittest.main()
