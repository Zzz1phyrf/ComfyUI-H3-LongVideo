import {app} from "../../scripts/app.js";

function showPanel(node) {
  const pid = node.widgets?.find(w => w.name === "project_id")?.value;
  if (!/^[0-9a-f]{32}$/.test(pid || "")) { alert("请先运行口播节点，准备音频和分段。"); return; }
  document.getElementById("h3talk-modal")?.remove();
  const overlay = document.createElement("div"); overlay.id = "h3talk-modal";
  Object.assign(overlay.style, {position:"fixed", inset:"0", background:"#000a", zIndex:100000, display:"grid", placeItems:"center"});
  const content = document.createElement("div");
  Object.assign(content.style,{width:"94vw",height:"93vh",background:"#17202c",borderRadius:"12px",position:"relative"});
  const close = document.createElement("button"); close.textContent = "关闭审核面板";
  Object.assign(close.style,{position:"absolute",right:"16px",top:"8px",zIndex:2});
  close.onclick = () => overlay.remove();
  const frame = document.createElement("iframe"); frame.src = `/h3talk/project/${pid}/panel`;
  Object.assign(frame.style,{width:"100%",height:"100%",border:0,borderRadius:"12px"});
  content.append(frame,close); overlay.append(content); document.body.append(overlay);
}

app.registerExtension({
  name:"H3LongVideo.TalkingRH",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "H3LVTalkingRH") return;
    const original = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function() {
      original?.apply(this,arguments);
      this.addWidget("button","打开分段审核",null,() => showPanel(this));
      this.addWidget("button","新建项目",null,() => {
        const widget = this.widgets.find(w => w.name === "project_id"); widget.value = "";
        app.graph.setDirtyCanvas(true,true);
        alert("已切换到新项目。运行节点后生成新的音频分段，旧项目和成片会保留。");
      });
    };
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function(info) {
      configured?.apply(this,arguments);
      // The original candidate had eight widgets and only supported RH.
      // Preserve that backend when loading its canvas, without changing new nodes.
      if (info.widgets_values?.length===8) {
        const backend=this.widgets.find(w=>w.name==="backend");
        if (backend) backend.value="RunningHub";
      }
    };
    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function(message) {
      executed?.apply(this,arguments);
      const pid = message.talking_rh_project?.[0];
      if (!pid) return;
      this.widgets.find(w => w.name === "project_id").value = pid;
      app.graph.setDirtyCanvas(true,true); showPanel(this);
    };
  },
});
