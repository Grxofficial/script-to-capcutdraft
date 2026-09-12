# 运行与故障处理

## 初始化

macOS：

```bash
bash scripts/setup-macos.sh
```

Windows PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-windows.ps1
```

安装脚本创建 `.venv`、`config.toml` 和 `.env`。用户只在本机 `.env` 中填写：

```dotenv
AUTOCUT_ARK_API_KEY=
```

然后运行：

```bash
script-to-capcutdraft doctor
```

`doctor` 不会输出密钥。Python、FFmpeg、FFprobe、pyJianYingDraft、Agent Plan 配置和剪映草稿路径应按当前用途准备完毕；TransNetV2 不可用时会回退到 FFmpeg 切镜。

## 网页控制台

```bash
script-to-capcutdraft serve
```

浏览器自动打开 `http://127.0.0.1:8420`。控制台没有公网认证，只允许本机访问。

## 单条生产

```bash
script-to-capcutdraft create \
  --script /absolute/path/script.txt \
  --library /absolute/path/video-library \
  --name demo
```

成功后检查 `jobs/demo/result.json`、`plan.json` 和 `report.md`。低置信度是人工替换建议，不代表整条任务失败。

## 白底口播

```bash
script-to-capcutdraft compose \
  --script /absolute/path/script.txt \
  --name demo-white \
  --voice zh_female_xiaohe_uranus_bigtts
```

该模式不使用素材库和镜头匹配，视频轨用白底图逐句对齐旁白。加 `--no-install` 只生成 staging。

## Markdown 批量

每篇文案之间用独立的 `---` 分隔：

```bash
script-to-capcutdraft batch \
  --scripts /absolute/path/scripts.md \
  --library /absolute/path/video-library \
  --name-prefix batch
```

程序按原文跳过已有 `result.json` 的任务，失败条目可直接重跑并复用 AI 与 TTS 缓存。

## 剪映安装

安装或替换草稿前完全退出剪映。如果任务已生成 staging：

```bash
script-to-capcutdraft install jobs/<任务名>
```

`install` 不会重新调用模型。程序会先备份注册信息，同名替换失败时回滚。

## 常见问题

### 外部接口超时

网络恢复后重跑原命令，不要删除 `.autocut/ai-cache` 或 `voice/*.source.wav`。重排超时会降级为本地向量顺序并写入报告。

### 音频字头或字尾被切

当前只裁句首和句尾，两端保留 40ms，不删除句内停顿。豆包 MP3 默认阈值为 `-45dB`。调整处理参数后可从 `.source.wav` 重做，无需重新请求 TTS。

### 拆句不理想

拆句模型返回后必须通过原文一致性校验，模型改写会被拒绝并回退到规则拆分。切分结果进入本地 AI 缓存。

### 剪映正在运行

CLI 不主动强杀剪映，只生成 staging。网页控制台的“退出剪映”由用户主动点击触发。冒烟门禁始终有效。

## 验证

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

真实草稿还需人工核对：仅视频/旁白/原生字幕三轨，视频原声为 0，无转场，媒体位于草稿 `Resources`，字幕可以编辑。
