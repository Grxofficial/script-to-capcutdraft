# 发给本地 Agent 的安装提示词

适用于能够操作本机终端、文件和浏览器的 Codex、Claude Code 等 Agent。普通网页聊天机器人没有本机权限，无法代替用户完成安装。

把仓库地址替换为真实地址，然后复制整段：

```text
请安装并启动这个本地项目：
https://github.com/Grxofficial/script-to-capcutdraft

先阅读 README.md、AGENTS.md 和当前操作系统的安装脚本。使用 Python 3.11，检查 Git、FFmpeg 和 FFprobe。不要让我把 API Key 发在聊天中；请创建项目根目录的 .env，并打开或告诉我它的绝对路径，让我直接在本机填写 AUTOCUT_ARK_API_KEY。

我确认填写完成前不要调用外部模型。确认后运行 script-to-capcutdraft doctor；不要显示、读取或转述密钥。依赖和火山方舟配置通过后，运行 script-to-capcutdraft serve，自动打开只监听 127.0.0.1 的网页控制台。

不要代替我批准剪映冒烟。首次安装草稿或剪映升级时，只能创建测试草稿并让我亲自在剪映中确认三轨存在、字幕可编辑，然后再根据我的明确确认运行 smoke approve --confirmed。
```
