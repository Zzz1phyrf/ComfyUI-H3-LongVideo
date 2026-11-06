import {app} from "../../scripts/app.js";

const legacyTitles = {
  H3LVUnified: ["H3 长视频 · 音频分析与顺序生成"],
  H3LVPromptExpand: ["H3 长视频 · 分镜提示词生成器"],
  H3LVTalkingRH: ["H3 口播 · 一镜到底", "H3口播 · 一镜到底"],
};

app.registerExtension({
  name: "H3LongVideo.NodeNames",
  beforeRegisterNodeDef(nodeType, nodeData) {
    const titles = legacyTitles[nodeData.name];
    if (!titles) return;
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function() {
      const result = configured?.apply(this, arguments);
      // Migrate only former stock titles; a user's own title is authoritative.
      if (titles.includes(this.title) && typeof nodeData.display_name === "string") {
        this.title = nodeData.display_name;
      }
      return result;
    };
  },
});
