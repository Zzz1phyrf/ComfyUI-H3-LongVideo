"""Migrate the shipped public canvas to sampler-owned seed and one video output."""
import copy


def migrate(source):
    value=copy.deepcopy(source)
    for owner in value['nodes']:
        if owner['type']!='H3LVTalkingAvatar':continue
        widgets=owner.get('widgets_values',[])
        seed_slot=next((i for i,item in enumerate(owner.get('outputs',[])) if item['name']=='seed'),None)
        if seed_slot is None:continue
        seed_index=5 if isinstance(widgets[5],(int,float)) else 4
        seed=widgets.pop(seed_index)
        remove={node['id'] for node in value['nodes'] if node['type']=='H3LVTalkingMerge'}
        for link in value['links']:
            if link[1] in remove:
                if next(node for node in value['nodes'] if node['id']==link[3])['type']=='SaveVideo':remove.add(link[3])
        seed_input=next((i for i,item in enumerate(owner.get('inputs',[])) if item['name']=='seed'),None)
        if seed_input is not None:
            for link in value['links']:
                if link[3:5]==[owner['id'],seed_input]:
                    parent=next(node for node in value['nodes'] if node['id']==link[1])
                    if parent['type']=='PrimitiveNode':seed=parent['widgets_values'][0];remove.add(parent['id'])
        seed_links={link[0] for link in value['links'] if link[1:3]==[owner['id'],seed_slot]}
        for node in value['nodes']:
            if node['type']!='SamplerCustom':continue
            node['widgets_values'][1:3]=[seed,'fixed']
            inputs=node.get('inputs',[])
            for index in range(len(inputs)-1,-1,-1):
                if inputs[index].get('link') not in seed_links:continue
                inputs.pop(index)
                for link in value['links']:
                    if link[3]==node['id'] and link[4]>index:link[4]-=1
        owner['outputs'].pop(seed_slot)
        if seed_input is not None:owner['inputs'].pop(seed_input)
        value['links']=[link for link in value['links'] if link[1] not in remove and link[3] not in remove
            and link[0] not in seed_links and not (seed_input is not None and link[3:5]==[owner['id'],seed_input])]
        for link in value['links']:
            if link[1]==owner['id'] and link[2]>seed_slot:link[2]-=1
            if seed_input is not None and link[3]==owner['id'] and link[4]>seed_input:link[4]-=1
        value['nodes']=[node for node in value['nodes'] if node['id'] not in remove]
        live={link[0] for link in value['links']}
        for node in value['nodes']:
            for output in node.get('outputs',[]):output['links']=[lid for lid in output.get('links') or [] if lid in live]
            for item in node.get('inputs',[]):
                if item.get('link') not in live:item['link']=None
            if node['type']=='SaveVideo':node['title']='视频输出 · 分段原片 / 完整成片'
        for key in ('widget_ue_connectable','input_ue_unconnectable'):
            owner.get('properties',{}).get('ue_properties',{}).get(key,{}).pop('seed',None)
        value['groups']=[group for group in value.get('groups',[]) if not group.get('title','').startswith('07 ')]
    return value
