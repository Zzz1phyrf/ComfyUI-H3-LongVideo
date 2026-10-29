import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {fitWindow, zoomWindow, pcmWavePeaks, createTimeline, cutRange} from '../web/timeline.js';

test('speech and MV both drag segment cuts while speech uses one source track',()=>{
  const labels=[];let moved=0,speech=false;
  const ctx=new Proxy({}, {get:(_,name)=>name==='measureText'?()=>({width:36}):name==='fillText'?(text)=>labels.push(text):()=>{}});
  class Node {
    children=[];value='';style={};
    append(n){this.children.push(n);}before(n){this.tools=n;}after(){}setAttribute(){}addEventListener(){}
    getBoundingClientRect(){return {left:0,width:1000};}getContext(){return ctx;}
    setPointerCapture(){}hasPointerCapture(){return false;}
  }
  globalThis.document={createElement:()=>new Node()};globalThis.window={devicePixelRatio:1};
  globalThis.ResizeObserver=class{observe(){}disconnect(){}};
  const canvas=new Node(),plan={id:'tracks',duration:10,max_seconds:15,segments:[{},{}]},rows=[5,10].map(end=>({end:{value:end}}));
  createTimeline(canvas,()=>({plan,rows,selected:0,analysis:{},singleTrack:speech}),()=>{},()=>moved++,()=>{},null);
  canvas.drawTimeline();assert.ok(labels.includes('原曲'));assert.ok(labels.includes('人声'));
  canvas.onpointerdown({clientX:516,button:0,pointerId:1});canvas.onpointermove({clientX:520});canvas.onpointerup({pointerId:1});
  assert.equal(moved,1);speech=true;labels.length=0;canvas.drawTimeline();
  assert.ok(labels.includes('原声'));assert.ok(!labels.includes('人声'));assert.ok(labels.includes('第1段'));
  canvas.onpointerdown({clientX:516,button:0,pointerId:1});canvas.onpointermove({clientX:520});canvas.onpointerup({pointerId:1});
  assert.equal(moved,2);canvas.disposeTimeline();
});

test('cut range leaves two 4 to 15 second segments and respects project cap',()=>{
  assert.deepEqual(cutRange(10,20),{min:14,max:16});
  assert.deepEqual(cutRange(10,18),{min:14,max:14});
  assert.ok(cutRange(10,17).min>cutRange(10,17).max);
  assert.deepEqual(cutRange(0,30,30),{min:15,max:15});
  assert.deepEqual(cutRange(0,12,7),{min:5,max:7});
});

test('add-cut action uses clicked time, rejects short pieces and blocks duplicate submission',async()=>{
  const ctx=new Proxy({}, {get:(_,name)=>name==='measureText'?()=>({width:36}):()=>{}});
  class Node {
    children=[]; value=''; style={};
    append(n){this.children.push(n);} before(n){this.tools=n;} after(n){this.afterNode=n;}
    setAttribute(){} addEventListener(){}
    getBoundingClientRect(){return {left:0,width:1000};} getContext(){return ctx;}
    setPointerCapture(){} hasPointerCapture(){return false;}
  }
  globalThis.document={createElement:()=>new Node()};globalThis.window={devicePixelRatio:1};
  globalThis.ResizeObserver=class{observe(){} disconnect(){}};
  const canvas=new Node(),plan={id:'cut-test',duration:17,max_seconds:15,segments:[{},{}]},rows=[10,17].map(end=>({end:{value:String(end)}}));
  let selected=0,calls=[],finish;
  createTimeline(canvas,()=>({plan,rows,selected,analysis:{}}),i=>selected=i,()=>{},()=>{},null,
    time=>{calls.push(time);return new Promise(resolve=>finish=resolve);});
  canvas.drawTimeline();
  const input=canvas.tools.children.find(n=>n.type==='number'),button=canvas.tools.children.find(n=>n.textContent==='＋ 增加切点');
  assert.equal(Number(input.value),5);assert.equal(button.disabled,false);
  canvas.onpointerdown({clientX:50+6/17*932,button:0,pointerId:1});
  canvas.onpointerup({pointerId:1});canvas.onpointerleave();
  assert.equal(Number(input.value),6);
  input.value='3.9';input.oninput();assert.equal(button.disabled,true);button.onclick();assert.equal(calls.length,0);
  input.value='4';input.oninput();button.onclick();button.onclick();
  assert.deepEqual(calls,[4]);assert.equal(button.disabled,true);
  finish();await new Promise(resolve=>setTimeout(resolve,0));
  selected=1;canvas.drawTimeline();assert.equal(button.disabled,true);
  selected=0;plan.run_status='running';canvas.drawTimeline();assert.equal(button.disabled,true);
  canvas.disposeTimeline();
});

test('remove-cut selects an internal boundary, enforces duration and prevents duplicate submissions',async()=>{
  const ctx=new Proxy({}, {get:(_,name)=>name==='measureText'?()=>({width:36}):()=>{}});
  class Node {
    children=[]; value=''; style={};
    append(n){this.children.push(n);} before(n){this.tools=n;} after(n){this.afterNode=n;}
    setAttribute(){} addEventListener(){}
    getBoundingClientRect(){return {left:0,width:1000};} getContext(){return ctx;}
    setPointerCapture(){} hasPointerCapture(){return false;}
  }
  globalThis.document={createElement:()=>new Node()};globalThis.window={devicePixelRatio:1};
  globalThis.ResizeObserver=class{observe(){} disconnect(){}};
  const canvas=new Node(),plan={id:'remove-test',duration:25,max_seconds:15,segments:[{},{},{}]};
  let rows=[5,10,25].map(end=>({end:{value:String(end)}})),selected=0,calls=[],finish;
  createTimeline(canvas,()=>({plan,rows,selected,analysis:{}}),i=>selected=i,()=>{},()=>{},null,()=>{},
    index=>{calls.push(index);return new Promise(resolve=>finish=resolve);});
  canvas.drawTimeline();
  const button=canvas.tools.children.find(n=>n.textContent==='− 删除切点');
  const add=canvas.tools.children.find(n=>n.textContent==='＋ 增加切点');
  assert.equal(button.disabled,false);
  assert.ok(canvas.tools.children.some(n=>n.textContent?.includes('删除 5.000s 切点')));
  selected=2;canvas.drawTimeline();assert.equal(button.disabled,true);
  assert.ok(canvas.tools.children.some(n=>n.textContent?.includes('超过15秒')));
  canvas.onpointerdown({clientX:50+5/25*932,button:0,pointerId:1});canvas.onpointerup({pointerId:1});
  assert.equal(selected,0);assert.equal(button.disabled,false);
  button.onclick();button.onclick();assert.deepEqual(calls,[0]);assert.equal(add.disabled,true);
  finish();await new Promise(resolve=>setTimeout(resolve,0));
  plan.controller_active=true;canvas.drawTimeline();assert.equal(button.disabled,true);
  plan.controller_active=false;plan.max_seconds=8;canvas.drawTimeline();assert.equal(button.disabled,true);
  plan.max_seconds=15;plan.run_status='running';canvas.drawTimeline();assert.equal(button.disabled,true);
  plan.run_status='draft';rows=[{end:{value:'10'}}];plan.segments=[{}];canvas.drawTimeline();
  assert.equal(button.disabled,true);canvas.disposeTimeline();
});

test('long-song zoom keeps the pointer time anchored', () => {
  const view={start:0,span:209.269};
  const next=zoomWindow(view,.5,175,209.269);
  assert.ok(Math.abs((175-next.start)/next.span-175/view.span)<1e-10);
  assert.deepEqual(fitWindow(205,8,209.269),{start:201.269,span:8});
  assert.deepEqual(fitWindow(-4,8,209.269),{start:0,span:8});
  assert.equal(zoomWindow({start:178,span:8},.001,182,209.269).span,.5);
});
function wav(samples, channels=2) {
  const buffer=new ArrayBuffer(44+samples.length*2),v=new DataView(buffer);
  const tag=(offset,text)=>[...text].forEach((c,i)=>v.setUint8(offset+i,c.charCodeAt(0)));
  tag(0,'RIFF');v.setUint32(4,buffer.byteLength-8,true);tag(8,'WAVE');tag(12,'fmt ');
  v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,channels,true);
  v.setUint32(24,48000,true);v.setUint32(28,48000*channels*2,true);v.setUint16(32,channels*2,true);v.setUint16(34,16,true);
  tag(36,'data');v.setUint32(40,samples.length*2,true);
  samples.forEach((s,i)=>v.setInt16(44+i*2,s,true));return buffer;
}
test('detail peaks retain silence, negative samples and both channels',()=>{
  assert.deepEqual([...pcmWavePeaks(wav([0,0,-32768,0,0,0,0,16384]),4)],[0,1,0,1]);
  assert.deepEqual([...pcmWavePeaks(wav([0,0,0,0]),2)],[0,0]);
  assert.throws(()=>pcmWavePeaks(new ArrayBuffer(10)));
  assert.throws(()=>pcmWavePeaks(wav([1,2]).slice(0,45)));
});
test('late-segment selection, fine drag and stale detail requests',async()=>{
  const ctx=new Proxy({}, {get:(_,name)=>name==='measureText'?()=>({width:36}):()=>{}});
  class Node {
    children=[]; value='0';
    append(n){this.children.push(n);} before(n){this.tools=n;} after(n){this.afterNode=n;}
    setAttribute(){} addEventListener(name,fn){this[name]=fn;}
    getBoundingClientRect(){return {left:0,width:1000};} getContext(){return ctx;}
    setPointerCapture(){} hasPointerCapture(){return false;}
  }
  globalThis.document={createElement:()=>new Node()};globalThis.window={devicePixelRatio:1};
  globalThis.ResizeObserver=class{observe(){} disconnect(){}};
  const canvas=new Node(),ends=Array.from({length:17},(_,i)=>Math.min((i+1)*12.3,209.1));
  let selected=0,commits=0;
  const plan={id:'song',duration:209.1,max_seconds:15,segments:ends.map(()=>({boundary_confidence:.8}))};
  const rows=ends.map(end=>({end:{value:String(end)}}));let requests=[];
  createTimeline(canvas,()=>({plan,rows,selected,analysis:{}}),i=>selected=i,(i,t)=>rows[i].end.value=t,()=>commits++,
    (start,end,signal)=>new Promise(resolve=>requests.push({start,end,signal,resolve})));
  canvas.drawTimeline();
  const evt=(x)=>({clientX:x,button:0,pointerId:1});
  canvas.onpointerdown(evt(50+178/209.1*932));canvas.onpointerup(evt(0));assert.equal(selected,14);assert.equal(commits,0);
  canvas.tools.children[4].onclick();await new Promise(r=>setTimeout(r,180));
  assert.equal(requests[0].end-requests[0].start,8);
  canvas.onpointerdown(evt(516));canvas.onpointermove(evt(518));canvas.onpointerup(evt(518));
  assert.ok(Math.abs(Number(rows[14].end.value)-ends[14]-16/932)<1e-8);assert.equal(commits,1);
  canvas.tools.children[2].onclick();await new Promise(r=>setTimeout(r,180));assert.ok(requests[0].signal.aborted);
  requests[0].resolve({original:[],vocals:[]});await Promise.resolve();
  assert.match(canvas.tools.children.at(-1).textContent,/加载中/);
  requests[1].resolve({original:[],vocals:[]});await Promise.resolve();
  assert.match(canvas.tools.children.at(-1).textContent,/精细波形/);
  canvas.disposeTimeline();assert.ok(requests[1].signal.aborted);
});
test('review selection does not scroll to segment cards',async()=>{
  const source=await readFile(new URL('../web/h3lv.js',import.meta.url),'utf8');
  assert.ok(!source.includes('scrollIntoView'));
  assert.match(source,/createTimeline\(canvas, state, index => updateSelected\(index\)/);
});
