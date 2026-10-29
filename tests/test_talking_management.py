"""Project removal uses temporary fixture trees only, preserving default finals."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_talking_overlap import project
management=importlib.import_module('h3talk_test.talking_rh.management')


class TalkingManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'H3TalkingRH/projects';self.root.mkdir(parents=True)

    def fixture(self,pid='a'*32,status='completed'):
        directory=self.root/pid;(directory/'state').mkdir(parents=True)
        (directory/'takes').mkdir();(directory/'takes/raw.mp4').write_bytes(b'raw')
        final=self.root.parent/'final_videos'/f'{pid}_overlap_test.mp4';final.parent.mkdir(exist_ok=True);final.write_bytes(b'final')
        plan={'id':pid,'kind':'talking_rh','created':10,'duration':7.,'run_status':status,
              'segments':[{'index':0,'job':{'status':'completed'}}],'final_video':str(final)}
        project.write_plan(self.root,plan)
        return plan,directory,final

    def test_inventory_is_readonly_and_marks_current_storage_and_pending_tasks(self):
        plan,directory,final=self.fixture();state=directory/'state/segments.json';before=state.read_bytes()
        rows=management.inventory(self.root);self.assertEqual(len(rows),1)
        row=rows[0];self.assertEqual(row['mode'],'speaking');self.assertFalse(row['active'])
        self.assertGreater(row['project_bytes'],0);self.assertEqual(row['final_bytes'],5)
        self.assertEqual(before,state.read_bytes())
        plan['segments'][0]['job']={'status':'creating_unknown'};project.write_plan(self.root,plan)
        self.assertTrue(management.inventory(self.root)[0]['active'])

    def test_default_removal_preserves_final_and_other_project(self):
        _,directory,final=self.fixture();_,other,_=self.fixture('b'*32)
        result=management.remove(self.root,{'ids':['a'*32]})
        self.assertFalse(directory.exists());self.assertTrue(final.exists());self.assertTrue(other.exists())
        self.assertEqual(result['deleted'],['a'*32]);self.assertFalse(result['delete_final'])

    def test_explicit_final_removal_includes_earlier_overlap_versions(self):
        _,directory,final=self.fixture()
        older=final.with_name('a'*32+'_overlap_older.mp4');older.write_bytes(b'old')
        management.remove(self.root,{'ids':['a'*32],'delete_final':True})
        self.assertFalse(directory.exists());self.assertFalse(final.exists());self.assertFalse(older.exists())

    def test_active_or_unknown_tasks_block_the_whole_batch(self):
        plan,first,_=self.fixture();_,second,_=self.fixture('b'*32)
        with patch.object(management.runner,'busy',side_effect=lambda root,pid:pid=='b'*32):
            with self.assertRaisesRegex(ValueError,'仍在运行'):management.remove(self.root,{'ids':['a'*32,'b'*32]})
        self.assertTrue(first.exists());self.assertTrue(second.exists())
        plan['segments'][0]['job']={'status':'creating_unknown'};project.write_plan(self.root,plan)
        with self.assertRaisesRegex(ValueError,'未解决任务'):management.remove(self.root,{'ids':['a'*32]})
        self.assertTrue(first.exists())

    def test_invalid_targets_and_external_final_cannot_remove_files(self):
        plan,directory,final=self.fixture()
        for payload in ({'ids':[]},{'ids':['../outside']},{'ids':['a'*32],'delete_final':'yes'}):
            with self.assertRaises(ValueError):management.remove(self.root,payload)
        outside=Path(self.temp.name)/'outside.mp4';outside.write_bytes(b'keep')
        plan['final_video']=str(outside);project.write_plan(self.root,plan)
        with self.assertRaises(ValueError):management.remove(self.root,{'ids':['a'*32],'delete_final':True})
        self.assertTrue(directory.exists());self.assertTrue(final.exists());self.assertTrue(outside.exists())
