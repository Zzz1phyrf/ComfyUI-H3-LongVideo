"""Formatting equivalence must preserve creative content and reject real gaps."""
import itertools
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_materials as fixtures

expansion=fixtures.expansion


class ExpansionFormatMatrixTests(unittest.TestCase):
    setUp=fixtures.MaterialTests.setUp

    def packet(self):
        return fixtures.materials.packet(self.plan,self.plan['segments'][0],self.directory)

    def test_heading_presentations_and_order_normalize_to_the_same_prompt(self):
        for style in ('spaces','hyphens','bullet','parentheses','standalone','reordered'):
            value=fixtures.MULTI_TEXT
            for index,name in enumerate(expansion.HEADINGS,1):
                header={'spaces':name.replace('_',' ').title()+':',
                        'hyphens':name.replace('_','-')+':',
                        'bullet':'- **'+name+':**',
                        'parentheses':f'({index}) {name}:',
                        'standalone':name,
                        'reordered':name+':'}[style]
                value=value.replace(name+':',header)
            if style=='reordered':
                parts=expansion.prompt_sections(value)
                value='\n'.join(name+':\n'+parts[name] for name in reversed(expansion.HEADINGS))
            with self.subTest(style=style):
                self.assertEqual(expansion.validate_result(value,self.packet()),fixtures.MULTI_TEXT)

    def test_reference_presentations_preserve_ids_in_all_sections(self):
        for label in ('<{kind} #{n}>','<{kind}_{n}>','{kind}{n}',
                      '[{kind} {n}]','＜{kind} {n}＞','&lt;{kind} {n}&gt;'):
            value=fixtures.MULTI_TEXT
            for kind,number in (('Picture',1),('Picture',2),('Audio',1),('Subject',1)):
                value=value.replace(f'<{kind} {number}>',label.format(kind=kind,n=number))
            with self.subTest(label=label):
                self.assertEqual(expansion.validate_result(value,self.packet()),fixtures.MULTI_TEXT)

    def test_format_cross_product_is_idempotent_for_one_to_six_pictures(self):
        for count,heading_style,token_style,kind in itertools.product(range(1,7),('plain','markdown','spaced'),('angle','square','escaped'),('performance','atmosphere','environment')):
            packet=self.packet();packet['paths']=['source']*count
            packet['visual_type']=kind
            source=' '.join(f'<Picture {i}> supplies reference {i}.' for i in range(1,count+1))
            value=fixtures.TEXT.replace('<Subject 1> from <Picture 1>.','<Subject 1> is the performer. '+source)
            if kind!='performance':value=value.replace('<Audio 1> is singing.','').replace('follows <Audio 1>','shows the scene')
            expected=expansion.validate_result(value,packet)
            for name in expansion.HEADINGS:
                label=name+':' if heading_style=='plain' else ('### **'+name+'**：' if heading_style=='markdown' else name.replace('_',' ').upper()+':')
                value=value.replace(name+':',label)
            if token_style=='square':value=value.replace('<','[').replace('>',']')
            elif token_style=='escaped':value=value.replace('<','&lt;').replace('>','&gt;')
            with self.subTest(count=count,heading=heading_style,token=token_style,kind=kind):
                result=expansion.validate_result(value,packet)
                self.assertEqual(result,expected)
                self.assertEqual(expansion.validate_result(result,packet),result)

    def test_explicit_json_sections_are_format_not_new_creative_content(self):
        fields=expansion.prompt_sections(fixtures.MULTI_TEXT)
        for value in (json.dumps(fields), '```json\n'+json.dumps(fields)+'\n```',
                      json.dumps({key:fields[key] for key in reversed(fields)})):
            with self.subTest(value=value[:30]):
                self.assertEqual(expansion.validate_result(value,self.packet()),fixtures.MULTI_TEXT)

    def test_json_duplicate_unknown_and_nontext_fields_do_not_silently_pass(self):
        fields=expansion.prompt_sections(fixtures.MULTI_TEXT)
        values=[json.dumps(fields)[:-1]+',"summary":"different"}',
                json.dumps({**fields,'unexpected':'new field'}),
                json.dumps({**fields,'summary':None})]
        for value in values:
            with self.subTest(value=value[:30]),self.assertRaises(ValueError):
                expansion.validate_result(value,self.packet())

    def test_empty_or_placeholder_visual_sections_are_not_filled_with_invented_prose(self):
        fields=expansion.prompt_sections(fixtures.MULTI_TEXT)
        for name,value in itertools.product(expansion.HEADINGS[:4],('', '...', 'N/A')):
            parts={**fields,name:('[Shot 1] '+value if name=='detailed_description' else value)}
            with self.subTest(name=name,value=value),self.assertRaises(ValueError):
                expansion.validate_result(expansion.join_sections(parts),self.packet())

    def test_malformed_reference_tokens_are_not_hidden_by_valid_sources(self):
        for tag in ('<Picture 0>','<Picture -1>','<Picture 1.5>','<Audio one>','<Subject 0>','<Picture 1','<Subject 1'):
            value=fixtures.MULTI_TEXT.replace('[Shot 1] Move.','[Shot 1] Move beside '+tag+'.')
            with self.subTest(tag=tag),self.assertRaises(ValueError):
                expansion.validate_result(value,self.packet())

    def test_unknown_video_and_undefined_subject_are_real_errors(self):
        for tag in ('<Video 1>','<Subject 9>'):
            value=fixtures.MULTI_TEXT.replace('[Shot 1] Move.','[Shot 1] Move beside '+tag+'.')
            with self.subTest(tag=tag),self.assertRaisesRegex(ValueError,'Video 1|Subject 9'):
                expansion.validate_result(value,self.packet())

    def test_subject_numbers_are_independent_of_picture_count(self):
        value=fixtures.MULTI_TEXT.replace('<Subject 1>','<Subject 8>')
        self.assertEqual(expansion.validate_result(value,self.packet()),value)

    def test_duplicate_presented_fields_are_still_rejected(self):
        value=fixtures.MULTI_TEXT.replace('summary:','summary:\nOriginal.\n- **Summary:**')
        with self.assertRaisesRegex(ValueError,'重复'):
            expansion.validate_result(value,self.packet())

    def test_cached_equivalent_format_never_makes_a_new_request(self):
        packet=self.packet()
        value=json.dumps(expansion.prompt_sections(fixtures.MULTI_TEXT))
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://test.invalid/v1'}):
            key=expansion.cache_key(packet,'vision','model','',0)
            directory=Path(packet['cache_dir']);directory.mkdir(parents=True)
            (directory/(key+'.invalid.txt')).write_text('\ufeff'+value,encoding='utf-8')
            with patch.object(expansion,'call',side_effect=AssertionError('No paid format retry')):
                self.assertEqual(expansion.expand(packet,'vision','model',''),fixtures.MULTI_TEXT)

    def test_nonfinal_provider_responses_never_become_success_cache(self):
        packet=self.packet()
        with patch.object(expansion,'public_settings',return_value={'base_url':'https://test.invalid/v1'}):
            for finish in ('length','content_filter','tool_calls','function_call'):
                response={'choices':[{'message':{'content':fixtures.MULTI_TEXT},'finish_reason':finish}]}
                with self.subTest(finish=finish),patch.object(expansion,'call',return_value=response) as request:
                    with self.assertRaises(ValueError):expansion.expand(packet,'vision','model','',revision=len(finish))
                    request.assert_called_once()
            response={'choices':[{'message':{'content':fixtures.MULTI_TEXT,'refusal':'blocked'},'finish_reason':'stop'}]}
            with patch.object(expansion,'call',return_value=response),self.assertRaises(ValueError):
                expansion.expand(packet,'vision','model','',revision=99)
        self.assertFalse(list(Path(packet['cache_dir']).glob('*.json')))

    def test_runtime_contract_lists_actual_assets_after_custom_style_rules(self):
        for kind in ('performance','atmosphere','environment'):
            packet=self.packet();packet['visual_type']=kind
            answer=fixtures.MULTI_TEXT
            if kind!='performance':answer=answer.replace('<Audio 1> is singing.','').replace('follows <Audio 1>','shows the scene')
            with patch.object(expansion,'public_settings',return_value={'base_url':'https://test.invalid/v1'}),patch.object(expansion,'call',return_value={'choices':[{'message':{'content':answer}}]}) as request:
                expansion.expand(packet,'vision','model','custom style',revision=len(kind))
            system=request.call_args.args[1]['messages'][0]['content']
            self.assertGreater(system.index(expansion.FORMAT_RULE),system.index('custom style'))
            assets=json.loads(system.rsplit('Available assets for this packet: ',1)[1])
            self.assertEqual(assets['pictures'],['<Picture 1>','<Picture 2>'])
            self.assertEqual(assets['audio'],'<Audio 1>' if kind=='performance' else None)
            self.assertIsNone(assets['video'])

    def test_cache_status_distinguishes_repairable_and_unusable_failures(self):
        packet=self.packet();key='fixture-key'
        directory=Path(packet['cache_dir']);directory.mkdir(parents=True)
        path=directory/(key+'.invalid.txt')
        path.write_text('not a complete prompt',encoding='utf-8')
        self.assertEqual(expansion.cache_source(packet,key,'vision'),'api')
        path.write_text(json.dumps(expansion.prompt_sections(fixtures.MULTI_TEXT)),encoding='utf-8')
        self.assertEqual(expansion.cache_source(packet,key,'vision'),'cache')

    def test_label_wrapped_whitespace_and_fullwidth_digits_preserve_numbers(self):
        value=fixtures.MULTI_TEXT.replace('<Picture 1>','< Picture\n１ >').replace('<Picture 2>','＜Picture ２＞')
        self.assertEqual(expansion.validate_result('\ufeff'+value.replace('\n','\r\n'),self.packet()),fixtures.MULTI_TEXT)

    def test_shot_heading_presentations_cannot_hide_a_second_shot(self):
        for label in ('Shot 1:', '(Shot 1):', '［Shot １］'):
            with self.subTest(label=label):
                self.assertEqual(expansion.validate_result(fixtures.MULTI_TEXT.replace('[Shot 1]',label),self.packet()),fixtures.MULTI_TEXT)
        for label in ('Shot 2:', '(Shot 2):', '［Shot ２］'):
            with self.subTest(label=label),self.assertRaisesRegex(ValueError,'Shot 1'):
                expansion.validate_result(fixtures.MULTI_TEXT.replace('[Shot 1]',label),self.packet())

    def test_invalid_later_segment_mentions_fail_before_starting_any_generation(self):
        import copy
        plan=copy.deepcopy(self.plan)
        plan['segments'][2].update(reference_source='custom',refs=self.names[:2],material_note='@图3 是环境')
        plan.update(approved=True,approved_fingerprint=fixtures.core.fingerprint(plan))
        root=self.directory/'projects';fixtures.core.write_plan(root,plan)
        directory=fixtures.core.project_path(root,plan['id'])
        import shutil
        shutil.copytree(self.directory/'refs',directory/'refs')
        before=fixtures.core.read_plan(root,plan['id'])
        with patch.object(expansion,'call',side_effect=AssertionError('Offline preflight only')):
            with self.assertRaisesRegex(ValueError,'第 3 段.*@图3'):
                fixtures.controller.start(root,plan['id'],{},object())
        self.assertEqual(fixtures.core.read_plan(root,plan['id']),before)
        self.assertFalse((directory/'state/queue_snapshot.json').exists())

    def test_preflight_preserves_completed_segments_and_resolved_shot_request(self):
        row=self.plan['segments'][0]
        row.update(job={'status':'completed'},material_note='@图9 不再使用的旧说明',reference_source='custom',refs=self.names[:2])
        fixtures.controller.validate_generation_materials(self.plan,self.directory)
        row.update(job={},shot_request='@图1 是人物',shot_intent='@图9 不再使用的旧要求')
        fixtures.controller.validate_generation_materials(self.plan,self.directory)

    def test_real_local_http_roundtrip_normalizes_json_and_stream_and_rejects_filtered_output(self):
        requests=[]
        answer=json.dumps(expansion.prompt_sections(fixtures.MULTI_TEXT))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(payload)
                finish='content_filter' if payload.get('metadata')=='filtered' else 'stop'
                if payload['stream']:
                    chunks=[{'choices':[{'index':0,'delta':{'content':part}}]} for part in (answer[:80],answer[80:])]
                    chunks.append({'choices':[{'index':0,'delta':{},'finish_reason':finish}]})
                    body=''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n'
                    kind='text/event-stream'
                else:
                    body=json.dumps({'choices':[{'message':{'content':answer},'finish_reason':finish}]})
                    kind='application/json'
                body=body.encode('utf-8');self.send_response(200)
                self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for mode in ('json','stream','filtered'):
                packet={**self.packet(),'cache_dir':str(self.directory/mode)}
                config={'base_url':f'http://127.0.0.1:{server.server_port}/v1','api_key':'fixture-only',
                        'response_mode':'json' if mode=='json' else 'stream',
                        'extra_body':{'metadata':'filtered'} if mode=='filtered' else {}}
                with patch.object(expansion,'settings',return_value=config):
                    if mode=='filtered':
                        with self.assertRaisesRegex(ValueError,'未正常完成'):
                            expansion.PromptExpand().run(packet,'vision','fixture-model','')
                        self.assertFalse(list(Path(packet['cache_dir']).glob('*.json')))
                    else:self.assertEqual(expansion.PromptExpand().run(packet,'vision','fixture-model','')['result'][0],fixtures.MULTI_TEXT)
            self.assertEqual(len(requests),3)
            self.assertTrue(all(sum(block['type']=='image_url' for block in request['messages'][1]['content'])==2 for request in requests))
        finally:
            server.shutdown();server.server_close();worker.join(timeout=2)


if __name__=='__main__':unittest.main()
