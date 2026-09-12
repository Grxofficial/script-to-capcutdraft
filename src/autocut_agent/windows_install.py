"""Windows 剪映草稿适配、媒体打包和可回滚安装。

草稿 JSON 结构与 macOS 适配共用，但平台字段、进程管理和路径
都只从当前 Windows 机器获取。首次正式安装仍必须通过人工冒烟门禁。
"""

from __future__ import annotations

import json
import os
import shutil
import string
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .errors import DraftCompatibilityError
from .mac_install import (
    _bundle_media,
    _material_records,
    _registry_entry,
    _validate_name,
    _write_json_atomic,
)


def default_draft_root() -> Path:
    """Windows 剪映专业版常用草稿目录，仍可在 config.toml 覆盖。"""
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    return local / "JianyingPro/User Data/Projects/com.lveditor.draft"


def browse_roots() -> list[dict[str, str]]:
    roots = [{"name": "主目录", "path": str(Path.home())}]
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        if drive.is_dir():
            roots.append({"name": f"{letter}: 盘", "path": str(drive)})
    return roots


def _executable_candidates() -> list[Path]:
    values: list[Path] = []
    configured = os.environ.get("AUTOCUT_JIANYING_EXE", "").strip()
    if configured:
        values.append(Path(configured))
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    values.extend([
        local / "JianyingPro/Apps/JianyingPro.exe",
        local / "JianyingPro/JianyingPro.exe",
    ])
    apps = local / "JianyingPro/Apps"
    if apps.is_dir():
        values.extend(sorted(apps.glob("*/JianyingPro.exe"), reverse=True))
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        root = os.environ.get(variable, "").strip()
        if root:
            values.append(Path(root) / "JianyingPro/JianyingPro.exe")
    return values


def find_jianying_executable() -> Path | None:
    for candidate in _executable_candidates():
        if candidate.is_file():
            return candidate.resolve()
    return None


def current_jianying_version() -> str:
    executable = find_jianying_executable()
    if executable is None:
        return ""
    escaped = str(executable).replace("'", "''")
    try:
        process = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                f"(Get-Item -LiteralPath '{escaped}').VersionInfo.ProductVersion",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if process.returncode != 0:
        return ""
    return process.stdout.strip().splitlines()[0] if process.stdout.strip() else ""


def jianying_running() -> bool:
    try:
        process = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq JianyingPro.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = process.stdout.casefold()
    return process.returncode == 0 and '"jianyingpro.exe"' in output


def _wait_until_quit(attempts: int, interval: float) -> bool:
    for _ in range(attempts):
        if not jianying_running():
            return True
        time.sleep(interval)
    return False


def quit_jianying() -> dict[str, Any]:
    """网页按钮由用户主动触发：先请求关闭主窗口，再使用 taskkill。"""
    if not jianying_running():
        return {"ok": True, "method": "already-quit", "message": "剪映本来就没在运行"}
    command = (
        "$p=Get-Process -Name JianyingPro -ErrorAction SilentlyContinue;"
        "if($p){$p | ForEach-Object {$null=$_.CloseMainWindow()}}"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=10,
    )
    if _wait_until_quit(20, 0.5):
        return {"ok": True, "method": "quit", "message": "已让剪映正常退出"}

    subprocess.run(
        ["taskkill.exe", "/IM", "JianyingPro.exe", "/T"],
        capture_output=True, text=True, timeout=10,
    )
    if _wait_until_quit(6, 0.5):
        return {"ok": True, "method": "term", "message": "已请求剪映退出"}

    subprocess.run(
        ["taskkill.exe", "/F", "/IM", "JianyingPro.exe", "/T"],
        capture_output=True, text=True, timeout=10,
    )
    if _wait_until_quit(2, 0.5):
        return {"ok": True, "method": "kill", "message": "已强制结束剪映"}
    return {"ok": False, "method": "kill", "message": "强制结束失败，请手动处理"}


def _platform_from_donor(root: Path) -> dict[str, Any] | None:
    for path in sorted(root.glob("*/draft_info.json")):
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        platform = content.get("platform", {})
        if str(platform.get("os", "")).casefold() in {"windows", "win"} and platform.get("device_id"):
            return {
                key: platform[key] for key in
                ("os", "app_version", "device_id", "hard_disk_id", "mac_address")
                if key in platform
            }
    return None


def windowsify(
    draft_dir: Path,
    name: str,
    draft_root: Path,
    allow_missing_fingerprint: bool,
) -> dict[str, Any]:
    target = _validate_name(name, draft_root)
    content_path = draft_dir / "draft_content.json"
    meta_path = draft_dir / "draft_meta_info.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    platform = _platform_from_donor(draft_root)
    if platform is None:
        if not allow_missing_fingerprint:
            raise DraftCompatibilityError("未找到 Windows 明文设备指纹；只有冒烟测试可以走无指纹路径")
        platform = {
            "os": "windows",
            "app_version": current_jianying_version() or "unknown",
        }
    size = _bundle_media(content, draft_dir, target)
    for key in ("platform", "last_modified_platform"):
        content[key] = {**content.get(key, {}), **platform}
    now_us = int(time.time() * 1_000_000)
    records = _material_records(content, now_us)
    meta.update({
        "draft_fold_path": str(target),
        "draft_root_path": str(draft_root),
        "draft_name": name,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_duration": content.get("duration", 0),
        "draft_timeline_materials_size_": size,
    })
    meta.pop("draft_is_ai_translate", None)
    for group in meta.get("draft_materials", []):
        if group.get("type") == 0:
            group["value"] = records
    videos = content.get("materials", {}).get("videos", [])
    if videos:
        cover_source = draft_dir / "Resources" / Path(videos[0]["path"]).name
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(cover_source),
            "-frames:v", "1", str(draft_dir / "draft_cover.jpg"),
        ], check=False)
    _write_json_atomic(content_path, content)
    _write_json_atomic(draft_dir / "draft_info.json", content)
    _write_json_atomic(meta_path, meta)
    return {
        "draft_id": meta.get("draft_id") or str(uuid.uuid4()).upper(),
        "fold_path": str(target),
        "duration": content.get("duration", 0),
        "materials_size": size,
        "tm": now_us,
    }


def _retry(action: Any, description: str) -> None:
    last: OSError | None = None
    for attempt in range(6):
        try:
            action()
            return
        except OSError as exc:
            last = exc
            time.sleep(0.15 * (attempt + 1))
    raise DraftCompatibilityError(f"{description}：{last}")


def install(draft_dir: Path, name: str, draft_root: Path, info: dict[str, Any]) -> Path:
    if jianying_running():
        raise DraftCompatibilityError("剪映正在运行，请完全退出后重试")
    target = _validate_name(name, draft_root)
    root_meta = draft_root / "root_meta_info.json"
    if not root_meta.is_file():
        raise DraftCompatibilityError(f"剪映草稿注册表不存在：{root_meta}")
    root_data = json.loads(root_meta.read_text(encoding="utf-8"))
    backup = root_meta.with_name(root_meta.name + time.strftime(".%Y%m%d-%H%M%S.bak"))
    shutil.copy2(root_meta, backup)
    trash = draft_root / ".autocut-trash"
    trash.mkdir(exist_ok=True)
    old: Path | None = None
    if target.exists():
        old = trash / f"{name}.replaced-{time.strftime('%Y%m%d-%H%M%S')}"
        _retry(lambda: os.replace(target, old), "备份同名旧草稿失败")
    try:
        _retry(lambda: shutil.move(str(draft_dir), str(target)), "移入新草稿失败")
        root_data["all_draft_store"] = [
            item for item in root_data.get("all_draft_store", [])
            if item.get("draft_name") != name
        ]
        root_data["all_draft_store"].insert(0, _registry_entry(info, name, draft_root))
        _write_json_atomic(root_meta, root_data)
    except BaseException:
        if target.exists():
            if draft_dir.exists():
                shutil.rmtree(target, ignore_errors=True)
            else:
                _retry(lambda: shutil.move(str(target), str(draft_dir)), "恢复 staging 草稿失败")
        if old and old.exists():
            _retry(lambda: os.replace(old, target), "恢复同名旧草稿失败")
        shutil.copy2(backup, root_meta)
        raise
    if old:
        shutil.rmtree(old, ignore_errors=True)
    return backup


def uninstall(name: str, draft_root: Path) -> None:
    if jianying_running():
        raise DraftCompatibilityError("剪映正在运行，请完全退出后重试")
    target = _validate_name(name, draft_root)
    root_meta = draft_root / "root_meta_info.json"
    root_data = json.loads(root_meta.read_text(encoding="utf-8"))
    backup = root_meta.with_name(root_meta.name + time.strftime(".%Y%m%d-%H%M%S.bak"))
    shutil.copy2(root_meta, backup)
    temporary = draft_root / ".autocut-trash" / f"{name}.uninstall-{int(time.time())}"
    temporary.parent.mkdir(exist_ok=True)
    if target.exists():
        _retry(lambda: os.replace(target, temporary), "移除冒烟草稿失败")
    try:
        root_data["all_draft_store"] = [
            item for item in root_data.get("all_draft_store", [])
            if item.get("draft_name") != name
        ]
        _write_json_atomic(root_meta, root_data)
    except BaseException:
        if temporary.exists():
            _retry(lambda: os.replace(temporary, target), "回滚冒烟草稿失败")
        raise
    shutil.rmtree(temporary, ignore_errors=True)
