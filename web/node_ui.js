// Shared canvas presentation; parameter values, ports and serialization stay owned by each node.
import {syncHiddenWidgetConnections} from './widget_visibility.js';
const actions=['运镜规则：查看与修改','RH API 设置','检查 RH 配置（不生成）',
  '重新分析并分段','打开分段与生成控制','下载错误日志'];
const sizes=new WeakMap(),callbacks=new WeakSet();

export function alignProjectNode(node) {
  if(!['H3LVUnified','H3LVTalkingAvatar','H3LVTalkingRH'].includes(node.comfyClass))return;
  node.color='#263b4b';node.bgcolor='#1c2b36';node.boxcolor='#74a9cf';
  const backend=node.widgets?.find(w=>w.name==='backend');
  if(backend && !callbacks.has(backend)) {
    callbacks.add(backend);const previous=backend.callback;
    backend.callback=function(...args){const result=previous?.apply(this,args);alignProjectNode(node);return result;};
  }
  if(node.comfyClass==='H3LVUnified')for(const widget of node.widgets || []) {
    if(!['rh_workflow_id','rh_region','rh_instance_type','workflow_id','region','instance_type'].includes(widget.name))continue;
    if(!sizes.has(widget))sizes.set(widget,widget.computeSize);
    widget.hidden=backend?.value!=='RunningHub';
    widget.computeSize=widget.hidden?()=>[0,-4]:sizes.get(widget);
  }
  const buttons=(node.widgets || []).filter(w=>w.type==='button' && actions.includes(w.name));
  buttons.sort((a,b)=>actions.indexOf(a.name)-actions.indexOf(b.name));
  const progress=(node.widgets || []).filter(w=>w.type==='h3talk-progress');
  const tail=new Set([...buttons,...progress]);
  if(node.widgets)node.widgets=[...node.widgets.filter(w=>!tail.has(w)),...buttons,...progress];
  syncHiddenWidgetConnections(node);
  const computed=node.computeSize?.();
  if(computed)node.setSize?.([Math.max(420,node.size?.[0] || 0,computed[0]),computed[1]]);
  node.setDirtyCanvas?.(true,true);
}
