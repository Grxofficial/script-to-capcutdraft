---
name: script-to-capcutdraft
description: 安装、配置或运行 Script to CapCut Draft；用于把中文文案和本地视频素材生成带豆包旁白和原生字幕的可编辑剪映草稿。
---

# Script to CapCut Draft

## 安装与配置

1. 先阅读仓库根 `README.md` 和 `AGENTS.md`，按当前操作系统运行 `scripts/setup-macos.sh` 或 `scripts/setup-windows.ps1`。
2. 固定使用 Python 3.11，并检查 Git、FFmpeg、FFprobe、pymediainfo 和 pyJianYingDraft。
3. 不要求用户在聊天中发送 API Key，不读取或输出 `.env`。创建 `.env` 后请用户直接在本机填写 `AUTOCUT_ARK_API_KEY`。
4. 用户确认填写完成后运行 `script-to-capcutdraft doctor`。检查通过后运行 `script-to-capcutdraft serve`，让浏览器打开仅监听 `127.0.0.1` 的本地网页。

## 创建草稿

- 普通混剪：`script-to-capcutdraft create --script <绝对路径> --library <绝对路径> --name <名称>`。
- 白底口播：`script-to-capcutdraft compose --script <绝对路径> --name <名称>`。
- 查看既有任务优先使用 `inspect`，安装已有 staging 使用 `install`，不要因此重复调用外部模型。

## 固定边界

- 文案保持原字原序，只决定断点。
- 只用用户指定的本地素材。
- 视频原声静音，只生成视频、旁白和原生字幕三轨；不加转场、BGM、SFX、滤镜，不渲染最终 MP4。
- 保留 `.autocut/ai-cache` 和 `voice/*.source.wav` 以支持断点重试。
- 未通过当前系统与剪映版本冒烟时只生成 staging。
- `smoke approve --confirmed` 只能在用户明确确认真实草稿能打开、三轨存在且字幕可编辑后执行。
