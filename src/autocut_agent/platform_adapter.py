"""剪映操作系统适配入口。

生产流水线只依赖本模块，不再直接区分 macOS/Windows。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from .errors import DraftCompatibilityError


def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return sys.platform


def _backend() -> ModuleType:
    if sys.platform == "darwin":
        from . import mac_install
        return mac_install
    if sys.platform == "win32":
        from . import windows_install
        return windows_install
    raise DraftCompatibilityError(f"尚未支持当前操作系统：{sys.platform}")


def resolve_draft_root(configured: str) -> Path:
    if not configured.strip() or configured.strip().casefold() == "auto":
        return _backend().default_draft_root().resolve()
    return Path(configured).expanduser().resolve()


def browse_roots() -> list[dict[str, str]]:
    return _backend().browse_roots()


def current_jianying_version() -> str:
    return _backend().current_jianying_version()


def jianying_running() -> bool:
    return _backend().jianying_running()


def quit_jianying() -> dict[str, Any]:
    return _backend().quit_jianying()


def open_jianying() -> dict[str, Any]:
    return _backend().open_jianying()


def prepare_draft(
    draft_dir: Path,
    name: str,
    draft_root: Path,
    allow_missing_fingerprint: bool,
) -> dict[str, Any]:
    backend = _backend()
    if sys.platform == "darwin":
        return backend.macify(draft_dir, name, draft_root, allow_missing_fingerprint)
    return backend.windowsify(draft_dir, name, draft_root, allow_missing_fingerprint)


def install(draft_dir: Path, name: str, draft_root: Path, info: dict[str, Any]) -> Path:
    return _backend().install(draft_dir, name, draft_root, info)


def uninstall(name: str, draft_root: Path) -> None:
    _backend().uninstall(name, draft_root)


def smoke_is_approved(state_path: Path) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    same_version = bool(state.get("passed")) and state.get("app_version") == current_jianying_version()
    if not same_version:
        return False
    saved_platform = state.get("platform")
    # 兼容当前已经人工验证的 macOS 旧状态文件。
    return saved_platform == platform_name() or (saved_platform is None and sys.platform == "darwin")


def approve_smoke(state_path: Path) -> None:
    version = current_jianying_version()
    if not version:
        raise DraftCompatibilityError("没有找到剪映专业版")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(state_path.suffix + ".tmp")
    temporary.write_text(json.dumps({
        "passed": True,
        "platform": platform_name(),
        "app_version": version,
        "approved_at": int(time.time()),
        "scope": "视频、音频、原生文本三轨无指纹明文草稿安装",
    }, ensure_ascii=False), encoding="utf-8")
    temporary.replace(state_path)


def require_smoke(state_path: Path) -> None:
    if not smoke_is_approved(state_path):
        raise DraftCompatibilityError(
            f"{platform_name()} 上的剪映 {current_jianying_version() or '未知版本'} 尚未通过冒烟测试。"
            "请先运行 script-to-capcutdraft smoke create，人工检查后运行 "
            "script-to-capcutdraft smoke approve --confirmed"
        )
