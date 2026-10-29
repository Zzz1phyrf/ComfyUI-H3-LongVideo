export function clampPanelPosition(left, top, width, height, viewportWidth, viewportHeight, margin = 8) {
  const maxLeft = Math.max(0, viewportWidth - width), maxTop = Math.max(0, viewportHeight - height);
  const lowLeft = Math.min(margin, maxLeft / 2), lowTop = Math.min(margin, maxTop / 2);
  return {left:Math.max(lowLeft, Math.min(maxLeft-lowLeft, left)), top:Math.max(lowTop, Math.min(maxTop-lowTop, top))};
}

// The handle is limited to the title row; media, editing and scroll events never start a drag.
export function attachPanelDrag(panel, handle) {
  let drag = null, disposed = false;
  const place = (left, top) => {
    const box = panel.getBoundingClientRect();
    const next = clampPanelPosition(left, top, box.width, box.height, window.innerWidth, window.innerHeight);
    Object.assign(panel.style, {position:"fixed", left:`${next.left}px`, top:`${next.top}px`, margin:"0"});
  };
  const recenter = () => {
    const box = panel.getBoundingClientRect(); place((window.innerWidth-box.width)/2, (window.innerHeight-box.height)/2);
  };
  const resize = () => {if (!disposed) {const box = panel.getBoundingClientRect(); place(box.left,box.top);}};
  const down = event => {
    if (event.button !== 0 || event.isPrimary === false || event.target.closest("button,input,select,textarea,a,summary,audio,video,[contenteditable]")) return;
    const box = panel.getBoundingClientRect(); drag = {x:event.clientX,y:event.clientY,left:box.left,top:box.top,id:event.pointerId};
    handle.setPointerCapture(event.pointerId); handle.classList.add("is-dragging"); event.preventDefault();
  };
  const move = event => {if (drag?.id === event.pointerId) {place(drag.left+event.clientX-drag.x,drag.top+event.clientY-drag.y); event.preventDefault();}};
  const up = event => {
    if (drag?.id !== event.pointerId) return;
    drag = null; handle.classList.remove("is-dragging");
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  };
  const key = event => {
    if (event.target !== handle) return;
    if (event.key === "Home") {event.preventDefault(); recenter(); return;}
    const delta = {ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];
    if (!delta) return; event.preventDefault();
    const box = panel.getBoundingClientRect(), step = event.shiftKey ? 48 : 16; place(box.left+delta[0]*step, box.top+delta[1]*step);
  };
  handle.classList.add("h3lv-drag-handle"); handle.tabIndex = 0;
  handle.setAttribute("aria-label", "拖动审核窗口；方向键移动，Home 键居中");
  handle.addEventListener("pointerdown", down); handle.addEventListener("pointermove", move);
  handle.addEventListener("pointerup", up); handle.addEventListener("pointercancel", up); handle.addEventListener("lostpointercapture", up);
  handle.addEventListener("keydown", key); window.addEventListener("resize",resize);
  recenter();
  return () => {
    if (disposed) return; disposed = true; drag = null;
    window.removeEventListener("resize",resize); handle.removeEventListener("pointerdown",down); handle.removeEventListener("pointermove",move);
    handle.removeEventListener("pointerup",up); handle.removeEventListener("pointercancel",up); handle.removeEventListener("lostpointercapture",up); handle.removeEventListener("keydown",key);
  };
}
