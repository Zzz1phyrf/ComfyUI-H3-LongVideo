import {createTimeline} from "./timeline.js";

if (document.getElementById("timeline") && location.pathname.includes("/h3talk/project/")) {
  const pid = location.pathname.match(/project\/([0-9a-f]{32})\//)?.[1];
  const base = `/h3talk/project/${pid}`;
  const $ = id => document.getElementById(id);
  let plan, rows = [], selected = 0, analysis, dirty = false, pending = false, stopAt = null, timeline, actionError = "";
  const labels = {draft:"等待确认分段",running:"RH逐段生成中",pausing:"等待当前段完成后暂停",paused:"已暂停后续生成",failed:"运行中断，可按已有任务继续",merging:"正在本地合并",completed:"完整片已完成"};
  async function request(action, body) {
    const response = await fetch(base+(action ? "/"+action : ""),body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const value = await response.json(); if (!response.ok) throw new Error(value.error || "请求失败"); return value;
  }
  function fail(error) { actionError = error.message || String(error); $("error").textContent = actionError; }
  function mark() { dirty = true; $("run").disabled=true; $("approve").disabled=true; $("dirty").textContent = "切点尚未保存；保存后会显示实际对齐结果。"; }
  function draftEnds() { return rows.slice(0,-1).map(row => Number(row.end.value)); }
  function renderRows(ends) {
    const body = $("cuts"); body.replaceChildren(); rows=[];
    [0,...ends.slice(0,-1)].forEach((start,index) => {
      const tr = document.createElement("tr");
      const add = text => { const td=document.createElement("td");td.textContent=text;tr.append(td);return td; };
      add(index+1);add(start.toFixed(3));
      const input = document.createElement("input");input.type="number";input.step="0.001";input.value=ends[index].toFixed(6);input.disabled=index===ends.length-1 || plan.controller_active;
      input.oninput=() => {mark();timeline?.drawTimeline();};add("").append(input);
      const play=document.createElement("button");play.textContent="试听";play.onclick=() => {selected=index;stopAt=Number(input.value);$("audio").currentTime=index ? Number(rows[index-1].end.value) : 0;$("audio").play().catch(fail);timeline.drawTimeline();};add("").append(play);
      const job = plan.segments[index]?.job;
      add(job ? `${job.status}${job.task_id ? " · "+job.task_id : ""}` : "尚未提交");
      const actions=add("");
      if (index<ends.length-1) {const remove=document.createElement("button");remove.textContent="删切点";remove.disabled=plan.controller_active;remove.onclick=()=>{const next=rows.map(r=>Number(r.end.value));next.splice(index,1);mark();renderRows(next);};actions.append(remove);}
      if (["failed","rejected"].includes(job?.status)) {const retry=document.createElement("button");retry.textContent="重试失败段";retry.onclick=()=>perform("retry",{index});actions.append(retry);}
      body.append(tr);rows.push({end:input});
    });
    timeline?.drawTimeline();
  }
  function apply(value, force = false) {
    plan = value;
    $("summary").textContent = `${plan.duration.toFixed(3)}秒 · ${plan.segments.length}段 · ${plan.config.megapixels} MP · seed ${plan.config.seed} · RH ${plan.config.instance_type}`;
    $("state").textContent = `${labels[plan.run_status] || plan.run_status} · 已完成 ${plan.segments.filter(r=>r.job?.status==="completed").length}/${plan.segments.length}${plan.approved ? " · 分段已确认" : ""}`;
    if (!plan.controller_active && ["running","pausing","merging"].includes(plan.run_status)) $("state").textContent += " · 本地控制已中断，可继续恢复已有任务";
    $("error").textContent = actionError || plan.error || (!plan.credential_configured ? "未配置RUNNINGHUB_API_KEY；请在启动本地ComfyUI的进程环境中设置后重启。" : "");
    if (!dirty || force) {
      renderRows(plan.segments.map(r=>r.end_sample/24000));dirty=false;$("dirty").textContent="";
    }
    const active = plan.controller_active;
    for (const id of ["save","approve","add","recover"]) $(id).disabled=active || pending;
    $("run").disabled=active || pending || dirty || !plan.approved || !plan.credential_configured;
    $("pause").disabled=!active;
    $("assemble").disabled=active || pending || dirty || !plan.approved || plan.segments.some(r=>r.job?.status!=="completed");
    $("project-path").textContent=`项目ID：${plan.id}。重启本地ComfyUI后，可从节点的project_id继续恢复。`;
    if (plan.first_condition==="ref2va") $("dirty").textContent += " 多参考图首段采用Ref2VA；多图完整片画质尚未验收。";
    if (plan.final_video) {
      $("result").hidden=false;$("result-note").textContent=plan.final_stale ? "这是旧成片；修改后的分段尚未重新生成。" : "本地裁切合并的完整片，请原速观看并试听。";
      const url=base+"/file/final?version="+encodeURIComponent(plan.final_video);
      if ($("video").dataset.version!==url) {$("video").src=url;$("video").dataset.version=url;}
    }
  }
  async function perform(action, body={}) {
    if (pending) return;
    pending=true;actionError="";
    try {const value=await request(action,body);pending=false;apply(value,true);} catch(error) {fail(error);} finally {pending=false;}
  }
  async function refresh() {try {if(!pending) apply(await request(""));} catch(error) {fail(error);}}
  $("audio").src=base+"/file/audio";$("reference").src=base+"/file/reference";$("mask").src=base+"/file/mask";
  $("audio").ontimeupdate=()=>{if(stopAt!==null && $("audio").currentTime>=stopAt){$("audio").pause();stopAt=null;}};
  $("audio").onseeked=()=>{if($("audio").paused)stopAt=null;};
  $("add").onclick=()=>{const ends=rows.map(r=>Number(r.end.value));const t=$("audio").currentTime;if(t<=0 || t>=plan.duration)return;ends.push(t);ends.sort((a,b)=>a-b);mark();renderRows(ends);};
  $("save").onclick=()=>perform("cuts",{cuts:draftEnds()});
  $("approve").onclick=()=>{if(dirty){fail(new Error("请先保存切点并试听实际落点。"));return;}perform("approve");};
  $("run").onclick=()=>perform("run");$("pause").onclick=()=>perform("pause");$("assemble").onclick=()=>perform("assemble");
  $("recover").onclick=()=>perform("recover",{index:Number($("recover-index").value)-1,task_id:$("recover-id").value});
  try {
    analysis=await request("waveform");plan=await request("");
    timeline=createTimeline($("timeline"),()=>({plan:{...plan,segments:rows.map((_,i)=>plan.segments[i] || {})},rows,selected,analysis}),(index)=>{selected=index;},(index,t)=>{
      const lo=index ? Number(rows[index-1].end.value)+.01 : .01;
      const hi=Number(rows[index+1].end.value)-.01;
      rows[index].end.value=Math.max(lo,Math.min(hi,t)).toFixed(6);mark();
    },()=>{},null);
    apply(plan,true);
    const timer=setInterval(refresh,2500);window.addEventListener("pagehide",()=>{clearInterval(timer);timeline.disposeTimeline();});
  } catch(error) {fail(error);}
}
