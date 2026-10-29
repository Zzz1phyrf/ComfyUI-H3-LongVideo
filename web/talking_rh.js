import {app} from "../../scripts/app.js";
import {api} from "../../scripts/api.js";
import {syncHiddenWidgetConnections} from "./widget_visibility.js";
import {talkingStatus} from './talking_status.js';
import {setTalkingStatus} from './talking_progress.js';
import {alignProjectNode} from './node_ui.js';
import {downloadErrorLog,reportUiError} from './diagnostics.js';

const projectId=node=>node.widgets?.find(w=>w.name==='project_id')?.value;
const validId=id=>/^[0-9a-f]{32}$/.test(id || '');
async function request(path,body) {
  const response=await api.fetchApi(path,body===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok) throw new Error(result.error || '请求失败，请检查后台日志。');
  return result;
}
function status(node,text) {
  setTalkingStatus(node,text);
}
function syncTalkingVideo(owner,plan) {
  const preview=plan.canvas_preview;
  if(projectId(owner)!==plan.id)return;
  owner.__talkingCanvasVideoId=plan.canvas_video_id;
  if(!preview?.filename)return;
  const videos=(app.graph?._nodes || []).filter(node=>node.comfyClass==='SaveVideo' &&
    (!plan.canvas_video_id || String(node.id)===String(plan.canvas_video_id)));
  if(videos.length!==1)return;
  const video=videos[0];
  const key=`${plan.id}/${preview.subfolder || ''}/${preview.filename}/${plan.canvas_preview_kind}`;
  if(video.__talkingPreview===key)return;
  video.title=plan.canvas_preview_kind==='final' ? '视频输出 · 完整成片' :
    `视频输出 · 第 ${Number(plan.canvas_preview_index)+1} 段原片`;
  api.dispatchCustomEvent('executed',{node:String(video.id),display_node:String(video.id),
    output:{images:[{filename:preview.filename,subfolder:preview.subfolder || '',type:preview.type || 'output'}],animated:[true]}});
  video.__talkingPreview=key;
}
async function refreshStatus(node) {
  const id=projectId(node);
  if(!validId(id) || node.__talkingPolling || node.__talkingPreparing) return;
  node.__talkingPolling=true;
  try {const plan=await request(`/h3talk/project/${id}`);if(projectId(node)===id){status(node,talkingStatus(plan));syncTalkingVideo(node,plan);}}
  catch(error){if(projectId(node)===id)status(node,'状态读取失败，请打开分段面板查看。');}
  finally{node.__talkingPolling=false;}
}
function button(node,label,action) {
  const widget=node.addWidget('button',label,null,async()=>{
    if(widget.disabled)return;
    widget.disabled=true;node.setDirtyCanvas?.(true,true);
    try{await action();}catch(error){status(node,error.message);void reportUiError(error,{source:'talking_node',action:label,node_id:String(node.id)});await showMessage({title:label+'失败',message:error.message,tone:'error'});}
    finally{widget.disabled=false;node.setDirtyCanvas?.(true,true);}
  });
  widget.serialize=false;return widget;
}
function showMessage(options) {
  if(app.h3lvMessageDialog)return app.h3lvMessageDialog(options);
  alert(options.message);
}
async function checkRH(node) {
  const value=name=>node.widgets.find(w=>w.name===name)?.value;
  status(node,'正在检查 RH 配置（不生成）…');
  const result=await request('/h3talk/rh-check',{workflow_id:value('workflow_id'),region:value('region'),instance_type:value('instance_type'),
    independent:node.comfyClass==='H3LVTalkingAvatar' || value('continuation_mode')==='独立窗口（自然动作）'});
  status(node,`RH配置检查通过 · ${result.workflow_id}`);
  await showMessage({title:'RH配置检查通过',message:`工作流 ${result.workflow_id} 可读取，Ref2VA、人声参考、音频零遮罩及采样链路检查通过。\n没有上传素材或提交生成。实际模型加载、额度及生成效果仍需运行验证。`});
}
async function analyze(node,{ask=true}={}) {
  if(node.__talkingPreparing)throw new Error('正在准备分段，请等待节点执行完成。');
  const id=projectId(node);
  if(validId(id)) {
    const plan=await request(`/h3talk/project/${id}`);
    if(plan.controller_active)throw new Error('当前项目仍在生成，请等待完成或暂停后再重新分析。');
  }
  if(ask && app.h3lvConfirmDialog && !await app.h3lvConfirmDialog({title:'重新分析并分段？',
    message:'将按节点当前设置创建一个新的分段项目；现有项目和已生成文件不会删除。',confirmText:'重新分析并分段'}))return false;
  const snapshot=await app.graphToPrompt();
  const target=snapshot.output?.[String(node.id)];
  if(!target)throw new Error('执行图中没有找到口播节点，请检查输入连线。');
  target.inputs.project_id='';
  node.__talkingPreparing=true;status(node,'正在准备音频与分段 · 旧项目和成片保留');
  try{await api.queuePrompt(0,snapshot,{partialExecutionTargets:[String(node.id)]});}
  catch(error){node.__talkingPreparing=false;throw error;}
}

function hideUnusedLegacyMask(node) {
  const index=node.inputs?.findIndex(input=>input.name==="person_mask");
  if (index>=0 && node.inputs[index].link==null) node.removeInput(index);
}

function resetPictureLabels(node) {
  if (node.comfyClass!=="H3LVTalkingAvatar") return;
  const backgroundInput=node.inputs?.findIndex(input=>input.name==="background_image");
  if (backgroundInput>=0) node.removeInput(backgroundInput);
  const backgroundOutput=node.outputs?.findIndex(output=>output.name==="background_image");
  if (backgroundOutput>=0) node.removeOutput(backgroundOutput);
  for (const input of node.inputs || []) {
    if (input.name==="reference_image") delete input.label;
  }
}

const publicWidgets=['backend','workflow_id','region','instance_type','project_id','fixed_prompt'];
const loadingWidgets=new Map();
function preparePublicCanvas(data) {
  loadingWidgets.clear();
  for(const owner of data.nodes || []) {
    if(owner.type!=='H3LVTalkingAvatar')continue;
    const saved=owner.widgets_values || [];
    const names=typeof saved[4]==='number' ? ['backend','workflow_id','region','instance_type',
      ...(typeof saved[5]==='number' ? ['megapixels'] : []),'seed','project_id','fixed_prompt'] : publicWidgets;
    const values=Object.fromEntries(names.map((name,index)=>[name,saved[index]]));
    loadingWidgets.set(String(owner.id),values);
    owner.widgets_values=publicWidgets.map(name=>values[name]);
    const output=owner.outputs?.findIndex(item=>item.name==='seed');
    const input=owner.inputs?.findIndex(item=>item.name==='seed');
    const removed=new Set();
    for(const link of data.links || []) {
      if(link[1]===owner.id && link[2]===output && output>=0) {
        removed.add(link[0]);
        const target=data.nodes.find(node=>node.id===link[3]);
        if(target?.type==='SamplerCustom' && Number.isSafeInteger(values.seed))target.widgets_values[1]=values.seed;
      }
      if(link[3]===owner.id && link[4]===input && input>=0)removed.add(link[0]);
    }
    if(output>=0)owner.outputs.splice(output,1);
    if(input>=0)owner.inputs.splice(input,1);
    data.links=(data.links || []).filter(link=>!removed.has(link[0]));
    for(const link of data.links) {
      if(output>=0 && link[1]===owner.id && link[2]>output)link[2]--;
      if(input>=0 && link[3]===owner.id && link[4]>input)link[4]--;
    }
    for(const node of data.nodes) {
      for(const item of node.inputs || [])if(removed.has(item.link))item.link=null;
      for(const item of node.outputs || [])if(item.links)item.links=item.links.filter(id=>!removed.has(id));
    }
    for(const key of ['widget_ue_connectable','input_ue_unconnectable'])
      if(owner.properties?.ue_properties?.[key])delete owner.properties.ue_properties[key].seed;
  }
}

const originalSizes=new WeakMap();
function updateMode(node) {
  const canvas=node.widgets?.find(w=>w.name==="execution_mode")?.value==="画布";
  const remote=node.widgets?.find(w=>w.name==="backend")?.value==="RunningHub";
  for (const widget of node.widgets || []) {
    if(widget.name==='project_id') {
      widget.hidden=true;widget.computeSize=()=>[0,-4];continue;
    }
    if (["first_seconds","segment_seconds","fixed_prompt"].includes(widget.name)) {
      if (!originalSizes.has(widget)) originalSizes.set(widget,widget.computeSize);
      const independent=node.comfyClass==="H3LVTalkingAvatar" || node.widgets.find(w=>w.name==="continuation_mode")?.value==="独立窗口（自然动作）";
      const connected=node.inputs?.some(input=>input.name===widget.name && input.link!=null);
      widget.hidden=widget.name==="fixed_prompt" ? (!independent || connected) : independent;
      widget.computeSize=widget.hidden ? (()=>[0,-4]) : originalSizes.get(widget);
      continue;
    }
    if (!["workflow_id","region","instance_type","backend","local_model","local_clip","local_video_vae","local_audio_vae","local_upscaler","local_lora","local_clip_read"].includes(widget.name)) continue;
    if (!originalSizes.has(widget)) originalSizes.set(widget,widget.computeSize);
    const localSetting=widget.name.startsWith("local_");
    const cloudSetting=["workflow_id","region","instance_type"].includes(widget.name);
    widget.hidden=canvas || (localSetting && remote) || (cloudSetting && !remote);
    widget.computeSize=widget.hidden ? (()=>[0,-4]) : originalSizes.get(widget);
  }
  syncHiddenWidgetConnections(node);
  alignProjectNode(node);
}

async function showPanel(node) {
  let pid = node.widgets?.find(w => w.name === "project_id")?.value;
  let projects=await request('/h3talk/projects');
  if(!validId(pid) && projects.length) {
    pid=projects[0].id;node.widgets.find(w=>w.name==='project_id').value=pid;
  }
  if(!validId(pid)){await analyze(node,{ask:false});return;}
  document.getElementById("h3talk-modal")?.remove();
  const overlay = document.createElement("div"); overlay.id = "h3talk-modal";
  overlay.className='h3lv-shade';
  const content = document.createElement("div");
  content.className='h3lv-panel h3talk-host-panel';
  Object.assign(content.style,{display:'flex',flexDirection:'column',overflow:'hidden'});
  const toolbar=document.createElement('div');Object.assign(toolbar.style,{display:'flex',gap:'12px',padding:'8px 16px',height:'48px',alignItems:'center'});
  const label=document.createElement('span');label.textContent='分析项目';
  const select=document.createElement('select');select.setAttribute('aria-label','分析项目');select.style.flex='1';
  function populateProjects() {
  select.replaceChildren();
  for(const item of projects) {
    const option=document.createElement('option');option.value=item.id;
    option.textContent=`${new Date(item.created*1000).toLocaleString()} · ${item.duration.toFixed(2)}秒 · ${item.count}段 · ${item.id.slice(0,8)}`;
    select.append(option);
  }
  select.value=projectId(node);
  }
  populateProjects();
  const manage=document.createElement('button');manage.textContent='管理项目';manage.className='h3lv-button project-manager';
  const close = document.createElement("button"); close.textContent = "关闭";close.className='h3lv-button h3talk-close';
  close.onclick = () => overlay.remove();
  const frame = document.createElement("iframe"); frame.src = `/h3talk/project/${pid}/panel?client_id=${encodeURIComponent(api.clientId || "")}`;
  frame.__h3talkAnalyze=()=>analyze(node);
  Object.assign(frame.style,{width:"100%",height:"calc(100% - 48px)",border:0,borderRadius:"12px"});
  select.onchange=()=>{
  if(frame.contentWindow?.h3talkHasUnsavedChanges?.()) {select.value=projectId(node);void showMessage({title:'尚有未保存的修改',message:'请先保存切点或提示词修改，再切换项目。'});return;}
    node.widgets.find(w=>w.name==='project_id').value=select.value;
    frame.src=`/h3talk/project/${select.value}/panel?client_id=${encodeURIComponent(api.clientId || '')}`;
    void refreshStatus(node);
  };
  manage.onclick=async()=>{
    if(frame.contentWindow?.h3talkHasUnsavedChanges?.()){await showMessage({title:'尚有未保存的修改',message:'请先保存当前切点或提示词修改，再管理项目。'});return;}
    manage.disabled=true;
    try {
      if(typeof app.h3lvProjectManager!=='function')throw new Error('项目管理尚未加载，请刷新页面。');
      const result=await app.h3lvProjectManager(projectId(node),'/h3talk');
      if(!result?.deleted?.length)return;
      if(result.deleted.includes(projectId(node))) {
        node.widgets.find(widget=>widget.name==='project_id').value='';
        status(node,'当前项目已删除 · 请重新分析并分段');
        for(const video of app.graph?._nodes || [])if(video.comfyClass==='SaveVideo' && String(video.id)===String(node.__talkingCanvasVideoId)) {
          delete video.__talkingPreview;
          api.dispatchCustomEvent('executed',{node:String(video.id),display_node:String(video.id),output:{images:[],animated:[]}});
        }
        overlay.remove();app.graph.setDirtyCanvas(true,true);return;
      }
      projects=await request('/h3talk/projects');populateProjects();
    } catch(error){await showMessage({title:'项目管理失败',message:error.message,tone:'error'});}finally{manage.disabled=false;}
  };
  toolbar.append(label,select,manage,close);content.append(toolbar,frame); overlay.append(content); document.body.append(overlay);
}

app.registerExtension({
  name:"H3LongVideo.TalkingRH",
  beforeConfigureGraph(data) {preparePublicCanvas(data);},
  loadedGraphNode(node) {
    if(node.comfyClass!=='H3LVTalkingAvatar')return;
    const values=loadingWidgets.get(String(node.id));
    if(values)for(const widget of node.widgets || [])if(publicWidgets.includes(widget.name))widget.value=values[widget.name];
    updateMode(node);
  },
  afterConfigureGraph() {
    for(const node of app.graph?._nodes || [])if(node.comfyClass==='H3LVTalkingAvatar')void refreshStatus(node);
  },
  setup() {
    if (app.__h3talkQueueGuardInstalled) return;
    app.__h3talkQueueGuardInstalled=true;
    const timer=setInterval(()=>{
      for(const node of app.graph?._nodes || []) if(['H3LVTalkingAvatar','H3LVTalkingRH'].includes(node.comfyClass))void refreshStatus(node);
    },2500);
    window.addEventListener('pagehide',()=>clearInterval(timer));
    for(const event of ['execution_error','execution_interrupted']) api.addEventListener(event,()=>{
      for(const node of app.graph?._nodes || []) if(node.__talkingPreparing){node.__talkingPreparing=false;status(node,'分段准备未完成，请查看ComfyUI错误信息。');}
    });
    const original=app.queuePrompt.bind(app);
    app.queuePrompt=async function(number,batchCount=1,options={}) {
      const nodes=app.graph?._nodes || [];
      const owners=nodes.filter(node=>["H3LVTalkingRH","H3LVTalkingAvatar"].includes(node.comfyClass));
      if (owners.length!==1 || nodes.some(node=>node.comfyClass==="H3LVUnified")) return original.apply(this,arguments);
      const owner=String(owners[0].id);
      const snapshot=await app.graphToPrompt();
      const prompt=snapshot.output || {};
      if (!prompt[owner]) throw new Error("没有在执行图中找到Talking Avatar，请检查节点与连线。");
      const requested=Array.isArray(options) ? options : (options?.queueNodeIds ?? options?.partialExecutionTargets);
      const targets=Array.isArray(requested) ? requested.map(item=>String(item?.nodeId ?? item)) : [];
      function ancestors(id,seen=new Set()) {
        id=String(id);
        if (seen.has(id) || !prompt[id]) return seen;
        seen.add(id);
        for (const value of Object.values(prompt[id].inputs || {})) {
          if (Array.isArray(value) && value.length===2 && Number.isInteger(value[1])) ancestors(value[0],seen);
        }
        return seen;
      }
      const sampling=new Set(['SamplerCustom','SelfLiftAvatarH3Sampler','KSampler','KSamplerAdvanced','SamplerCustomAdvanced']);
      if (targets.length && targets.every(id=>{
        const chain=ancestors(id);
        if (!chain.has(owner)) return true; // An unrelated explicitly selected branch.
        return [...chain].some(key=>prompt[key].class_type==='H3LVTalkingMerge') &&
          ![...chain].some(key=>sampling.has(prompt[key].class_type));
      })) return original.apply(this,arguments);
      // Keep the full graph available to PROMPT/canvas.capture, but execute only
      // project preparation. Actual generation is submitted by the controller.
      return api.queuePrompt(number,snapshot,{partialExecutionTargets:[owner]});
    };
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!["H3LVTalkingRH","H3LVTalkingAvatar"].includes(nodeData.name)) return;
    const original = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function() {
      original?.apply(this,arguments);
      hideUnusedLegacyMask(this);
      resetPictureLabels(this);
      const labels={backend:'生成后端',workflow_id:'RH 工作流 ID',region:'RH 区域',instance_type:'RH 实例'};
      for(const widget of this.widgets || [])if(labels[widget.name])widget.label=labels[widget.name];
      updateMode(this);
      for (const mode of this.widgets.filter(w=>["execution_mode","continuation_mode","backend"].includes(w.name))) {
        const callback=mode.callback;
        mode.callback=(...args)=>{callback?.apply(this,args);updateMode(this);app.graph.setDirtyCanvas(true,true);};
      }
      button(this,'检查 RH 配置（不生成）',()=>checkRH(this));
      button(this,'重新分析并分段',()=>analyze(this));
      button(this,'打开分段与生成控制',()=>showPanel(this));
      button(this,'下载错误日志',()=>downloadErrorLog());
      status(this,'尚未准备项目 · 请先重新分析并分段');
      alignProjectNode(this);
    };
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function(info) {
      configured?.apply(this,arguments);
      const values=info.widgets_values;
      if (nodeData.name==='H3LVTalkingAvatar' && typeof values?.[4]==='number') {
        const old=['backend','workflow_id','region','instance_type',
          ...(typeof values?.[5]==='number' ? ['megapixels'] : []),'seed','project_id','fixed_prompt'];
        for (const widget of this.widgets || []) {
          const index=old.indexOf(widget.name);
          if (index>=0 && index<values.length) widget.value=values[index];
        }
      }
      hideUnusedLegacyMask(this);
      resetPictureLabels(this);
      const mode=this.widgets.find(w=>w.name==="continuation_mode");
      const modeIndex=this.widgets.findIndex(w=>w.name==="continuation_mode");
      if (mode && !["独立窗口（自然动作）","旧版latent续接"].includes(info.widgets_values?.[modeIndex])) mode.value="旧版latent续接";
      // Missing serialized fields belong to the old managed node, not the new
      // expanded-canvas default. Never migrate an existing project implicitly.
      const execution=this.widgets.find(w=>w.name==="execution_mode");
      const executionIndex=this.widgets.findIndex(w=>w.name==="execution_mode");
      if (execution && !["内置","画布"].includes(info.widgets_values?.[executionIndex])) execution.value="内置";
      // The original candidate had eight widgets and only supported RH.
      // Preserve that backend when loading its canvas, without changing new nodes.
      if (nodeData.name==="H3LVTalkingRH" && info.widgets_values?.length===8) {
        const backend=this.widgets.find(w=>w.name==="backend");
        if (backend) backend.value="RunningHub";
      }
      updateMode(this);
      void refreshStatus(this);
    };
    const serialized=nodeType.prototype.onSerialize;
    nodeType.prototype.onSerialize=function(info) {
      serialized?.apply(this,arguments);
      if(nodeData.name==='H3LVTalkingAvatar')info.widgets_values=publicWidgets.map(name=>this.widgets.find(widget=>widget.name===name)?.value);
    };
    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function(message) {
      executed?.apply(this,arguments);
      const pid = message.talking_rh_project?.[0];
      if (!pid) return;
      this.__talkingPreparing=false;
      this.widgets.find(w => w.name === "project_id").value = pid;
      app.graph.setDirtyCanvas(true,true);
      void refreshStatus(this);
      if (message.talking_rh_open_review?.[0]!==false) showPanel(this).catch(error=>showMessage({title:'无法打开分段审核',message:error.message,tone:'error'}));
    };
  },
});
