<div align="center">

# Script to CapCut Draft

把一篇文案和一堆本地素材，变成一份可以继续修改的剪映草稿。

[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![macOS](https://img.shields.io/badge/macOS-supported-111111?logo=apple)](docs/operator-runbook.md)
[![Windows](https://img.shields.io/badge/Windows-adapter-0078D4?logo=windows)](docs/windows-runbook.md)
[![License](https://img.shields.io/badge/License-Apache--2.0-D22128)](LICENSE)

</div>

这个项目起源于一个很具体的需求：我已经写好了文案，也拍了一批素材，只想让 AI 帮我把旁白、镜头和字幕先铺好，然后回到剪映里做最后调整。

给它一篇中文文案和一个本地视频目录，它会分析素材、生成豆包旁白，再按句子挑选镜头。完成后得到一份正常的剪映工程，你可以继续移动镜头、改字幕、调时间。

```text
中文文案 + 本地视频素材
          ↓
   拆句 · 配音 · 选镜头
          ↓
画面轨 + 旁白轨 + 原生字幕轨
          ↓
       剪映草稿
```

默认输出竖屏 `1080 × 1920 / 30fps`。素材原声静音，画面直接拼接，字幕仍然可以在剪映里逐句修改。每次任务还会留下 `plan.json` 和 `report.md`，方便回头检查某句话到底用了哪段素材。

> 项目面向中国大陆版剪映专业版。首次安装或升级剪映后，需要先完成一次本机冒烟验证。国际版 CapCut 留待后续适配。

## 最快的开始方式

如果你在用 Codex、Claude Code 或其他能操作本机终端的 Agent，直接把下面这段发给它：

```text
请帮我安装并启动这个项目：
https://github.com/Grxofficial/script-to-capcutdraft

先阅读 README.md 和 AGENTS.md，再执行适合当前操作系统的安装脚本。项目使用 Python 3.11，需要 Git、FFmpeg 和 FFprobe。

在项目根目录创建被 Git 忽略的 .env，让我亲自在本机填写 AUTOCUT_ARK_API_KEY。不要要求我在聊天里发送 API Key，也不要打印它。

我确认填写完成后，运行 script-to-capcutdraft doctor。检查通过后运行 script-to-capcutdraft serve，打开本地网页控制台。

网页只监听 127.0.0.1。首次安装草稿或剪映升级后，先创建冒烟草稿，让我亲自在剪映里确认视频、旁白、字幕三轨和可编辑字幕，再执行 approve。
```

[单独打开这段安装提示词](docs/agent-install-prompt.md)

## 自己安装

需要准备：

- Python 3.11
- Git
- FFmpeg 和 FFprobe
- 剪映专业版
- 火山方舟 Agent Plan API Key

macOS：

```bash
git clone https://github.com/Grxofficial/script-to-capcutdraft.git
cd script-to-capcutdraft
bash scripts/setup-macos.sh
```

Windows PowerShell：

```powershell
git clone https://github.com/Grxofficial/script-to-capcutdraft.git
cd script-to-capcutdraft
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-windows.ps1
```

安装脚本会准备虚拟环境和配置模板。接着打开项目根目录里的 `.env`，填入自己的 Key：

```dotenv
AUTOCUT_ARK_API_KEY=your_agent_plan_key_here
```

检查环境，然后打开网页：

```bash
script-to-capcutdraft doctor
script-to-capcutdraft serve
```

浏览器会进入 `http://127.0.0.1:8420`。在页面里粘贴文案，可添加一个或多个素材文件夹；右侧会先抽取一条素材作为样片，实际镜头仍按文案语义匹配。

## 命令行

喜欢终端的话，常用命令只有这几个：

```bash
# 建立或更新素材索引
script-to-capcutdraft index --library /absolute/path/to/video-library

# 用文案和素材创建混剪草稿
script-to-capcutdraft create \
  --script examples/demo-script.txt \
  --library /absolute/path/to/video-library \
  --name demo

# 创建白底口播草稿
script-to-capcutdraft compose \
  --script examples/demo-script.txt \
  --name demo-white
```

第一次使用，或者刚升级过剪映，先做一次 6 秒冒烟测试：

```bash
script-to-capcutdraft smoke create
# 在剪映里打开测试草稿，亲自检查三条轨道和字幕
script-to-capcutdraft smoke approve --confirmed
```

这份确认按“操作系统 + 剪映版本”保存。换电脑或升级剪映后再检查一次。

## 它怎么选镜头

项目会先用 TransNetV2 找出镜头边界，再从每个镜头抽取关键帧。多模态模型负责理解画面，向量模型召回与文案相关的候选，语言模型做最后排序。旁白生成后，时间轴按音频时长铺设。

当前默认使用火山方舟 Agent Plan：

| 工作 | 默认模型或接口 |
|---|---|
| 关键帧多模态理解（VLM） | `glm-5.3-flash` / Responses API |
| 文案与镜头向量 | `doubao-embedding-vision` / Embeddings API |
| 拆句与候选排序 | `doubao-seed-2-0-lite` / Chat Completions API |
| 中文旁白 | 豆包语音合成 2.0 / `seed-tts-2.0` |
| 默认音色 | `zh_female_xiaohe_uranus_bigtts` |

这些配置都在 `config.toml`，可以集中修改。已经分析过的素材和模型响应会缓存在本机，重复运行时会尽量复用。

## 数据会发到哪里

视频原文件留在本机。建立素材索引时，每个镜头会抽取三张关键帧并发送给配置的多模态模型；文案、候选镜头描述和配音文字会发送给火山方舟。这些 API 请求会使用你的 Agent Plan 额度。

`.env`、素材索引、缓存、音视频、任务记录和剪映草稿都保存在本机，并已排除在 Git 之外。网页控制台只绑定 `127.0.0.1`。

项目提供 macOS 和 Windows 草稿安装适配。兼容许可按“操作系统 + 剪映版本”保存在当前机器上，每台新机器都需要由使用者完成首次冒烟验证。

[操作手册](docs/operator-runbook.md) · [Windows 迁移](docs/windows-runbook.md) · [模型配置](docs/providers.md) · [隐私说明](docs/privacy.md) · [项目结构](docs/architecture.md)

## 这个项目站在谁的肩膀上

草稿生成离不开 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)。镜头切分和媒体理解参考了 [FireRed-OpenStoryline](https://github.com/FireRedTeam/FireRed-OpenStoryline)，草稿打包、注册、备份和回滚参考了 [video-shotcraft](https://github.com/Vincentwei1021/video-shotcraft)。感谢这些项目把代码公开出来。

具体版本、采用方式和许可证归属写在 [NOTICE](NOTICE) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 中。

本项目使用 [Apache License 2.0](LICENSE)。Script to CapCut Draft 是独立的第三方开源项目，与剪映、CapCut、字节跳动、火山引擎及相关公司没有隶属、合作或官方授权关系。产品名称和商标归各自权利人所有。

## 开发

```bash
python -m unittest discover -s tests -v
```

想提交代码可以先看 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请查看 [SECURITY.md](SECURITY.md)。
