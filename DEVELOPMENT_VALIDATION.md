# 开发版验证记录 · 2026-09-19

## 当前结果

- 基于合并后的 `2c1361d`，分支 `feature/segment-materials-expander`，尚未推送 GitHub。
- 118 项 unittest 通过；所有新增前端模块通过 Node 语法检查。
- 本机 ComfyUI 成功注册 `H3LVUnified` 和 `H3LVPromptExpand`。
- 真实 OFOX `qwen/qwen3.8-flash` 多图扩写：双图唱歌、单图空镜、单图口播均成功；六段结构、图片序号、字幕约束和两个 `N/A` 音频字段均有检查。
- 发现并修复 Qwen 将生成额度耗在思考而正文为空的问题：请求显式关闭思考，同时保留空正文和截断检查。部分模型使用 Markdown 标题，已做标题格式归一化；不补造缺失内容。
- 实际 HTTP 预览命中与运行节点相同的缓存键；编辑提示词后保存成功，原审批失效，重新确认成功。
- 真实 selflift 双采样出片三段，保持原工作流模型、LoRA 0.7、6 步基础调度及 selflift 配置。两图人物演唱→单图空镜成功切换；口播一张图也完成。
- 插件自动合成唱歌与空镜为 8 秒视频成功。原项目、素材、生成文件和原始工作流均保留。

## 本地证据

- 桌面 `C:\Users\A\Desktop\H3新版节点测试_20260919`：三段约4秒视频、8秒自动合成视频、对应真实扩写文本和说明。
- 测试工作流：`D:\ComfyUI\ComfyUI-py312\ComfyUI\user\default\workflows\MiniMaxH3无限时长数字人工作流 By 像素幻想Lab (self lift双采)_开发测试.json`。
- 唱歌测试项目：`ad4ffdb6de9b4fd3bfffd966e56f465e`；口播：`d32b5c48bed34c9883de02fb3a414c33`。
- 工作区 `diagnostics/dev-smoke-projects.json`、`diagnostics/expansion-*.txt`、`diagnostics/dev-video-review/` 保存调用与抽帧证据，不包含 API 密钥。
- 原版插件归档：工作区 `diagnostics/stable-plugin-2c1361d.zip`。

## 明确尚未验收的部分

- 浏览器工具访问本地 ComfyUI 被 `net::ERR_BLOCKED_BY_CLIENT` 拦截，未进行真实点击上传、拖动排序、模型选择等界面操作验收。接口测试不能替代这些检查。
- 每段仅查看了 0.3、2、3.6 秒的图像帧：背景、衣服颜色、空镜无人、口播机位及无新增字幕在这些帧中符合预期。没有正常速度完整播放及听音验收，不宣称口型同步或整段无字幕已经验收。
- 测试复用了已有项目音频前8秒，以两段4秒构造开发测试项目；未重新跑整曲识别/分离。
- 本次全部沿用 selflift 工作流横屏分辨率 1056×608；口播原图为竖图，因此生成背景会扩展，不能将它当作原图边界完全一致的验收。
- 发布前仍需人工操作审核界面并检查样片。当前主分支保持稳定版。
