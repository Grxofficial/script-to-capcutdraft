# Windows 安装与首次验收

## 前置依赖

- Windows 10 或 11
- Python 3.11 与 Python Launcher
- Git for Windows
- FFmpeg 和 FFprobe，均已加入 `PATH`
- 剪映专业版

## 初始化

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-windows.ps1
```

在项目根目录 `.env` 填写 `AUTOCUT_ARK_API_KEY`，然后运行：

```powershell
.\.venv\Scripts\script-to-capcutdraft.exe doctor
```

默认草稿目录候选为：

```text
%LOCALAPPDATA%/JianyingPro/User Data/Projects/com.lveditor.draft
```

非标准路径可在 `config.toml` 的 `[draft]` 中使用正斜杠绝对路径覆盖。无法定位剪映 EXE 时，可在 `.env` 增加 `AUTOCUT_JIANYING_EXE=C:/path/to/JianyingPro.exe`。

## 第一次冒烟

先启动一次剪映，让它完成初始化，再完全退出：

```powershell
.\.venv\Scripts\script-to-capcutdraft.exe smoke create
```

重新打开剪映，人工确认测试草稿：

- 可以打开，没有损坏、丢媒体或权限提示；
- 视频、旁白、字幕三条轨道存在；
- 字幕可以直接修改。

确认后再次退出剪映：

```powershell
.\.venv\Scripts\script-to-capcutdraft.exe smoke approve --confirmed
.\.venv\Scripts\script-to-capcutdraft.exe smoke restore
```

冒烟授权绑定 Windows 和具体剪映版本，不能继承另一台机器或 macOS 的结果。

## 启动网页

```powershell
.\.venv\Scripts\script-to-capcutdraft.exe serve
```

Windows 适配代码提供路径、版本、进程、资源打包、注册、备份和回滚，但真实兼容性仍以目标机器的人工冒烟为准。
