# MiniMax H3 · Talking Avatar

同一口播节点默认本地生成，可选RunningHub。两者共用完整音频分段、试听审核、空间约束、动作续接、裁切和最终合并。歌曲视频使用MV Generator及配套MV Prompt Expander，旧MV节点的口播入口已移除。当前为测试候选，GitHub尚未推送。

## 本地生成

1. 在独立测试用ComfyUI中只加载一份本插件，安装requirements.txt，让FFmpeg、FFprobe可从PATH访问。本地生成还需要SelfLift-Avatar、KJ、YC MiniMax-H3及相应原生H3节点。
2. 导入examples/05_H3口播_一镜到底.json，加载完整音频、带背景的人物主图、人物活动遮罩。灰度遮罩经ImageToMask的red通道连接person_mask。
3. backend选择“本地”，明确选择local_model、local_clip、local_video_vae、local_audio_vae、local_upscaler，需要时选local_lora。仅使用ComfyUI已有权重。本地不填写workflow_id，不需要RH Key或网络，失败时不会自动转到收费云端。
4. 运行节点仅准备项目。审核面板中试听、微调，保存切点后再次试听实际对齐落点，再点“确认分段”“开始/继续生成”。
5. 各段在当前ComfyUI原生队列执行，后段使用前段实际保留尾部的原生latent续接；完成后自动裁切合并。面板可播放成片，重新运行同项目节点可刷新final_video输出路径。

模型与可选LoRA是显式配置，基础8步需与所选模型匹配。本地不会自动下载、融合或替换权重；没有云端同款权重时，只能分别验证效果，不能声称两边画质一致。

本轮验证使用[MATLOWAI的融合模型](https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot)：`minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors`，`local_lora`选择“不使用”。该文件已融合Turbo；本轮采用它与RH对齐模型组合。此前本机hybrid b20-49／FL2VA pruned配单独Turbo 8step的替代组合出现闭嘴，文件可加载不等于口型已验证，不能当作等价替换。

本地短片测试显式采用首段6秒、后续5.667秒、seed999，实际切点5.875秒；这是测试配置，不是所有硬件的显存保证。长首段、完整音频和更多素材仍需逐项复核。人物活动MASK仍需手动提供。

local_clip_read默认“原生”。本机实测遇到Windows大CLIP文件映射读取时原生崩溃，因此提供“内存”方式：仅此口播图的CLIP分支从原文件逐张量读取相同字节，保留dtype和元数据，不修改全局CLIPLoader、Avatar或权重文件，可能占用更多系统内存。没有此问题时保留原生读取。

选择“内存”时，同一进程仅保留一份对应文件的CLIP实例供各口播项目复用，避免准备新项目使队列缓存淘汰后重复加载32B编码器。文件路径、大小或修改时间改变时替换，重启后释放；即使队列空闲也可能保留约25GB系统内存。原生读取模式不使用这份缓存。

## 可选RunningHub

backend选择“RunningHub”，填写有权调用的workflow_id，选择海外/国内站与实例类型；local_*权重选项不参与此模式，本机无需H3模型。启动ComfyUI的进程环境需要配置自己的RUNNINGHUB_API_KEY，例如PowerShell：

```powershell
$rhSecret = Read-Host 'RunningHub API Key' -AsSecureString
$env:RUNNINGHUB_API_KEY = [Net.NetworkCredential]::new('', $rhSecret).Password
# 接着在此终端中启动ComfyUI
```

Key不进入节点或工作流。RH侧需要defaults/talking_rh_graph.json所用的H3、Avatar、LMS、KJ、LTX节点及权重；每个独立任务显式上传该段音频、参考图、遮罩和上段latent，不依赖RH保持同一进程或临时目录。控制节点不需要上传到RH；云端用现有节点。本地做相同的分段审核和最终合并，确认后点击开始才创建付费RH任务。

## 人物遮罩和续接

白色允许人物及手势活动，黑色保留原图背景，边缘留柔和过渡，包住头发、肩膀、双手及预期活动余量。首版仍需每张主图手动提供一次MASK，各段复用；未封装自动人物分割。

MASK可与主图或最终画面同尺寸。1.0 MP/9:16为768×1376，0.8为672×1216，0.6为608×1056；与主图同尺寸时按相同中心裁切映射。近白/近黑内部值规范为纯白/纯黑，保留羽化过渡。

两种后端都保留原版SelfLift-Avatar、音频零噪声遮罩、LMS、lowres_scale0.8、基础8步、transition5、rho0和固定提示词。没有K采样生成或付费扩写。单主图首段同图首尾，后段Ref2VA＋39帧对应的12个原生latent token续接。reference_2至reference_6保留多图条件；多图首段使用Ref2VA，其完整片画质尚未验收。

目标时长仅为声学建议。切点按H3续接网格对齐，最大偏移约0.354秒，须试听实际落点。首段非末段至少2.333秒、最多13.667秒，后段最多12.75秒。末段保留全部有效音频样本；尾部token索引由实际保留区间计算。

## 暂停与恢复

后端、图像、遮罩、模型或配置改变后，点击“新建项目”再运行，旧项目与片段保留。改切点使变化位置及后续依赖段失效，原文件仍保留。“暂停后续生成”会收集当前段，再停止提交下一段。

项目在输出目录H3TalkingRH/projects，成片在H3TalkingRH/final_videos。旧目录名和内部节点ID仅为兼容首版保留，不代表只能用RH。

本地先落盘prompt_id再入队，完成记录持久保存。若采样时重启使任务从ComfyUI队列/历史消失，会明确停止并保留文件，检查后手动重试，不自动重排。

RH中断恢复和下载失败均查询同一taskId。创建结果未知时不自动重建，先核实RH任务列表，再在异常恢复处绑定已有ID。明确失败才手动重试。旧RH候选画布导入时保留RH后端和项目ID。

## 验证边界

此前RH单主图1.0MP男女完整片获整体稳定性正面反馈；首版封装已真实验证男女各两段、暂停/恢复与插件合并。新增本地后端的验证单独记录，不能用RH结果替代本地画质验收，不承诺零劣化或任意素材一镜到底。

候选包不含Key、模型、用户素材、实验视频或开发机路径。跨机器安装及多参考图完整片画质仍待验证。
