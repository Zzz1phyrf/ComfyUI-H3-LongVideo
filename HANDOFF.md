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

- 修复新空白画布下的素材界面状态不一致：新分析项目现在默认启用内置素材，未生成过的旧草稿打开时也会自动迁移，不再依赖长视频节点的图片输出是否已接线。
- 项目默认参考图区保留默认收起的展开/收起功能；折叠现在只影响这个默认素材区的显示，分段的“参考图来源 / 本段画面 / 手写提示词”始终独立可用。
- 保留的旧版分段素材界面在画布没有 `ref_image` 槽位时会直接禁用上传按钮，不再允许点击后弹出“操作失败”。
- `7c1241f`：新增音频阶段感知的导演简报、人物氛围表演模式、扩写规则上下文和手写提示词直通输出。
- 新增音频语义映射：长前奏可覆盖连续多段，新唱歌项目对疑似前奏/间奏/尾奏默认使用“人物氛围表演”，而不是固定人物演唱；用户仍可切换为人物演唱或空镜。
- 镜头简报改为“画面任务 / 声音关系 / 开场构图 / 主体动作 / 镜头运动 / 结束构图”六行导演简报，并保留逐段编辑。
- 扩写素材包和系统规则增加音频阶段、画面类型、生成帧数与 24fps 生成时长；人物演唱/口播才使用人声参考，人物氛围与空镜均不要求口型。
- 长视频节点在 `image_6` 后追加 `segment_prompt`。审核面板新增逐段手写提示词输入，接口原样输出，不扩写、不校验、不回退；默认工作流连线仍是 `H3LVPromptExpand → PreviewAny → H3 prompt`。

- `cd3ea75`：增加每段素材与独立多图提示词扩写节点。
- `10c2f0c`：参考图与输出口收敛为六张；分段试听改为原曲；审核面板精简。
- `991c70b`：删除节点上的“镜头活跃度”和“最远允许景别”，景别及运镜统一由本地运镜规则控制。
- `abf363e`：删除废弃的 `segment_brief` 输出，当前节点共 11 个输出；旧队列快照自动迁移输出编号。
- `6a2c7d9`：项目默认参考图区改为默认折叠、可反复展开/收起；分段继承项目默认图时，上传按钮使用明确禁用光标，不再显示等待转圈。

运行时代码、前端与测试均已复制到安装目录。按用户要求没有代替用户重启 ComfyUI；下次启动后才会加载本轮节点输出与前端变化。

## 当前输出编号

| 编号 | 输出 |
|---:|---|
| 0 | `original_audio_padded` |
| 1 | `vocals_padded` |
| 2 | `generation_frames` |
| 3 | `filename_prefix` |
| 4 | `segment_material` |
| 5–10 | `image_1` 至 `image_6` |
| 11 | `segment_prompt`（手写提示词原样输出，默认不连接） |

控制器的旧快照迁移版本为 `output_contract_version = 3`。不要把已删除的 `segment_brief`、`camera_activity` 或 `widest_framing` 加回节点表面。

## 验证证据

- 当前 ComfyUI Python：124 项单元/契约测试通过。
- 时间轴 Node 测试：4 项通过。
- `web/h3lv.js`、`web/materials.js`、`web/expansion.js`：`node --check` 通过。
- 开发测试工作流共 12 个输出，新增 `segment_prompt` 未连接；默认扩写连线仍指向 H3 `prompt`。
- 安装目录已同步本轮修改，文件哈希与仓库一致；重新运行 124 项 Python 测试、4 项时间轴测试及三份前端语法检查，全部通过。
- 尚未在重启后的真实浏览器中人工点击验收本轮的折叠显示、分段独立状态与自动迁移。

## 下一轮先做什么

1. 让用户手动启动 ComfyUI 并刷新页面。
2. 打开开发测试工作流，确认长视频节点末尾出现未连接的 `segment_prompt`，原 `H3LVPromptExpand → PreviewAny → H3 prompt` 连线不变。
3. 新分析一段含长前奏的歌曲，确认所有被前奏覆盖的分段默认显示“人物氛围表演”，试听后再判断声学建议是否准确。
4. 确认导演简报六行字段可编辑；填写手写提示词、保存确认后，临时改接 `segment_prompt → H3 prompt`，核对文本原样到达 H3。测试后恢复默认扩写连线。
5. 验收项目默认参考图区默认收起且可反复展开，收起时分段素材控件仍立即可见，以及空白画布上传图片不再走 `ref_image` 接线报错；截图顶部曾出现的 `ValueError 4 H3LVPromptExpand` 仍需在新进程中取得完整控制台错误与复现步骤。
6. 未经用户明确要求不要推送 GitHub。

## 新对话可直接粘贴的提示词

```text
继续开发 MiniMax H3 长视频 ComfyUI 插件。先阅读：
D:\Codex\comfyui工作流\MiniMaxH3-LongVideo\development\ComfyUI-H3-LongVideo\HANDOFF.md

仓库位于：
D:\Codex\comfyui工作流\MiniMaxH3-LongVideo\development\ComfyUI-H3-LongVideo

当前分支是 feature/segment-materials-expander，安装目录和测试工作流路径都写在 HANDOFF.md。先检查 git 状态和最近提交，不要回退已有改动。ComfyUI 是否重启由我来操作；修改后同步插件文件但不要替我重启。先根据我接下来的截图和反馈继续调整，完成后运行 HANDOFF.md 中列出的测试。未经我明确要求不要推送 GitHub。
```
