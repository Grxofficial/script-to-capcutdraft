#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"

for command_name in python3.11 git ffmpeg ffprobe; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "缺少 $command_name，请先安装后重试。" >&2
    exit 2
  fi
done

if [ ! -x ".venv/bin/python" ]; then
  python3.11 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[scene]'

if [ ! -f config.toml ]; then
  cp config.example.toml config.toml
fi
if [ ! -f .env ]; then
  cp .env.example .env
fi

echo "安装完成。"
echo "请在本机填写：$project_root/.env"
echo "填入 AUTOCUT_ARK_API_KEY 后运行："
echo "  .venv/bin/script-to-capcutdraft doctor"
echo "  .venv/bin/script-to-capcutdraft serve"
