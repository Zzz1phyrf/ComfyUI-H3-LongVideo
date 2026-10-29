import {createTimeline} from "./timeline.js?v=segments-20261027b";
import {talkingStatus} from './talking_status.js?v=segments-20261027';
import {syncWorkflowStatus} from './talking_progress.js?v=progress-20261028c';

if (document.getElementById("timeline") && location.pathname.includes("/h3talk/project/")) {
  const pid = location.pathname.match(/project\/([0-9a-f]{32})\//)?.[1];
  const base = `/h3talk/project/${pid}`;
  const $ = id => document.getElementById(id);
  let plan, rows = [], selected = 0, analysis, dirty = false, pending = false, timeline, actionError = "";
  let promptVersions, promptDirty=false, promptSignature='', promptLoading=false;
  const promptEdits=new Map();
  let rowsSignature='';
  window.h3talkHasUnsavedChanges=()=>dirty || promptDirty;
  function syncHost() {
    if(window.parent===window){document.body.classList.add('h3talk-standalone');return;}
    const frame=window.frameElement,shade=frame?.closest('#h3talk-modal');
    if(!shade)return;
    const host=frame.parentElement,toolbar=frame.previousElementSibling;
    if(!toolbar)return;
    const doc=window.parent.document;
    if(!doc.querySelector('style[data-h3talk-layout]')) {
      const style=doc.createElement('style');style.dataset.h3talkLayout='1';
      style.textContent='#h3talk-modal .h3talk-host-panel{display:flex;flex-direction:column;overflow:hidden}#h3talk-modal .h3talk-host-header{flex:none;padding-bottom:0;border-bottom:0}#h3talk-modal .h3talk-host-header select{min-width:0;width:min(660px,66vw);font:inherit}#h3talk-modal .h3talk-host-frame{display:block;flex:1;min-height:0;width:100%;border:0}';doc.head.append(style);
    }
    shade.removeAttribute('style');shade.className='h3lv-shade';
    host.removeAttribute('style');host.className='h3lv-panel h3talk-host-panel';
    toolbar.removeAttribute('style');toolbar.className='h3lv-header h3talk-host-header';
    const select=toolbar.querySelector('select'),label=toolbar.querySelector('span');
    const close=toolbar.querySelector('.h3talk-close, .h3lv-close') || [...toolbar.querySelectorAll('button')].find(button=>button.textContent==='关闭');
    const manage=toolbar.querySelector('.project-manager');
    if(select && close && !toolbar.querySelector('.h3lv-title-row')) {
      const title=doc.createElement('div');title.className='h3lv-title-row';
      const heading=doc.createElement('h2');heading.textContent='MiniMax H3 · Talking Avatar';
      close.className='h3lv-button h3lv-close h3talk-close';title.append(heading,close);
      const project=doc.createElement('div');project.className='h3lv-project-row';
      select.removeAttribute('style');project.append(label,select);if(manage)project.append(manage);toolbar.replaceChildren(title,project);
    }
    if(close && select) {
      close.__h3talkOriginalClose ??= close.onclick;select.__h3talkOriginalChange ??= select.onchange;
      close.onclick=()=>{if(window.h3talkHasUnsavedChanges()){fail(new Error('请先保存或撤销切点、提示词修改，再关闭面板。'));return;}close.__h3talkOriginalClose?.();};
      select.onchange=()=>{if(window.h3talkHasUnsavedChanges()){select.value=pid;fail(new Error('请先保存或撤销修改，再切换项目。'));return;}select.__h3talkOriginalChange?.();};
    }
    frame.removeAttribute('style');frame.className='h3talk-host-frame';
    document.body.classList.add('h3talk-framed');
  }
  async function request(action, body) {
    const response = await fetch(base+(action ? "/"+action : ""),body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const value = await response.json(); if (!response.ok) throw new Error(value.error || "请求失败"); return value;
  }
  function fail(error) { actionError = error.message || String(error); $("error").textContent = actionError; }

  function independent() {return plan?.config.talking_profile==='ref2va-overlap-v1';}
  function element(tag,text,parent,className='') {
    const node=document.createElement(tag);if(text!==undefined)node.textContent=text;
    if(className)node.className=className;parent?.append(node);return node;
  }
  function versionLabel(value) {
    if(!value)return '尚未生成';
    const labels={verified:'已核对实际生成记录',actual:'实际生成记录',submitted:'已提交，实际结果未核对',mismatch:'提交与实际文本不一致',unavailable:'缺少实际文本记录'};
    return labels[value.state] || '缺少实际文本记录';
  }
  function promptKey(value) {return JSON.stringify([value.editing_fingerprint,value.segments.map(row=>[row.job?.directory,row.job?.task_id,row.job?.status,row.prompt_draft?.sha256])]);}
  function draftEnds() {return rows.slice(0,-1).map(row=>Number(row.end.value));}
  function validCutDraft() {
    let start=0;
    return rows.every(row=>{const end=Number(row.end.value),length=end-start;start=end;return row.end.value!=='' && Number.isFinite(end) && length>0 && length<=15 && (length>=4 || rows.length===1 && plan.duration<4);});
  }
  function savedEnds() {return [...(plan.manual_cuts || plan.overlap_cuts?.slice(1,-1) || plan.segments.slice(0,-1).map(r=>r.end_sample/1000)).map(f=>f/24),plan.duration];}
  function cutBlocked() {return pending || plan.controller_active || Boolean(plan.regeneration) || promptDirty;}
  function mark() {dirty=true;apply(plan);}
  function refreshTimes() {
    rows.forEach((row,i)=>{
      const start=i?Number(rows[i-1].end.value):0,end=Number(row.end.value);
      row.time.textContent=`${start.toFixed(3)}—${end.toFixed(3)}s`;row.duration.textContent=`${(end-start).toFixed(3)}s`;
      row.metrics.textContent=`时长 ${(end-start).toFixed(3)} 秒`;
      const url=base+`/audio-preview?start=${start}&end=${Math.min(end,plan.duration)}`;
      if(row.clipAudio.getAttribute('src')!==url)row.clipAudio.src=url;
    });
    const start=selected?Number(rows[selected-1].end.value):0,end=Number(rows[selected]?.end.value || 0);
    $('selected-audio-label').textContent=`当前试听：第 ${selected+1} 段 · ${start.toFixed(3)}—${end.toFixed(3)}s`;
    const source=rows[selected]?.clipAudio.getAttribute('src');
    if(source && $('audio').getAttribute('src')!==source)$('audio').src=source;
  }
  function selectedWindow(index) {
    selected=index;
    rows.forEach((row,i)=>{row.card.open=i===index;if(i!==index){row.video?.pause();row.clipAudio?.pause();}});
    refreshTimes();timeline?.drawTimeline();
  }
  function editPrompt(index,text) {
    const row=rows[index],prior=promptEdits.get(index);
    promptEdits.set(index,{text,expected:prior?.expected ?? plan.editing_fingerprint});promptDirty=true;
    row.promptState.textContent='有未保存修改';apply(plan);
  }
  function showPrompts() {
    for(const [index,row] of rows.entries()) {
      const value=promptVersions?.windows[row.source],draft=promptEdits.get(index);
      row.editor.value=draft?.text ?? value?.next_prompt.text ?? plan.config.fixed_prompt ?? '';
      row.promptState.textContent=draft?'有未保存修改':value?.draft?'已保存，下次生成本段时使用':'修改后保存，当前视频仍保留';
      row.version.textContent='查看当前与历史提示词 · '+versionLabel(value?.current);
      row.actual.value=value?.current?.text ?? value?.current?.reason ?? '本段尚无实际生成文本。';
      row.actualLabel.textContent='当前视频使用的提示词'+(value?.current?.task_id?` · 任务 ${value.current.task_id}`:'');
      row.history.replaceChildren();
      for(const old of value?.history || []) {
        const detail=element('details',undefined,row.history);element('summary',`任务 ${old.task_id || '未知'} · ${versionLabel(old)}`,detail);
        const text=element('textarea',undefined,detail,'h3lv-rule-editor');text.readOnly=true;text.value=old.text || old.reason || '缺少文本记录';
      }
      row.historyBlock.hidden=!value?.history.length;
    }
  }
  async function loadPrompts() {
    promptLoading=true;apply(plan);
    try {promptVersions=await request('prompts');promptSignature=promptKey(plan);showPrompts();}
    catch(error){fail(error);}finally{promptLoading=false;apply(plan);}
  }
  async function savePrompt(index) {
    const draft=promptEdits.get(index);if(!draft || rows[index].savePrompt.disabled)return;
    await perform('prompt',{index,text:draft.text,expected_sha256:promptVersions.windows[index].draft?.sha256 ?? null,expected_fingerprint:draft.expected});
  }
  function resetPrompt(index) {promptEdits.delete(index);promptDirty=promptEdits.size>0;actionError='';showPrompts();apply(plan);}
  function changeLayout(ends,sources) {
    if(cutBlocked()){fail(new Error('请先保存提示词修改，或等待当前操作完成，再调整切点。'));return;}
    dirty=true;renderRows(ends,sources);refreshTimes();apply(plan);
  }
  function splitAt(time) {
    const index=rows.findIndex(row=>time<Number(row.end.value));if(index<0)return;
    const start=index?Number(rows[index-1].end.value):0,end=Number(rows[index].end.value);
    if(time-start<4 || end-time<4 || time-start>15 || end-time>15)throw new Error('新增切点后两段都须为4–15秒。');
    const ends=rows.map(r=>Number(r.end.value)),sources=rows.map(r=>r.source);
    ends.splice(index,0,time);sources.splice(index,0,sources[index]);selected=index;changeLayout(ends,sources);
  }
  function removeCut(index) {
    const ends=rows.map(r=>Number(r.end.value)),sources=rows.map(r=>r.source);
    const duration=ends[index+1]-(index?ends[index-1]:0);
    if(duration<4 || duration>15)throw new Error('删除切点后的合并段须为4–15秒。');
    ends.splice(index,1);sources.splice(index+1,1);selected=index;changeLayout(ends,sources);
  }
  function moveCut(index,time) {
    if(cutBlocked())return;
    const previous=index?Number(rows[index-1].end.value):0,next=Number(rows[index+1].end.value);
    const low=Math.max(previous+4,next-15),high=Math.min(previous+15,next-4);
    if(low>high)return;
    rows[index].end.value=Math.max(low,Math.min(high,time)).toFixed(6);refreshTimes();mark();
  }
  function renderRows(ends,sources=ends.map((_,i)=>i)) {
    const signature=JSON.stringify([ends,sources,plan.preview_intervals,plan.segments.map(row=>row.job)]);
    if(rows.length && signature===rowsSignature)return;
    rowsSignature=signature;for(const row of rows){row.video?.pause();row.clipAudio?.pause();}
    $('cuts').replaceChildren();rows=[];
    ends.forEach((end,index)=>{
      const source=sources[index],segment=plan.segments[source],job=segment?.job;
      const card=element('details',undefined,$('cuts'),'h3lv-card h3talk-segment');card.open=index===selected;
      const heading=element('summary',undefined,card),main=element('span',undefined,heading,'h3lv-summary-main');
      element('strong',`第 ${index+1} 段`,main);const time=element('span','',main,'h3lv-time'),duration=element('span','',main,'h3lv-duration');
      const label=dirty?'分段尚未保存':({completed:'已完成',failed:'生成失败',rejected:'已拒绝',submitted:'正在生成',creating_unknown:'任务待核实'})[job?.status] || '待生成';
      element('span',label,heading,'h3lv-chip '+(job?.status==='failed'?'risk':'neutral'));
      const content=element('div',undefined,card,'h3lv-card-body h3talk-segment-body');
      const inner=element('div',undefined,content,'h3lv-segment-editor');
      const top=element('div',undefined,inner,'h3talk-segment-top');
      const metrics=element('span','',top,'h3lv-notice');
      const endLabel=element('label','结束时间（秒）',top,'h3talk-end-label');
      const input=element('input',undefined,endLabel);input.type='number';input.step='any';input.value=end.toFixed(6);input.setAttribute('aria-label',`第${index+1}段结束时间（秒）`);
      input.oninput=()=>{dirty=true;if(validCutDraft())refreshTimes();apply(plan);};
      input.onchange=()=>moveCut(index,Number(input.value));
      element('label','本段原声试听',inner,'h3lv-audio-label');
      const clipAudio=element('audio',undefined,inner);clipAudio.controls=true;clipAudio.preload=card.open?'metadata':'none';
      const promptHeader=element('div',undefined,inner,'h3talk-prompt-heading');element('strong','本段提示词',promptHeader);
      const promptState=element('span','正在读取…',promptHeader,'h3lv-notice');
      const editor=element('textarea',undefined,inner,'h3lv-rule-editor h3talk-segment-prompt');editor.maxLength=32000;editor.spellcheck=false;editor.setAttribute('aria-label',`第${index+1}段提示词`);editor.oninput=()=>editPrompt(index,editor.value);
      const actions=element('div',undefined,inner,'h3lv-actions');
      const save=element('button','保存本段提示词',actions,'h3lv-button primary');save.onclick=()=>savePrompt(index);
      const reset=element('button','撤销修改',actions,'h3lv-button');reset.onclick=()=>resetPrompt(index);
      const versions=element('details',undefined,inner,'h3talk-versions');
      const version=element('summary','当前视频与历史提示词',versions);
      const actualBlock=element('details',undefined,versions),actualLabel=element('summary','当前视频使用的提示词',actualBlock);
      const actual=element('textarea',undefined,actualBlock,'h3lv-rule-editor');actual.readOnly=true;
      const historyBlock=element('details',undefined,versions);element('summary','历史生成版本',historyBlock);const history=element('div',undefined,historyBlock);
      const result=element('div',undefined,content,'h3lv-segment-result');let video,caption,mode,regenerate,retry;
      if(job?.status==='completed') {
        const selector=element('label','预览内容',result,'h3talk-preview-select');mode=element('select',undefined,selector);mode.setAttribute('aria-label',`第${index+1}段预览内容`);
        for(const [value,text] of [['used','成片中使用的画面'],['raw','完整生成视频（含重叠部分）']]){const option=element('option',text,mode);option.value=value;}
        const figure=element('figure',undefined,result,'h3lv-segment-preview');caption=element('figcaption','',figure);video=element('video',undefined,figure);video.controls=true;video.playsInline=true;video.preload='metadata';
        const used=plan.preview_intervals?.[source];
        function showVideo() {
          const raw=mode.value==='raw';
          const url=base+`/segment/${source}/preview?`+(raw?'kind=raw&':'')+'take='+encodeURIComponent(JSON.stringify([job.task_id,job.video_sha256,used]));
          caption.textContent=raw?'完整生成视频'+(segment?.generation_frames?` · ${(segment.generation_frames/24).toFixed(3)}秒`:''):'成片画面'+(used?` · ${(used[0]/24).toFixed(3)}—${(used[1]/24).toFixed(3)}秒`:'');
          if(video.getAttribute('src')!==url)video.src=url;
        }
        mode.value=independent() && !used?'raw':'used';mode.children[0].disabled=independent() && !used;mode.onchange=showVideo;
        regenerate=element('button',independent()?'将本段加入重做':'将本段及后续加入重做',result,'h3lv-button segment-run');regenerate.onclick=()=>perform('regenerate',{index});
        element('p','加入后，点击顶部“开始 / 继续生成”执行。当前结果保留。',result,'h3lv-help h3talk-redo-help');
        card.ontoggle=()=>{if(!card.open)return;selectedWindow(index);clipAudio.preload='metadata';if(!video.getAttribute('src'))showVideo();};
        if(card.open)showVideo();
      } else {
        element('div','本段生成后在这里预览',result,'h3talk-empty-preview');
        card.ontoggle=()=>{if(card.open){selectedWindow(index);clipAudio.preload='metadata';}};
      }
      if(['failed','rejected'].includes(job?.status)){retry=element('button','重试本段',result,'h3lv-button segment-run');retry.onclick=()=>perform('retry',{index});}
      rows.push({source,end:input,card,time,duration,metrics,clipAudio,editor,promptState,savePrompt:save,resetPrompt:reset,version,actual,actualLabel,history,historyBlock,video,caption,mode,regenerate,retry});
    });
    selected=Math.min(selected,rows.length-1);refreshTimes();showPrompts();
  }
  function apply(value,force=false) {
    plan=value;const active=plan.controller_active,local=plan.config.backend==='local',regenerating=Boolean(plan.regeneration);
    if(force){dirty=false;renderRows(savedEnds());}
    else if(!dirty)renderRows(savedEnds());
    $('state').textContent=talkingStatus(plan)+(plan.approved?' · 分段已确认':' · 分段待确认');
    if(typeof window!=='undefined' && window.parent!==window)syncWorkflowStatus(window.parent,plan.id,talkingStatus(plan));
    $('overview-meta').textContent=`${plan.duration.toFixed(3)}秒 · ${plan.segments.length}段 · ${plan.resolution_summary || (plan.config.megapixels+' MP')} · ${local?'本地生成':'RunningHub / '+plan.config.instance_type} · 音频：${plan.config.audio_processing || '旧项目沿用原处理'}`;
    $('error').textContent=actionError || plan.error || (!plan.credential_configured?'请在节点的RH API设置中保存凭据，再刷新状态。':'');
    $('dirty').textContent=dirty?'切点尚未保存。保存并确认后，受影响的段将等待重新生成，旧视频保留。':plan.segmentation_summary || '拖动彩色切点调整分段；新增或合并后的每段为4–15秒。';
    $('approve').disabled=active || pending || regenerating || promptDirty || dirty && !validCutDraft();
    $('reset-cuts').hidden=!dirty;$('reset-cuts').disabled=active || pending;
    $('run').disabled=active || pending || dirty || promptDirty || !plan.approved || !plan.credential_configured;
    $('pause').disabled=!active || plan.run_status==='merging' || pending;$('stop').disabled=!active || plan.run_status==='merging' || pending;$('refresh').disabled=pending;
    $('assemble').hidden=false;$('assemble').disabled=active || pending || dirty || promptDirty || !plan.approved || plan.segments.some(r=>r.job?.status!=='completed');
    $('reanalyze').disabled=active || pending || dirty || promptDirty || regenerating;
    $('restore').hidden=!regenerating;$('restore').disabled=active || pending || dirty || promptDirty || !plan.regeneration_can_restore;
    $('regeneration-note').hidden=!regenerating;
    $('regeneration-note').textContent=regenerating?`第${plan.regeneration.index+1}段${independent()?'':'及后续'}已加入重做。点击“开始 / 继续生成”才提交任务；可取消重做恢复旧结果。`:'';
    $('seam-review').hidden=!plan.seam_warnings?.length;$('seam-summary').textContent=`${plan.seam_warnings?.length || 0}处接缝需要复核`;
    $('seam-warnings').replaceChildren();for(const w of plan.seam_warnings || [])element('li',w,$('seam-warnings'));
    const editable=promptVersions?.editable && !active && !pending && !regenerating && !dirty && !promptLoading;
    rows.forEach((row,index)=>{
      row.end.disabled=index===rows.length-1 || cutBlocked();
      row.editor.disabled=!editable;row.savePrompt.disabled=!editable || !promptEdits.has(index);
      row.resetPrompt.disabled=pending || !promptEdits.has(index);
      if(row.regenerate)row.regenerate.disabled=active || pending || dirty || promptDirty || !plan.approved || regenerating;
      if(row.retry)row.retry.disabled=active || pending || dirty || promptDirty;
    });
    $('mask').hidden=plan.config.person_mask_mode==='none';$('mask-note').textContent='整条口播共用这张人物与场景参考图。';
    if(!$('mask').hidden && !$('mask').getAttribute('src'))$('mask').src=base+'/file/mask';
    const frozen=plan.sampling_summary;
    $('summary').textContent=`seed ${plan.config.seed} · ${(frozen?.models || []).join('，')}`+(frozen?`\n${frozen.loras.map(l=>`${l.name} × ${l.strength}`).join('，')}`:'');
    $('project-path').textContent=`项目ID：${plan.id}`;$('cloud-recovery').hidden=local;$('recover').disabled=active || pending || dirty || promptDirty;
    $('result').hidden=!plan.final_video;$('reveal-final').disabled=!plan.final_video || pending;$('final-path').textContent=plan.final_video || '';
    if(plan.final_video){$('result').hidden=false;$('result-note').textContent=plan.final_stale?'这是修改前的成片，待新分段生成后重新合并。':'完整成片';const url=base+'/file/final?version='+encodeURIComponent(plan.final_video);if($('video').dataset.version!==url){$('video').src=url;$('video').dataset.version=url;}}
    timeline?.drawTimeline();
  }
  async function perform(action,body={}) {
    if(pending)return false;pending=true;actionError='';apply(plan);
    try {
      const value=await request(action,body);
      if(action==='prompt'){promptEdits.delete(body.index);promptDirty=promptEdits.size>0;}
      if(action==='cuts'){promptVersions=null;promptEdits.clear();promptDirty=false;}
      pending=false;apply(value,true);
      if(['prompt','cuts','regenerate','restore','retry'].includes(action))await loadPrompts();
      return true;
    } catch(error){fail(error);return false;}finally{pending=false;apply(plan);}
  }
  async function saveCuts() {
    if(dirty && !await perform('cuts',{cuts:draftEnds(),expected_fingerprint:plan.editing_fingerprint}))return;
    await perform('approve');
  }
  async function refresh() {
    try{if(!pending){apply(await request(''));if(promptVersions && promptSignature!==promptKey(plan))await loadPrompts();}}catch(error){fail(error);}
  }
  $('audio').src=base+'/file/audio';$('reference').src=base+'/file/reference';
  $('approve').onclick=saveCuts;
  $('reset-cuts').onclick=()=>{dirty=false;actionError='';apply(plan,true);};
  $('run').onclick=()=>perform('run',{client_id:new URLSearchParams(location.search).get('client_id') || ''});$('pause').onclick=()=>perform('pause');$('stop').onclick=async()=>{
    const confirm=window.parent?.comfyAPI?.app?.app?.h3lvConfirmDialog;
    const options={title:'停止提交后续片段？',message:'当前正在生成的片段会正常完成，完成后不再提交新的片段。',confirmText:'停止后续生成',confirmClass:'stop',tone:'warning'};
    if(confirm?!await confirm(options):!window.confirm(options.message))return;
    return perform('stop');
  };$('assemble').onclick=()=>perform('assemble');$('restore').onclick=()=>perform('restore');
  $('reveal-final').onclick=async()=>{try{const value=await request('reveal-final',{});$('final-path').textContent=value.path;}catch(error){fail(error);}};
  $('recover').onclick=()=>perform('recover',{index:Number($('recover-index').value)-1,task_id:$('recover-id').value});
  $('refresh').onclick=async()=>{await refresh();await loadPrompts();};
  try {
    if(!document.querySelector('.h3talk-page') || !document.querySelector('#reset-cuts')){const fresh=new URL(location.href);fresh.searchParams.set('ui','segments-20261027');location.replace(fresh);throw new Error('正在加载新版口播面板…');}
    syncHost();
    const reference=document.querySelector('.h3talk-reference');reference.ontoggle=()=>{reference.querySelector('.h3lv-default-materials-toggle').textContent=reference.open?'收起':'展开';};
    if(typeof window.frameElement?.__h3talkAnalyze==='function'){$('reanalyze').hidden=false;$('reanalyze').onclick=()=>window.frameElement.__h3talkAnalyze();}
    analysis=await request('waveform');plan=await request('');
    timeline=createTimeline($('timeline'),()=>({plan:{...plan,max_seconds:independent()?15:plan.max_seconds,controller_active:cutBlocked(),segments:rows.map(()=>({}))},rows,selected,analysis,singleTrack:true}),index=>selectedWindow(index),moveCut,()=>{},async(start,end,signal)=>{
      const response=await fetch(base+`/waveform?start=${start}&end=${end}`,{signal});if(!response.ok)throw new Error('局部波形加载失败');return (await response.json()).waveform;
    },splitAt,removeCut);
    apply(plan,true);await loadPrompts();
    const timer=setInterval(refresh,2500);window.addEventListener('pagehide',()=>{clearInterval(timer);timeline.disposeTimeline();});
  }catch(error){fail(error);}
}
