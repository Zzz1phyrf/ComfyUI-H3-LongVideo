export function fitWindow(start, span, duration) {
  span = Math.min(duration, Math.max(Math.min(.5, duration), span));
  return {start: Math.max(0, Math.min(duration-span, start)), span};
}

export function zoomWindow(view, factor, anchor, duration) {
  const fraction = (anchor-view.start)/view.span;
  const span = fitWindow(0, view.span*factor, duration).span;
  return fitWindow(anchor-fraction*span, span, duration);
}

// The existing audio-preview endpoint returns PCM16 WAV, including for old projects.
export function pcmWavePeaks(buffer, bins = 2048) {
  const data = new DataView(buffer);
  const tag = offset => String.fromCharCode(...new Uint8Array(buffer, offset, 4));
  if (data.byteLength < 12 || tag(0) !== "RIFF" || tag(8) !== "WAVE") throw new Error("无效的试听音频");
  let channels = 0, block = 0, begin = 0, length = 0;
  for (let offset = 12; offset+8 <= data.byteLength;) {
    const size = data.getUint32(offset+4, true), next = offset+8;
    if (next+size > data.byteLength) throw new Error("试听音频不完整");
    if (tag(offset) === "fmt ") {
      if (size < 16 || data.getUint16(next, true) !== 1 || data.getUint16(next+14, true) !== 16) {
        throw new Error("试听音频格式不支持");
      }
      channels = data.getUint16(next+2, true);
      block = data.getUint16(next+12, true);
    }
    if (tag(offset) === "data") { begin = next; length = size; }
    offset = next+size+(size%2);
  }
  if (!channels || block !== channels*2 || !length) throw new Error("试听音频为空");
  const frames = Math.floor(length/block), count = Math.min(bins, frames);
  if (!count) throw new Error("试听音频为空");
  const peaks = new Float32Array(count);
  for (let i = 0; i < frames; i++) {
    const bin = Math.min(count-1, Math.floor(i*count/frames));
    for (let channel = 0; channel < channels; channel++) {
      peaks[bin] = Math.max(peaks[bin], Math.abs(data.getInt16(begin+i*block+channel*2, true))/32768);
    }
  }
  const sorted = Array.from(peaks).sort((a, b) => a-b);
  const scale = Math.max(sorted[Math.floor((count-1)*.99)] || 0, 1e-9);
  return peaks.map(value => Math.min(1, value/scale));
}

export function createTimeline(canvas, getState, onSelect, onMove, onCommit, loadDetail) {
  let project = null, view = {start: 0, span: 1}, dragIndex = -1, changed = false;
  let hover = null, pan = null, detail = null, detailKey = "", detailStatus = "";
  let timer = null, controller = null, disposed = false;
  const toolbar = document.createElement("div");
  toolbar.className = "h3lv-timeline-tools";
  canvas.before(toolbar);
  const button = (label, action) => {
    const node = document.createElement("button");
    node.type = "button"; node.className = "h3lv-button"; node.textContent = label;
    node.onclick = () => { if (getState().plan) action(); }; toolbar.append(node); return node;
  };
  const bounds = () => {
    const {rows, selected} = getState();
    return {start: selected ? Number(rows[selected-1].end.value) : 0, end: Number(rows[selected]?.end.value || 0)};
  };
  button("全曲", () => setView(0, getState().plan.duration));
  button("− 缩小", () => zoom(2));
  button("＋ 放大", () => zoom(.5));
  button("当前段", () => { const b = bounds(); setView(b.start, b.end-b.start); });
  button("切点附近", () => {
    const {plan, selected, rows} = getState();
    const b = bounds(), cut = selected < rows.length-1 ? b.end : b.start;
    const span = Math.min(8, plan.max_seconds, plan.duration);
    setView(cut-span/2, span);
  });
  const readout = document.createElement("span");
  readout.className = "h3lv-timeline-readout"; toolbar.append(readout);
  const navigation = document.createElement("input");
  navigation.type = "range"; navigation.min = "0"; navigation.step = ".001";
  navigation.className = "h3lv-timeline-navigation";
  navigation.setAttribute("aria-label", "波形显示起点");
  canvas.after(navigation);
  navigation.oninput = () => setView(Number(navigation.value), view.span);
  const hint = document.createElement("div");
  hint.className = "h3lv-help";
  hint.textContent = "双击波形放大附近；Ctrl / ⌘ + 滚轮缩放；Shift + 滚轮横移。拖动彩色切点线调整，普通滚轮滚动页面。";
  navigation.after(hint);
  const geometry = () => ({left: 50, right: 18, top: 25, height: 178});
  const widthFor = () => Math.max(1, canvas.getBoundingClientRect().width);
  const xFor = (time, width) => 50+(time-view.start)/view.span*Math.max(1, width-68);
  const timeFor = clientX => {
    const rect = canvas.getBoundingClientRect();
    return view.start+Math.max(0, Math.min(1, (clientX-rect.left-50)/Math.max(1, rect.width-68)))*view.span;
  };
  function setView(start, span) {
    const {plan} = getState();
    if (!plan) return;
    view = fitWindow(start, span, plan.duration); hover = null; draw();
  }
  function zoom(factor, anchor = view.start+view.span/2) {
    const {plan} = getState();
    if (!plan) return;
    view = zoomWindow(view, factor, anchor, plan.duration); draw();
  }
  function fetchDetail(plan) {
    const key = `${plan.id}:${view.start.toFixed(6)}:${view.span.toFixed(6)}`;
    if (key === detailKey) return;
    detailKey = key; detail = null; clearTimeout(timer); controller?.abort();
    detailStatus = view.span <= plan.max_seconds+1e-6 ? "局部波形加载中…" : "全曲概览 · 继续放大可查看精细波形";
    if (!loadDetail || view.span > plan.max_seconds+1e-6) return;
    const start = view.start, end = Math.min(plan.duration, start+view.span);
    controller = new AbortController();
    const signal = controller.signal;
    timer = setTimeout(async () => {
      try {
        const waves = await loadDetail(start, end, signal);
        if (disposed || signal.aborted || detailKey !== key) return;
        detail = {...waves, start, end}; detailStatus = "精细波形"; draw();
      } catch (error) {
        if (disposed || signal.aborted || detailKey !== key) return;
        detailStatus = "局部波形加载失败，当前显示概览"; draw();
      }
    }, 160);
  }
  function drawWave(ctx, peaks, center, amplitude, width, start, end, color) {
    if (!peaks?.length) return;
    ctx.beginPath();
    const first = Math.max(0, Math.floor((view.start-start)/(end-start)*peaks.length)-1);
    const last = Math.min(peaks.length, Math.ceil((view.start+view.span-start)/(end-start)*peaks.length)+1);
    for (let i = first; i < last; i++) {
      const x = xFor(start+(i+.5)/peaks.length*(end-start), width);
      ctx.moveTo(x, center-peaks[i]*amplitude); ctx.lineTo(x, center+peaks[i]*amplitude);
    }
    ctx.strokeStyle = color; ctx.globalAlpha = .82; ctx.lineWidth = 1; ctx.stroke(); ctx.globalAlpha = 1;
  }
  function draw() {
    if (disposed) return;
    const {plan, analysis, rows, selected} = getState();
    if (!plan || !rows.length) return;
    if (project !== plan.id) {
      project = plan.id; view = {start: 0, span: plan.duration}; hover = null; dragIndex = -1;
    }
    view = fitWindow(view.start, view.span, plan.duration);
    fetchDetail(plan);
    navigation.max = Math.max(0, plan.duration-view.span);
    navigation.value = view.start; navigation.disabled = view.span >= plan.duration;
    readout.textContent = `${view.start.toFixed(3)}—${(view.start+view.span).toFixed(3)}s · ${detailStatus}`+
      (hover === null ? "" : ` · 指针 ${hover.toFixed(3)}s`);
    const width = widthFor(), height = 228, ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width*ratio); canvas.height = Math.round(height*ratio);
    const ctx = canvas.getContext("2d"), g = geometry();
    ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#9ea8b7"; ctx.font = "12px sans-serif";
    ctx.fillText("原曲", 12, 74); ctx.fillText("人声", 12, 149);
    ctx.save(); ctx.beginPath(); ctx.rect(g.left, 0, Math.max(1, width-g.left-g.right), height); ctx.clip();
    ctx.fillStyle = "#11151c"; ctx.fillRect(g.left, g.top, width-g.left-g.right, g.height);
    const ends = rows.map(row => Number(row.end.value)), starts = [0, ...ends.slice(0, -1)];
    starts.forEach((start, index) => {
      const x1 = xFor(start, width), x2 = xFor(ends[index], width);
      ctx.fillStyle = index === selected ? "rgba(77,163,255,.23)" : (index%2 ? "rgba(255,255,255,.025)" : "rgba(255,255,255,.055)");
      ctx.fillRect(x1, g.top, x2-x1, g.height);
    });
    for (const section of analysis?.sections || []) {
      const x1 = xFor(section.start, width), x2 = xFor(section.end, width);
      ctx.fillStyle = "rgba(178,125,255,.13)"; ctx.fillRect(x1, g.top, x2-x1, g.height);
      ctx.fillStyle = "#c6a5ff"; ctx.font = "11px sans-serif"; ctx.fillText(section.kind, x1+4, g.top+14);
    }
    const waveform = detail || analysis?.waveform;
    drawWave(ctx, waveform?.original, 70, 34, width, detail?.start || 0, detail?.end || plan.duration, "#6fb8ff");
    drawWave(ctx, waveform?.vocals, 145, 34, width, detail?.start || 0, detail?.end || plan.duration, "#7ee0b1");
    let lastLabel = -Infinity;
    starts.forEach((start, index) => {
      const left = Math.max(g.left, xFor(start, width)), right = Math.min(width-g.right, xFor(ends[index], width));
      const label = `第${index+1}段`; ctx.font = "bold 12px sans-serif";
      const labelWidth = ctx.measureText(label).width+12, x = (left+right)/2;
      if (right-left < labelWidth || x-labelWidth/2 < lastLabel+3) return;
      lastLabel = x+labelWidth/2; ctx.fillStyle = "rgba(12,16,22,.72)";
      ctx.fillRect(x-labelWidth/2, g.top+5, labelWidth, 20);
      ctx.fillStyle = "#edf2f8"; ctx.fillText(label, x-labelWidth/2+6, g.top+19);
    });
    ctx.strokeStyle = "rgba(255,208,111,.16)"; ctx.lineWidth = 1;
    for (const beat of analysis?.rhythm?.bars || []) {
      if (beat < view.start || beat > view.start+view.span) continue;
      const x = xFor(beat, width); ctx.beginPath(); ctx.moveTo(x, g.top); ctx.lineTo(x, g.top+g.height); ctx.stroke();
    }
    ends.slice(0, -1).forEach((end, index) => {
      if (end < view.start || end > view.start+view.span) return;
      const row = plan.segments[index], x = xFor(end, width);
      ctx.strokeStyle = row.boundary_kind === "endpoint" ? "#9ea8b7" : row.boundary_confidence >= .8 ? "#50d890" : row.boundary_confidence >= .5 ? "#f1c45c" : "#ff7d7d";
      ctx.lineWidth = dragIndex === index ? 4 : 2;
      ctx.beginPath(); ctx.moveTo(x, g.top); ctx.lineTo(x, g.top+g.height); ctx.stroke();
      ctx.fillStyle = ctx.strokeStyle; ctx.beginPath(); ctx.arc(x, g.top, 6, 0, Math.PI*2); ctx.fill();
      if (view.span <= 30) { ctx.font = "11px sans-serif"; ctx.fillText(`${end.toFixed(3)}s`, x+7, 18); }
    });
    if (hover !== null) {
      const x = xFor(hover, width); ctx.strokeStyle = "rgba(255,255,255,.55)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, g.top+8); ctx.lineTo(x, g.top+g.height); ctx.stroke();
    }
    const desired = view.span/Math.max(2, Math.floor((width-68)/85));
    const magnitude = 10**Math.floor(Math.log10(desired));
    const step = [1, 2, 5, 10].find(value => value*magnitude >= desired)*magnitude;
    ctx.fillStyle = "#9ea8b7"; ctx.font = "11px sans-serif";
    for (let time = Math.ceil(view.start/step)*step; time <= view.start+view.span+1e-8; time += step) {
      ctx.fillText(`${time.toFixed(step < 1 ? 2 : step < 10 ? 1 : 0)}s`, xFor(time, width)+2, 222);
    }
    ctx.restore();
  }
  function nearestBoundary(clientX) {
    const {rows} = getState(), rect = canvas.getBoundingClientRect();
    let result = -1, distance = 12;
    rows.slice(0, -1).forEach((row, index) => {
      const end = Number(row.end.value);
      if (end < view.start || end > view.start+view.span) return;
      const d = Math.abs(clientX-rect.left-xFor(end, rect.width));
      if (d < distance) { result = index; distance = d; }
    });
    return result;
  }
  canvas.onpointerdown = event => {
    if (!getState().plan || event.button > 1) return;
    if (event.shiftKey || event.button === 1) {
      pan = {x: event.clientX, start: view.start}; canvas.setPointerCapture(event.pointerId); return;
    }
    dragIndex = nearestBoundary(event.clientX); changed = false;
    if (dragIndex >= 0) { canvas.setPointerCapture(event.pointerId); onSelect(dragIndex); }
    else {
      const {rows} = getState(), time = timeFor(event.clientX);
      const index = rows.findIndex(row => time <= Number(row.end.value));
      onSelect(index < 0 ? rows.length-1 : index);
    }
    draw();
  };
  canvas.onpointermove = event => {
    if (pan) { setView(pan.start-(event.clientX-pan.x)/Math.max(1, widthFor()-68)*view.span, view.span); return; }
    hover = timeFor(event.clientX);
    if (dragIndex >= 0) { onMove(dragIndex, hover); changed = true; }
    draw();
  };
  const stop = event => {
    const committed = dragIndex; dragIndex = -1; pan = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    if (committed >= 0 && changed) onCommit(committed);
    changed = false; draw();
  };
  canvas.onpointerup = stop; canvas.onpointercancel = stop;
  canvas.onpointerleave = () => { if (dragIndex < 0 && !pan) { hover = null; draw(); } };
  canvas.ondblclick = event => {
    if (!getState().plan) return;
    const span = Math.min(8, getState().plan.max_seconds, getState().plan.duration);
    setView(timeFor(event.clientX)-span/2, span);
  };
  canvas.addEventListener("wheel", event => {
    if (!getState().plan || dragIndex >= 0 || pan) return;
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault(); zoom(Math.exp(Math.max(-1, Math.min(1, event.deltaY*.003))), timeFor(event.clientX));
    } else if (event.shiftKey || event.deltaX) {
      event.preventDefault(); setView(view.start+(event.deltaX || event.deltaY)/Math.max(1, widthFor()-68)*view.span, view.span);
    }
  }, {passive: false});
  canvas.setAttribute("aria-label", "可缩放的原曲和人声波形；双击放大附近，拖动切点；也可使用分段结束时间输入框");
  const observer = new ResizeObserver(draw); observer.observe(canvas);
  canvas.drawTimeline = draw;
  canvas.disposeTimeline = () => { disposed = true; clearTimeout(timer); controller?.abort(); observer.disconnect(); };
  return canvas;
}
