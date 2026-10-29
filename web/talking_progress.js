// Keep the legacy value as a compatibility sink for already-open ComfyUI pages.
// The visible DOM widget does not depend on disabled text rendering or canvas LOD.
export function setTalkingStatus(node,text,ownerDocument=document) {
  let legacy=node.widgets?.find(w=>w.name==='任务状态');
  if(!legacy)legacy=node.addWidget('text','任务状态','',()=>{},{serialize:false});
  legacy.hidden=true;legacy.disabled=true;legacy.serialize=false;legacy.computeSize=()=>[0,-4];
  legacy.value=text;
  let view=node.widgets.find(w=>w.type==='h3talk-progress');
  if(!view) {
    const element=ownerDocument.createElement('div');
    element.className='h3talk-workflow-progress';
    element.setAttribute('role','status');element.setAttribute('aria-live','polite');
    element.style.cssText='box-sizing:border-box;width:100%;height:100%;padding:7px 9px;border:1px solid #42617b;border-radius:6px;background:#142331;color:#e4eef8;font:12px/18px sans-serif;white-space:pre-line;overflow:hidden;pointer-events:auto';
    let last='';
    const paint=()=>{
      const value=String(legacy.value || '正在读取项目状态…');if(value===last)return;last=value;
      const parts=value.replace(/\s*·\s*任务\s+\S+/g,'').split(' · ');
      element.textContent=parts.slice(0,2).join(' · ')+(parts.length>2?'\n'+parts.slice(2).join(' · '):'');
      element.title=value;
    };
    view=node.addDOMWidget('项目进度','h3talk-progress',element,{serialize:false,hideOnZoom:false,margin:6,getMinHeight:()=>64,getMaxHeight:()=>64,
      getValue:()=>legacy.value,setValue(value){legacy.value=value;paint();}});
    view.serialize=false;view.computeSize=width=>[width,64];view.__h3talkProgressElement=element;view.__h3talkPaint=paint;
    // A cached main-page script can keep writing the legacy value after the panel closes.
    const scheduler=ownerDocument.defaultView || {setInterval,clearInterval};
    const timer=scheduler.setInterval(paint,500),removed=view.onRemove;
    view.onRemove=function(){scheduler.clearInterval(timer);removed?.apply(this,arguments);};
    const size=node.computeSize?.();if(size)node.setSize?.([node.size[0],Math.max(node.size[1],size[1])]);
  }
  if(view.options.getMinHeight?.()!==64) {
    Object.assign(view.options,{margin:6,getMinHeight:()=>64,getMaxHeight:()=>64});view.computeSize=width=>[width,64];
    const size=node.computeSize?.();if(size)node.setSize?.([node.size[0],Math.max(node.size[1],size[1])]);
  }
  view.__h3talkPaint();node.setDirtyCanvas?.(true,true);
  return view;
}

export function syncWorkflowStatus(hostWindow,pid,text) {
  const app=hostWindow.comfyAPI?.app?.app;
  for(const node of app?.graph?._nodes || []) {
    if(!['H3LVTalkingAvatar','H3LVTalkingRH'].includes(node.comfyClass))continue;
    if(node.widgets?.find(w=>w.name==='project_id')?.value===pid)setTalkingStatus(node,text,hostWindow.document);
  }
}
