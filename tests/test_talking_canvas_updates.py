"""Visible local model/LoRA choices and the panel's served module dependencies."""
import copy
import importlib
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import torch
from test_talking_flat_local import expanded
from test_talking_flat_rh import node, project, flat, graph


def public_prompt():
    value=expanded()
    inverse={slot:i for i,slot in enumerate(node.TalkingAvatar.LEGACY_SLOTS)}
    for n in value.values():
        for key,item in n['inputs'].items():
            if isinstance(item,list) and len(item)==2 and item[0]=='5':n['inputs'][key]=['5',inverse[item[1]]]
    return value


class LocalCanvasUpdateTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.output=Path(temp.name)
        modules=patch.dict(sys.modules,{'folder_paths':types.SimpleNamespace(get_output_directory=lambda:temp.name),
            'comfy_execution.graph':types.SimpleNamespace(ExecutionBlocker=lambda msg:('blocked',msg))})
        modules.start();self.addCleanup(modules.stop)
        client=patch.object(node,'Client',side_effect=AssertionError('Local preparation cannot contact RH'))
        client.start();self.addCleanup(client.stop)
        self.args=dict(audio={'waveform':torch.zeros(1,1,400001),'sample_rate':24000},
            reference_image=torch.zeros(1,301,101,3),width=96,height=160,backend='本地',
            fixed_prompt=flat.DEFAULT_PROMPT,generation_strategy=flat.MODE,unique_id='5',prompt=public_prompt())
        self.pid=node.TalkingAvatar().prepare(**self.args)['result'][0]
        self.plan=project.read_plan(node.root(),self.pid)
        self.plan.update(approved=True,approved_fingerprint=project.fingerprint(self.plan))
        self.plan['segments'][0]['prompt_draft']={'text':'My speaking prompt','sha256':'fixture'}
        project.write_plan(node.root(),self.plan)

    def edited(self):
        value=copy.deepcopy(self.args['prompt'])
        lora=next(n for n in value.values() if n['class_type']=='LoraLoaderModelOnly')
        lora['inputs'].update(lora_name='my-other-turbo.safetensors',strength_model=.37)
        return value

    def test_lora_change_reuses_reviewed_cuts_without_reanalysis_or_rh(self):
        edited=self.edited()
        with patch.object(flat,'segment',side_effect=AssertionError('Must keep reviewed cuts')):
            result=node.TalkingAvatar().prepare(**{**self.args,'prompt':edited},project_id=self.pid)
        updated=project.read_plan(node.root(),result['result'][0])
        self.assertTrue(result['ui']['talking_rh_open_review'][0])
        self.assertEqual(updated['id'],self.pid)
        self.assertEqual(updated['segments_clock'],self.plan['segments_clock'])
        self.assertEqual(updated['segments'][0]['prompt_draft'],self.plan['segments'][0]['prompt_draft'])
        loras=[n['inputs'] for n in updated['config']['canvas']['prompt'].values() if n['class_type']=='LoraLoaderModelOnly']
        self.assertIn(('my-other-turbo.safetensors',.37),[(n['lora_name'],n['strength_model']) for n in loras])
        project.verify_assets(node.root(),updated)
        self.assertEqual(updated['approved_fingerprint'],project.fingerprint(updated))
        submitted=graph.build(updated,updated['segments'][0],{'attempt':'a'*12})
        actual=[n['inputs'] for n in submitted.values() if n['class_type']=='LoraLoaderModelOnly']
        self.assertIn(('my-other-turbo.safetensors',.37),[(n['lora_name'],n['strength_model']) for n in actual])

    def test_existing_results_fork_with_new_lora_and_keep_old_project_bytes(self):
        directory=project.project_path(node.root(),self.pid)
        self.plan['segments'][0]['job']={'status':'completed','video':'old.mp4'}
        project.write_plan(node.root(),self.plan)
        before=(directory/'state/segments.json').read_bytes()
        result=node.TalkingAvatar().prepare(**{**self.args,'prompt':self.edited()},project_id=self.pid)
        updated=project.read_plan(node.root(),result['result'][0])
        self.assertTrue(result['ui']['talking_rh_open_review'][0])
        self.assertNotEqual(updated['id'],self.pid)
        self.assertEqual(updated['parent_project'],self.pid)
        self.assertEqual(updated['segments_clock'],self.plan['segments_clock'])
        self.assertTrue(all('job' not in row for row in updated['segments']))
        self.assertEqual((directory/'state/segments.json').read_bytes(),before)
        project.verify_assets(node.root(),updated)

    def test_removing_acceleration_loras_is_preserved_in_actual_submission(self):
        edited=copy.deepcopy(self.args['prompt'])
        for key,n in list(edited.items()):
            if n['class_type']!='LoraLoaderModelOnly':continue
            for target in edited.values():
                for field,value in target['inputs'].items():
                    if value==[key,0]:target['inputs'][field]=copy.deepcopy(n['inputs']['model'])
            edited.pop(key)
        result=node.TalkingAvatar().prepare(**{**self.args,'prompt':edited},project_id=self.pid)
        updated=project.read_plan(node.root(),result['result'][0])
        submitted=graph.build(updated,updated['segments'][0],{'attempt':'a'*12})
        self.assertFalse(any(n['class_type']=='LoraLoaderModelOnly' for n in submitted.values()))
        self.assertEqual(updated['segments_clock'],self.plan['segments_clock'])

    def test_uncollected_task_blocks_configuration_change_without_mutation(self):
        self.plan['segments'][0]['job']={'status':'submitted','task_id':'123'}
        project.write_plan(node.root(),self.plan)
        path=project.project_path(node.root(),self.pid)/'state/segments.json';before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'尚未收集'):
            node.TalkingAvatar().prepare(**{**self.args,'prompt':self.edited()},project_id=self.pid)
        self.assertEqual(path.read_bytes(),before)

    def test_audio_change_still_requires_new_analysis_without_mutating_project(self):
        changed={**self.args,'audio':{'waveform':torch.ones(1,1,400001),'sample_rate':24000},'prompt':self.edited()}
        path=project.project_path(node.root(),self.pid)/'state/segments.json';before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'重新分析'):
            node.TalkingAvatar().prepare(**changed,project_id=self.pid)
        self.assertEqual(path.read_bytes(),before)


class PanelModuleRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_and_all_relative_imports_are_served_by_actual_http_routes(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer
        routes=importlib.import_module('h3talk_test.talking_rh.routes')
        host=types.SimpleNamespace(routes=web.RouteTableDef())
        with patch.dict(sys.modules,{'server':types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=host))}):
            routes.register()
        app=web.Application();app.add_routes(host.routes)
        async with TestClient(TestServer(app)) as client:
            pending=['talking_panel.js'];seen=set()
            while pending:
                name=pending.pop()
                if name in seen:continue
                seen.add(name)
                response=await client.get('/h3talk/assets/'+name)
                self.assertEqual(response.status,200,f'Missing panel module: {name}')
                source=await response.text()
                for relative in re.findall(r"from\s*['\"]\./([^'\"]+)",source):
                    pending.append(relative.split('?')[0])
            self.assertIn('progress.js',seen)
            self.assertEqual((await client.get('/h3talk/assets/nodes.py')).status,404)
