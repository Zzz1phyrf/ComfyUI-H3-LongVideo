# 安装与问题反馈

安装包包含插件运行文件、说明和两份完整示例，**不包含模型、测试素材或 API Key**。导入 examples 中的口播或 MV 工作流，选择自己的素材并配置对应模型/API；两者分别配置本地和 RH 后端，不混用云端条件链。

将插件放入 `ComfyUI/custom_nodes`，用 ComfyUI 的 Python 安装 `requirements.txt`，准备 FFmpeg，并安装自己画布需要的外部节点。更新后重启 ComfyUI 并刷新页面。不要删除用户工作流、用户配置或 output 项目。

先检查音频、参考图、模型和分辨率，准备并审核分段，再开始生成。可在分段面板暂停、继续、重做单段或重新合并。RH 配置检查不会创建生成任务。

反馈问题时，点击节点 **下载错误日志**，发送 `H3LongVideo-errors.log`，附复现步骤、所选本地/RH、显卡显存、分辨率，以及可公开的异常截图或片段。不要发送 API Key 或用户目录中的密钥配置。

操作详见 [主说明](README.md)、[口播说明](README_TALKING_RH.md)、[RH 执行与恢复](MV_RUNNINGHUB.md)。
