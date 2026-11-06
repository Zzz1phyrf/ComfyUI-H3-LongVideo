# ComfyUI H3 Long Video

MiniMax H3 长音频分段生成插件，包含两个独立控制节点：**MiniMax H3 · MV Generator** 和 **MiniMax H3 · Talking Avatar**。两者均支持本地 / RunningHub、分段审核、生成、单段重做、预览与合并。

**仓库和安装包附带两份完整示例工作流**：[MV](examples/MiniMaxH3MV数字人工作流%20By%20像素幻想Lab.json)、[口播](examples/MiniMaxH3口播数字人工作流%20By%20像素幻想Lab.json)。两份都支持在节点选择本地 / RunningHub；RH 单段计算工作流需另行导入自己的 RH 工作台并填写新 ID。口播和 MV 的模型条件链不同，分别使用各自画布；本插件不会替你把全部生成节点封装为一个黑盒。

## 安装

1. 将插件放入 `ComfyUI/custom_nodes/ComfyUI-H3-LongVideo`，避免重复安装或多套一层目录。
2. 使用 ComfyUI 的 Python 安装 `requirements.txt`，准备 `ffmpeg`、`ffprobe`。
3. 根据自己工作流中的节点安装外部依赖，选择实际存在的模型、LoRA、CLIP、VAE 等文件。
4. 重启 ComfyUI 并刷新页面，导入 examples 中需要的一份工作流，上传自己的音频/图片。更新插件不会删除用户工作流或 output 中的项目。

RH 模式需要本地工作流涉及的节点定义，但不在本地加载 H3 生成权重。模型和采样参数以实际运行的本地画布或 RH 工作流为准。

需要 Git 安装时，在 `ComfyUI/custom_nodes` 下执行：

```bash
git clone https://github.com/Zzz1phyrf/ComfyUI-H3-LongVideo.git
```

示例模型名是参考，必须选择自己环境已有的权重。MV 使用 SelfLift-Avatar、ComfyUI-KJNodes 和 ComfyUI-YCNodes-MiniMax-H3 等画布节点；口播使用 ComfyUI 原生 H3 与标准 K 条件链。MV Prompt Expander 默认使用视觉扩写，需配置自己的扩写 API。提取人声还需要与 PyTorch 版本匹配的 `torchaudio`；首次运行可能下载人声分离和语音识别模型。

## 使用

上传完整音频；口播同时提供一张人物连场景的参考图。选择本地或 RunningHub，先准备并审核分段，保存确认后从分段面板开始生成。MV 可逐段使用不同参考图、编辑分镜与提示词；口播每段共用原始参考图。

分享口播工作流时，画布可能保存着原电脑的项目编号。如果接收方本机没有该项目，插件会提示并解除旧编号绑定，再按接收方当前音频和参考图准备新项目；不会自动选取其他历史项目。原电脑已有项目的恢复不受影响。此修复需要更新插件、重启 ComfyUI 并刷新页面。

口播新项目先统一参考图尺寸，再用同一张图作为每段首尾约束；不继承上一段生成画面。提示词保持自然说话、自然手势、固定构图及外观一致。原声作为连续成片音轨，输出 24fps。旧项目保留原保存配置，不自动切换到新策略。

MV 手动增加、移动或删除切点不再要求最短 4 秒；保留最长 15 秒（或项目设置的更小上限）及至少一个 24fps 编辑帧。口播的停顿与帧网格约束保持其自身规则。

分段失败后先查看错误；继续会复用已知任务和完成片段。单段重做保留旧成功结果，合并成功后才更新成片。修改音频边界会影响相邻片段；修改全局模型配置或参考图时按界面要求新建项目。详细说明见[口播使用](README_TALKING_RH.md)、[RH 执行与恢复](MV_RUNNINGHUB.md)。

## API 配置

两个控制节点的 **RH API 设置** 按国内/海外站保存本机 Key，两类节点共用设置；MV Prompt Expander 的扩写 API 单独配置。Key 保存在 ComfyUI 用户目录，不写入工作流。保存后可点击 **检查 RH 配置（不生成）**。

整条音频可选择直接使用或提取人声：MV 默认提取，口播默认直接使用。提取结果按项目缓存，最终成片仍使用完整原声。

## 输出与报错日志

`ComfyUI/output/H3LongVideo/MV` 和 `ComfyUI/output/H3LongVideo/口播` 均为三类目录：

- `projects`：项目、素材和内部任务记录；口播本地记录位于 `_local_tasks`。
- `final_videos`：完整成片。
- `logs`：错误日志。

点击任一节点的 **下载错误日志**，发送 `H3LongVideo-errors.log` 即可协助排查。文件汇总 MV/口播的错误阶段、项目、分段、任务、出错节点、堆栈和运行环境/插件指纹。密钥、令牌和 URL 脱敏，不打包素材或执行图。每类日志最多约 6 MB；无错误时明确提示。

## 兼容与验证

示例已清空项目绑定、素材选择和RH工作流ID，使用前填写自己的配置。已有工作流和保存的项目继续兼容。旧节点、旧任务路径、项目快照及必要的内部执行模板继续保留兼容。完整生成、合并和单段重做已有本地与 RH 测试记录；本次清理不重新生成视频，也不扩大画质验收范围。
