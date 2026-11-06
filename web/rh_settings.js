import {app} from "../../scripts/app.js";
import {api} from "../../scripts/api.js";
import {alignProjectNode} from './node_ui.js';

async function rhSettingsRequest(baseUrl, values) {
  const response=await api.fetchApi('/h3lv/rh/settings'+(values ? '' : '?base_url='+encodeURIComponent(baseUrl)),
    values ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_url:baseUrl,...values})} : {});
  const result=await response.json();
  if (!response.ok) throw new Error(result.error || 'RH设置读取或保存失败。');
  return result;
}

async function showRHSettings(node) {
  document.getElementById('h3lv-rh-settings')?.remove();
  const dialog=document.createElement('dialog');dialog.id='h3lv-rh-settings';
  Object.assign(dialog.style,{width:'460px',maxWidth:'90vw',background:'#202b36',color:'#eee',border:'1px solid #586778',borderRadius:'10px',padding:'24px'});
  dialog.innerHTML=`<h3>RunningHub API 设置</h3>
    <p>仅保存在本机用户目录，不写入工作流。MV和口播共用；与提示词扩写API分开。</p>
    <label>站点 <select data-site><option value="https://www.runninghub.ai">海外版</option><option value="https://www.runninghub.cn">国内版</option></select></label>
    <p><label>API Key <input data-key type="password" autocomplete="off" placeholder="填写新的Key；不会回显已保存内容" style="width:100%;box-sizing:border-box;padding:8px;margin-top:8px"></label></p>
    <p data-status role="status"></p>
    <div style="display:flex;gap:10px;justify-content:flex-end"><button data-clear>清除本机Key</button><button data-close>关闭</button><button data-save>保存</button></div>`;
  const site=dialog.querySelector('[data-site]'),key=dialog.querySelector('[data-key]'),status=dialog.querySelector('[data-status]');
  const buttons=[...dialog.querySelectorAll('button')];
  site.value=node.widgets?.find(w=>['rh_region','region'].includes(w.name))?.value==='国内版' ? 'https://www.runninghub.cn' : 'https://www.runninghub.ai';
  const describe=value=>value.source==='local' ? '已使用本机保存的Key。可直接重新检查RH配置，无需重启。' : value.source==='environment' ? '当前使用环境变量中的Key。保存后优先使用本机Key；清除本机配置不会删除环境变量。' : '尚未配置此站点的Key。';
  async function perform(values) {
    buttons.forEach(b=>b.disabled=true);site.disabled=true;key.disabled=true;
    try {status.textContent=describe(await rhSettingsRequest(site.value,values));}
    catch(error){status.textContent=error.message;}
    finally {key.value='';buttons.forEach(b=>b.disabled=false);site.disabled=false;key.disabled=false;}
  }
  site.onchange=()=>perform();
  dialog.querySelector('[data-save]').onclick=()=>{
    const value=key.value.trim();
    if (!value) {status.textContent='请填写新的Key；原配置未修改。';return;}
    return perform({api_key:value});
  };
  dialog.querySelector('[data-clear]').onclick=()=>perform({clear:true});
  const close=()=>{key.value='';dialog.close();dialog.remove();};
  dialog.querySelector('[data-close]').onclick=close;
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  document.body.append(dialog);dialog.showModal();await perform();
}

app.registerExtension({
  name:'H3LongVideo.RHSettings',
  beforeRegisterNodeDef(nodeType,nodeData) {
    if (!['H3LVUnified','H3LVTalkingRH','H3LVTalkingAvatar'].includes(nodeData.name)) return;
    const created=nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated=function() {
      created?.apply(this,arguments);
      this.addWidget('button','RH API 设置',null,()=>showRHSettings(this),{serialize:false});
      alignProjectNode(this);
    };
  },
});
