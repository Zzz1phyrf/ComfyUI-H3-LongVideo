# MiniMax H3 长视频节点开发交接

更新时间：2026-09-20

## 当前工作区

- 仓库：`D:\Codex\comfyui工作流\MiniMaxH3-LongVideo\development\ComfyUI-H3-LongVideo`
- 分支：`feature/segment-materials-expander`
- 安装目录：`D:\ComfyUI\ComfyUI-py312\ComfyUI\custom_nodes\ComfyUI-H3-LongVideo`
- 当前测试工作流：`D:\ComfyUI\ComfyUI-py312\ComfyUI\user\default\workflows\MiniMaxH3无限时长数字人工作流 By 像素幻想Lab (self lift双采)_开发测试.json`
- 远端：`https://github.com/Zzz1phyrf/ComfyUI-H3-LongVideo.git`
- 当前分支尚未推送 GitHub。

## 用户要实现的产品形态

长音频分析切分后，在审核面板为项目设置最多六张默认参考图，也可为每个分段选择“使用项目默认”或上传最多六张本段自定义图。长视频节点输出当前分段的原曲、人声、生成帧数、文件名前缀、`segment_material` 和六路动态图像；独立的 `H3LVPromptExpand` 节点读取素材包，用多图视觉模型扩写 H3 提示词。口播保持固定构图，唱歌根据本地运镜规则安排景别和运动。

## 本轮已经完成

- `cd3ea75`：增加每段素材与独立多图提示词扩写节点。
- `10c2f0c`：参考图与输出口收敛为六张；分段试听改为原曲；审核面板精简。
- `991c70b`：删除节点上的“镜头活跃度”和“最远允许景别”，景别及运镜统一由本地运镜规则控制。
- `abf363e`：删除废弃的 `segment_brief` 输出，当前节点共 11 个输出；旧队列快照自动迁移输出编号。
- `6a2c7d9`：项目默认参考图区改为默认折叠、可反复展开/收起；分段继承项目默认图时，上传按钮使用明确禁用光标，不再显示等待转圈。

运行时代码和前端文件均已复制到安装目录。按用户要求没有代替用户重启 ComfyUI；要看到 `6a2c7d9` 的前端变化，需要用户手动重启并刷新页面。

## 当前输出编号

| 编号 | 输出 |
|---:|---|
| 0 | `original_audio_padded` |
| 1 | `vocals_padded` |
| 2 | `generation_frames` |
| 3 | `filename_prefix` |
| 4 | `segment_material` |
| 5–10 | `image_1` 至 `image_6` |

控制器的旧快照迁移版本为 `output_contract_version = 3`。不要把已删除的 `segment_brief`、`camera_activity` 或 `widest_framing` 加回节点表面。

## 验证证据

- 当前 ComfyUI Python：120 项单元/契约测试通过。
- 时间轴 Node 测试：4 项通过。
- `web/h3lv.js`、`web/materials.js`：`node --check` 通过。
- 开发测试工作流的 11 个输出与所有下游连线已逐项核对。
- 安装目录重新运行 120 项测试通过。
- 尚未在重启后的真实浏览器中人工点击验收最新折叠按钮和禁用光标。

## 下一轮先做什么

1. 让用户手动重启 ComfyUI 并刷新页面。
2. 打开开发测试工作流与分段审核面板，确认“项目默认参考图”初始折叠，点击标题或“展开”后出现内容，再点击“收起”可折叠。
3. 展开任一分段，选择“使用项目默认”，确认上传按钮不可点击且光标为禁用样式；切换到“本段自定义”后恢复可点击。
4. 截图顶部曾出现 `ValueError 4 H3LVPromptExpand`。目前没有诊断证据，先在重启后重新运行并取得完整 ComfyUI 控制台错误与复现步骤，再判断是否与旧进程、旧输出编号或扩写 API 有关。
5. 继续按用户反馈做界面与流程调整；测试通过后再询问是否统一推送 GitHub。不要自行推送。

## 新对话可直接粘贴的提示词

```text
继续开发 MiniMax H3 长视频 ComfyUI 插件。先阅读：
D:\Codex\comfyui工作流\MiniMaxH3-LongVideo\development\ComfyUI-H3-LongVideo\HANDOFF.md

仓库位于：
D:\Codex\comfyui工作流\MiniMaxH3-LongVideo\development\ComfyUI-H3-LongVideo

当前分支是 feature/segment-materials-expander，安装目录和测试工作流路径都写在 HANDOFF.md。先检查 git 状态和最近提交，不要回退已有改动。ComfyUI 是否重启由我来操作；修改后同步插件文件但不要替我重启。先根据我接下来的截图和反馈继续调整，完成后运行 HANDOFF.md 中列出的测试。未经我明确要求不要推送 GitHub。
```

