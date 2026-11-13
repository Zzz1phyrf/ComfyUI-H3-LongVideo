let captureSequence=0;

// Temporary graph node IDs, not credentials. HTTP pages may lack randomUUID.
export function referenceCaptureId() {
  const crypto=globalThis.crypto;
  const nonce=typeof crypto?.randomUUID==='function' ? crypto.randomUUID() :
    typeof crypto?.getRandomValues==='function' ?
      Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('') :
      Math.random().toString(36).slice(2);
  return `h3talk-reference-${Date.now().toString(36)}-${++captureSequence}-${nonce}`;
}

// Evaluate the actual IMAGE input, including upstream image processing, without
// executing audio analysis or the downstream video generation graph.
export function referenceBranch(snapshot,owner,id='h3talk-reference-capture') {
  const prompt=snapshot.output || {},link=prompt[String(owner)]?.inputs?.reference_image;
  if(!Array.isArray(link) || link.length!==2 || !Number.isInteger(link[1]))throw new Error('请连接口播参考图输入。');
  const output={},visiting=new Set();
  function visit(key) {
    key=String(key);
    if(key===String(owner) || visiting.has(key))throw new Error('参考图输入存在循环连线。');
    if(output[key])return;
    if(!prompt[key])throw new Error('参考图上游节点缺失，请检查连线。');
    visiting.add(key);
    for(const value of Object.values(prompt[key].inputs || {}))
      if(Array.isArray(value) && value.length===2 && Number.isInteger(value[1]))visit(value[0]);
    visiting.delete(key);output[key]=prompt[key];
  }
  visit(link[0]);
  const signature=JSON.stringify([link,Object.keys(output).sort().map(key=>[key,output[key].class_type,output[key].inputs])]);
  output[id]={class_type:'H3LVTalkingReferenceCapture',inputs:{image:link}};
  return {snapshot:{output,workflow:snapshot.workflow},signature,id};
}

export function captureReference(api,branch,{timeoutMs=300000,signal}={}) {
  return new Promise((resolve,reject)=>{
    let done=false,promptId;
    const earlyErrors=[];
    const listeners=[];
    function finish(error,value) {
      if(done)return;done=true;clearTimeout(timer);
      for(const [name,fn] of listeners)api.removeEventListener(name,fn);
      signal?.removeEventListener('abort',cancel);
      error?reject(error):resolve(value);
    }
    function listen(name,fn){api.addEventListener(name,fn);listeners.push([name,fn]);}
    const timer=setTimeout(()=>finish(new Error('读取参考图超时，未开始视频生成。请等待当前队列完成后再试。')),timeoutMs);
    const cancel=()=>finish(new Error('已取消读取参考图，未开始视频生成。'));
    signal?.addEventListener('abort',cancel,{once:true});
    if(signal?.aborted){cancel();return;}
    listen('executed',event=>{
      const data=event.detail || {};
      if(String(data.node)!==branch.id)return;
      const images=data.output?.images;
      if(images?.length!==1 || images[0].type!=='temp')return finish(new Error('读取参考图失败：必须输出一张图片。'));
      finish(null,images[0]);
    });
    function failed(event) {
      const data=event.detail || {};
      if(!promptId){if(earlyErrors.length<32)earlyErrors.push(data);return;}
      if(data.prompt_id===promptId)finish(new Error('读取参考图失败：'+(data.exception_message || '图片执行已中断')));
    }
    listen('execution_error',failed);listen('execution_interrupted',failed);
    Promise.resolve().then(()=>done?null:api.queuePrompt(0,branch.snapshot,{partialExecutionTargets:[branch.id]})).then(result=>{
      promptId=result?.prompt_id;
      if(!promptId && !done)return finish(new Error('参考图任务没有返回执行编号。'));
      for(const data of earlyErrors)if(data.prompt_id===promptId)failed({detail:data});
    }).catch(error=>finish(error));
  });
}
