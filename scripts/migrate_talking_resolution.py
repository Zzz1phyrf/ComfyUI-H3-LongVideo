"""Convert a saved public talking canvas to an external ResolutionSelector."""
import copy


def migrate(workflow):
    value=copy.deepcopy(workflow)
    for node in value['nodes'][:]:
        if node['type']!='H3LVTalkingAvatar' or any(i['name']=='width' for i in node.get('inputs',[])):continue
        old_inputs=node.get('inputs',[])
        old_slots={i:item['name'] for i,item in enumerate(old_inputs)}
        pixels=next((i for i in old_inputs if i['name']=='megapixels'),None)
        edge=next((link for link in value['links'] if pixels and link[0]==pixels.get('link')),None)
        source=next((n for n in value['nodes'] if edge and n['id']==edge[1]),None)
        widgets=node['widgets_values'];mp=widgets[4]
        if source and source['type']=='PrimitiveNode':mp=source['widgets_values'][0]
        reusable=source and source['type']=='PrimitiveNode' and sum(len(o.get('links') or []) for o in source['outputs'])==1
        if not reusable:
            key=max([value.get('last_node_id',0),*(n['id'] for n in value['nodes'])])+1
            source={'id':key,'pos':[node['pos'][0]-360,node['pos'][1]],'size':[320,150],'flags':{},'order':0,'mode':0}
            value['nodes'].append(source)
        source.update(type='ResolutionSelector',title='口播分辨率预设',inputs=[],
            outputs=[{'name':'width','type':'INT','links':[]},{'name':'height','type':'INT','links':[]}],
            widgets_values=['9:16 (Portrait Widescreen)',mp,32],properties={'Node name for S&R':'ResolutionSelector'})
        if edge:
            value['links'].remove(edge)
            for origin in value['nodes']:
                for output in origin.get('outputs',[]):
                    if edge[0] in (output.get('links') or []):output['links'].remove(edge[0])
        node['inputs']=[item for item in old_inputs if item['name'] in ('audio','reference_image')]+[
            {'name':'width','type':'INT','link':None},{'name':'height','type':'INT','link':None}]+[
            item for item in old_inputs if item['name'] not in ('audio','reference_image','megapixels')]
        slots={item['name']:i for i,item in enumerate(node['inputs'])}
        for link in value['links']:
            if link[3]==node['id']:link[4]=slots[old_slots[link[4]]]
        last=max([value.get('last_link_id',0),*(link[0] for link in value['links'])])
        for output,name in enumerate(('width','height')):
            last+=1;value['links'].append([last,source['id'],output,node['id'],slots[name],'INT'])
            source['outputs'][output]['links']=[last];node['inputs'][slots[name]]['link']=last
        node['widgets_values']=[*widgets[:4],*widgets[5:8]]
        value['last_node_id']=max(n['id'] for n in value['nodes']);value['last_link_id']=last
    return value
