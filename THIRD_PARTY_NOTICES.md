# 第三方软件与来源声明

本项目自身采用 Apache License 2.0。以下组件或项目不因被本项目使用而改变其原有许可证。

## 运行依赖

### pyJianYingDraft

- 仓库：https://github.com/GuanYixuan/pyJianYingDraft
- 固定提交：`c3318066d964744e2bfc66f75c71745fe8cea52a`
- 版本：`0.3.0`
- 许可证：Apache License 2.0
- 用途：创建剪映视频、音频和原生文本轨草稿结构。

### transnetv2-pytorch（可选）

- 仓库：https://github.com/soCzech/TransNetV2
- Python 包：`transnetv2-pytorch==1.0.5`
- 许可证：MIT
- 用途：本地镜头边界检测。

本仓库不单独提交 TransNetV2 权重；安装可选依赖时，程序优先使用 `transnetv2-pytorch` 包内提供的权重。重新分发独立模型文件前应再次核对来源和许可。

## 参考和改写来源

### FireRed-OpenStoryline

- 仓库：https://github.com/FireRedTeam/FireRed-OpenStoryline
- 许可证：Apache License 2.0
- 用途：镜头切分、关键帧和结构化媒体理解的架构参考。

### video-shotcraft

- 仓库：https://github.com/Vincentwei1021/video-shotcraft
- 许可证：Apache License 2.0
- 用途：草稿媒体打包、注册、原子备份和失败回滚逻辑的参考与改写来源。

## 系统工具与外部服务

FFmpeg、FFprobe、MediaInfo、剪映专业版及火山方舟 Agent Plan 均不是本项目的一部分。用户需要自行安装、开通并遵守各自许可证或服务条款。本项目不分发剪映客户端、剪映素材或第三方 API Key。
