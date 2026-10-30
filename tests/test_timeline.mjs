import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {fitWindow, zoomWindow, pcmWavePeaks, createTimeline} from '../web/timeline.js';

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
