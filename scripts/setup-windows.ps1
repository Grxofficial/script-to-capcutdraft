$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "缺少 $Name。$Hint"
    }
}

Require-Command "py.exe" "请先安装 Python 3.11，并勾选 Python Launcher。"
Require-Command "git.exe" "请先安装 Git for Windows。"
Require-Command "ffmpeg.exe" "请先安装 FFmpeg 并加入 PATH。"
Require-Command "ffprobe.exe" "请先安装 FFmpeg 并加入 PATH。"

$Version = & py.exe -3.11 -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
if ($LASTEXITCODE -ne 0 -or $Version.Trim() -ne "3.11") {
    throw "未找到 Python 3.11。请运行 py -0p 检查已安装版本。"
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    & py.exe -3.11 -m venv (Join-Path $ProjectRoot ".venv")
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -e ".[scene]"

if (-not (Test-Path (Join-Path $ProjectRoot "config.toml"))) {
    Copy-Item (Join-Path $ProjectRoot "config.example.toml") (Join-Path $ProjectRoot "config.toml")
}
if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
    Copy-Item (Join-Path $ProjectRoot ".env.example") (Join-Path $ProjectRoot ".env")
    Write-Warning "已创建 .env。请直接在本机填入 AUTOCUT_ARK_API_KEY，不要把 Key 发到聊天或提交到 Git。"
}

$Command = Join-Path $ProjectRoot ".venv\Scripts\script-to-capcutdraft.exe"
& $Command --project-root $ProjectRoot doctor
Write-Host "Windows 环境初始化完成。填写 .env 后重新运行 doctor，再运行 script-to-capcutdraft serve。" -ForegroundColor Green
