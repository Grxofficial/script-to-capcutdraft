# 架构与数据流

更新日期：2026-09-08

## 目标

`script-to-capcutdraft` 是一个可脱离 Codex、Claude Code 等 Agent 独立运行的本地编排器。用户直接运行 CLI 或网页控制台时，外部推理统一走火山方舟 Agent Plan。

## 主数据流

### 素材混剪（`create` / `batch`）

```text
中文文案 / Markdown 文案集
        |
        v
LLM 语义拆句（校验不改写，失败回退逗号级规则）
        |                          拆分后删除段内标点与 emoji
        v
豆包 TTS 逐句配音 -----> 保守首尾裁边（MP3 源用 -45dB）
        |                          |
        |                          v
        |                    真实音频时长
        v
火山文案向量 ---> SQLite 镜头向量召回 ---> Lite 候选重排
                                                   |
                                                   v
                                           无循环时间线计划
                                                   |
                                                   v
                              pyJianYingDraft 三轨草稿 + Resources 打包
                                                   |
                                                   v
                                冒烟门禁 + 原子备份 + 剪映注册
```

### 白底口播（`compose`）

与混剪共用「拆句 → 配音 → 草稿 → 安装」链路，但**没有素材库和镜头匹配**：视频轨整条用纯白底图（ffmpeg lavfi 生成的 1080×1920 PNG），每段时长逐句对齐旁白音频。适合先审文案节奏、后期再替换素材。

### 网页控制台（`serve`）

标准库 `http.server` 实现的本地单用户控制台（仅绑定 127.0.0.1，零第三方 web 依赖）。前端 `webui.html` 每次请求现读，改界面无需重启；任务在后台线程执行 `create_job` / `create_white_job`，进度写入 `WebJob` 由前端轮询。提供剪映状态检查、用户主动的"退出剪映"（AppleScript 优雅退出 → SIGTERM → SIGKILL 三级递进）和被门禁拦下后的重试安装。

素材索引是独立的增量流程：FFprobe 读取媒体参数，TransNetV2（失败时 FFmpeg 回退）切镜，抽取首/中/尾关键帧，由视觉模型生成结构化画面事实，再向量化写入 SQLite。

## 组件职责

| 组件 | 职责 |
|---|---|
| `script.py` | LLM 语义拆句（带原文归一化校验，改写即拒收）+ 逗号级规则兜底；段文本去标点/emoji |
| `media.py` + `db.py` | 素材指纹、切镜、关键帧、视觉标签、向量和 SQLite 缓存 |
| `tts.py` | 豆包语音合成 2.0 逐句配音、原音缓存、仅首尾的安全裁边 |
| `matching.py` | 向量召回、重复/同源/文字污染惩罚、LLM 重排、时间线规划 |
| `draft.py` | 构建视频/旁白/字幕三轨（支持白底图 photo 段）并校验禁用素材 |
| `compose.py` | 白底图生成与白底口播任务编排 |
| `platform_adapter.py` | 统一选择 macOS/Windows 剪映后端，隔离操作系统逻辑 |
| `mac_install.py` / `windows_install.py` | 媒体打包、剪映运行检测、注册表备份、同名替换和回滚 |
| `pipeline.py` | 混剪任务编排、任务产物落盘与 staging 安装 |
| `batch.py` | Markdown 拆分、原文去重、逐条执行、失败隔离和断点状态 |
| `webapp.py` + `webui.html` | 本地网页控制台：任务提交、进度轮询、剪映状态与退出、文件夹浏览 |

## 缓存与失效

- `.autocut/library.sqlite3`：按素材库、文件指纹、分析器版本和模型保存镜头索引。
- `.autocut/ai-cache/*.json`：按接口和完整请求哈希保存外部 AI 响应（含语义拆句结果）。模型名或请求变化会自动失效。
- `.autocut/web-scripts/*.txt`：网页控制台提交的文案副本。
- `jobs/<name>/voice/*.source.wav`：豆包 MP3 解码后的原始 WAV，是调整本地裁边策略时的起点。
- `jobs/<name>/voice/*.wav`：实际进入草稿的音频。处理参数参与 `processing_key`，变更后只本地重做，不重新请求 TTS。

## 时间线与安全约束

- 每句旁白的 FFprobe 时长向下取到毫秒，避免四舍五入超出媒体真实时长。
- 每个视频镜头默认跳过 10 帧头部空档，时长不足时换下一候选，不循环播放。
- 重排服务失败时，当句使用本地向量顺序并写告警，整任务不失败。
- 豆包 MP3 解码有本底噪声，裁边阈值默认 `-45dB`。两端各留 40ms，句内停顿保留。
- 安装前必须通过当前剪映版本的人工冒烟验收，且剪映主程序已退出。

## 批次状态机

`batch` 按原 Markdown 顺序编号，每条状态为：

- `skipped_existing`：原文已有带 `result.json` 的任务。
- `installed`：草稿已生成并安装。
- `generated`：草稿已生成，但安装被剪映运行或冒烟门禁阻止。
- `failed`：当条失败；整批继续。

状态在每条结束后原子写入 `jobs/_batches/`。重跑同一命令时，已完成原文被跳过，失败或中断任务重进流程并复用已有缓存。
