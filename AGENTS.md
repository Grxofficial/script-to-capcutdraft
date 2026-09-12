# Script to CapCut Draft Agent 约定

- 修改前先读 `README.md` 和相关 `docs/`。
- 正式运行使用 Python 3.11。
- 不读取、显示、提交或让用户在聊天中发送 `.env` 密钥。
- 缺少密钥时，创建 `.env` 并让用户直接在本机填写 `AUTOCUT_ARK_API_KEY`。
- 配置完成后运行 `script-to-capcutdraft doctor`；检查通过后可运行 `script-to-capcutdraft serve` 打开本地网页。
- 网页只能监听 `127.0.0.1`。
- 文案不改写，只决定断点；只使用用户指定的本地素材。
- 固定输出视频、旁白、原生字幕三轨；素材原声静音，不加转场、BGM、SFX、滤镜，不渲染最终 MP4。
- 不删除 `.autocut/ai-cache` 或 `voice/*.source.wav` 来解决重试问题。
- 安装或替换草稿前让用户完全退出剪映；不得绕过版本冒烟门禁。
- `smoke approve --confirmed` 只能在用户明确确认真实检查通过后执行。
- Windows 代码或模拟测试不等于 Windows 剪映实机验收。
