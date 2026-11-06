"""Local expanded FL2VA contract and legacy/RH separation regressions."""
import copy,json,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import torch
from PIL import Image
from test_talking_flat_rh import source,config,flat,node,project,graph
canvas=node.canvas

def expanded():
    value=source()
    value['5']={'class_type':'H3LVTalkingRH','inputs':{}}
    for n in value.values():
        for key,item in n['inputs'].items():
            if item==['9000',0]:n['inputs'][key]=['5',canvas.SLOT['condition_audio']]
            elif item==['9001',0]:n['inputs'][key]=['5',canvas.SLOT['reference_image']]
    for field,slot in [('width','width'),('height','height'),('length','generation_frames'),('prompt','fixed_prompt')]:
        value['268']['inputs'][field]=['5',canvas.SLOT[slot]]
    value['273']['inputs']['filename_prefix']=['5',canvas.SLOT['raw_prefix']]
    value.pop('9000');value.pop('9001')
    return value

class LocalFlatTests(unittest.TestCase):
    def test_visible_model_and_sampler_edits_preserved_with_project_bindings(self):
        src=expanded();src['270']['inputs']['cfg']=1.3
        selected=next(n for n in src.values() if n['class_type']=='UNETLoader')
        selected['inputs']['unet_name']='user-selected.safetensors'
        before=copy.deepcopy(src);saved=flat.capture_canvas(src,'5')
        cfg=config();cfg.pop('rh_sampling');cfg.update(backend='local',execution_mode='canvas',canvas=saved)
        p={'id':'a'*32,'config':cfg};row=flat.clock(200000,[])[0]
        queued=graph.build(p,row,{'attempt':'b'*12})
        self.assertEqual(queued['270']['inputs']['cfg'],1.3)
        self.assertEqual(queued['5']['class_type'],'H3LVTalkingSegmentState')
        self.assertEqual(queued['5']['inputs']['attempt'],'b'*12)
        self.assertIn('8.71-second',queued['268']['inputs']['prompt'])
        self.assertEqual(next(n['inputs']['unet_name'] for n in queued.values() if n['class_type']=='UNETLoader'),'user-selected.safetensors')
        self.assertEqual(canvas.output_ids(cfg),{'video':'273'})
        self.assertEqual(src,before)

    def test_wrong_anchor_audio_clock_or_decoder_rejected(self):
        for key,field,value in [('268','last_frame',['5',0]),('268','length',141),
                                ('26000','audio',['5',canvas.SLOT['reference_audio']]),
                                ('249','value',1),('122','vae',['10004',0])]:
            src=expanded();src[key]['inputs'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):flat.capture_canvas(src,'5')

    def test_local_prepare_normalizes_and_does_not_call_rh_or_generate(self):
        src=expanded()
        # Public node uses compact output slots; prepare maps these to persisted slots.
        inverse={slot:i for i,slot in enumerate(node.TalkingAvatar.LEGACY_SLOTS)}
        for n in src.values():
            for key,value in n['inputs'].items():
                if isinstance(value,list) and len(value)==2 and value[0]=='5':n['inputs'][key]=['5',inverse[value[1]]]
        blocked=types.SimpleNamespace(ExecutionBlocker=lambda message:('blocked',message))
        with tempfile.TemporaryDirectory() as temp, patch.dict('sys.modules',{
            'folder_paths':types.SimpleNamespace(get_output_directory=lambda:temp),'comfy_execution.graph':blocked}), patch.object(node,'Client') as client:
            args=dict(audio={'waveform':torch.zeros(1,1,214000),'sample_rate':24000},
                      reference_image=torch.zeros(1,301,101,3),width=96,height=160,
                      backend='本地',fixed_prompt=flat.DEFAULT_PROMPT,generation_strategy=flat.MODE,
                      unique_id='5',prompt=src)
            result=node.TalkingAvatar().prepare(**args);pid=result['result'][0]
            plan=project.read_plan(node.root(),pid);project.verify_assets(node.root(),plan)
            self.assertEqual(plan['config']['backend'],'local')
            self.assertEqual(plan['config']['seed'],1013)
            self.assertFalse(plan['config'].get('continuity'))
            self.assertTrue(all(r==('blocked',None) for r in result['result'][1:]))
            with Image.open(node.root()/pid/'refs/reference_0.png') as image:self.assertEqual(image.size,(96,160))
            resumed=node.TalkingAvatar().prepare(**args,project_id=pid)
            self.assertEqual(resumed['result'][0],pid)
            self.assertEqual(node.TalkingAvatar().prepare(**{**args,'generation_strategy':'原图首尾锚定（RH测试）'},project_id=pid)['result'][0],pid)
            self.assertEqual(project.read_plan(node.root(),pid),plan)
            client.assert_not_called()
            edited=copy.deepcopy(src);edited['270']['inputs']['cfg']=1.3
            with self.assertRaisesRegex(ValueError,'新建项目'):
                node.TalkingAvatar().prepare(**{**args,'prompt':edited},project_id=pid)

if __name__=='__main__':unittest.main()
