"""macOS 剪映草稿适配、媒体打包和可回滚安装。

实现思路来自 Vincentwei1021/video-shotcraft 的 jianying-export/mac_draft.py，
并按本项目的版本级冒烟门禁重新整理。上游使用 Apache License 2.0。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from .errors import DraftCompatibilityError


def default_draft_root() -> Path:
    """macOS 剪映专业版的默认草稿根目录。"""
    return Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft"


def browse_roots() -> list[dict[str, str]]:
    """网页文件夹选择器的 macOS 快捷入口。"""
    roots = [{"name": "主目录", "path": str(Path.home())}]
    volumes = Path("/Volumes")
    if volumes.is_dir():
        roots.append({"name": "外置卷", "path": str(volumes)})
    roots.append({"name": "磁盘根目录", "path": "/"})
    return roots


def current_jianying_version() -> str:
    plist = Path("/Applications/VideoFusion-macOS.app/Contents/Info.plist")
    if not plist.exists():
        return ""
    process = subprocess.run([
        "/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(plist),
    ], capture_output=True, text=True)
    return process.stdout.strip() if process.returncode == 0 else ""


def jianying_running() -> bool:
    process = subprocess.run(
        [
            "pgrep", "-f", "-i",
            r"^(/Applications/VideoFusion-macOS\.app/Contents/MacOS/VideoFusion-macOS|.*/JianyingPro)( |$)",
        ],
        capture_output=True, text=True,
    )
    return bool(process.stdout.strip())


def open_jianying() -> dict[str, Any]:
    """网页按钮由用户主动触发：通过 launchd 打开剪映。"""
    if jianying_running():
        return {"ok": True, "method": "already-running", "message": "剪映本来就在运行"}
    app = Path("/Applications/VideoFusion-macOS.app")
    if not app.is_dir():
        return {"ok": False, "method": "open", "message": "没有找到剪映专业版"}
    subprocess.Popen(["open", "-a", str(app)])
    return {"ok": True, "method": "open", "message": "已启动剪映"}


def quit_jianying() -> dict[str, Any]:
    """用户主动退出剪映：先正常退出，再逐级结束进程。"""
    if not jianying_running():
        return {"ok": True, "method": "already-quit", "message": "剪映本来就没在运行"}

    subprocess.run(
        ["osascript", "-e", 'tell application "VideoFusion-macOS" to quit'],
        capture_output=True, text=True, timeout=10,
    )
    for _ in range(20):
        if not jianying_running():
            return {"ok": True, "method": "quit", "message": "已让剪映正常退出"}
        time.sleep(0.5)

    pattern = "VideoFusion-macOS.app/Contents/MacOS/VideoFusion-macOS"
    subprocess.run(["pkill", "-TERM", "-f", pattern], capture_output=True)
    for _ in range(6):
        if not jianying_running():
            return {"ok": True, "method": "term", "message": "已请求剪映退出（SIGTERM）"}
        time.sleep(0.5)

    subprocess.run(["pkill", "-KILL", "-f", pattern], capture_output=True)
    time.sleep(1.0)
    if jianying_running():
        return {"ok": False, "method": "kill", "message": "强制结束失败，请手动处理"}
    return {"ok": True, "method": "kill", "message": "已强制结束剪映"}


def _validate_name(name: str, root: Path) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or Path(name).is_absolute():
        raise ValueError(f"非法草稿名：{name!r}")
    resolved = (root / name).resolve()
    if resolved.parent != root.resolve():
        raise ValueError(f"草稿名逃逸草稿根目录：{name!r}")
    return root / name


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _platform_from_donor(root: Path) -> dict[str, Any] | None:
    for path in sorted(root.glob("*/draft_info.json")):
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        platform = content.get("platform", {})
        if platform.get("os") == "mac" and platform.get("device_id"):
            return {
                key: platform[key] for key in
                ("os", "app_version", "device_id", "hard_disk_id", "mac_address")
                if key in platform
            }
    return None


def _name_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _unique_name(name: str, used: set[str]) -> str:
    if _name_key(name) not in used:
        return name
    stem, suffix = os.path.splitext(name)
    index = 2
    while _name_key(f"{stem}-{index}{suffix}") in used:
        index += 1
    return f"{stem}-{index}{suffix}"


def _bundle_media(content: dict[str, Any], draft_dir: Path, final_dir: Path) -> int:
    resources = draft_dir / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    used: set[str] = set()
    total = 0
    for kind in ("videos", "audios"):
        for material in content.get("materials", {}).get(kind, []):
            source = Path(material["path"]).expanduser().resolve()
            if not source.exists():
                bundled = resources / source.name
                expected_parent = (final_dir / "Resources").resolve()
                if source.parent == expected_parent and bundled.exists():
                    source = bundled.resolve()
                else:
                    raise FileNotFoundError(f"草稿素材不存在：{source}")
            source_key = str(source)
            if source_key not in mapping:
                name = _unique_name(source.name, used)
                used.add(_name_key(name))
                target = resources / name
                if source != target.resolve():
                    shutil.copy2(source, target)
                total += target.stat().st_size
                mapping[source_key] = str(final_dir / "Resources" / name)
            material["path"] = mapping[source_key]
    return total


def _material_records(content: dict[str, Any], now_us: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for kind, default_type in (("videos", "video"), ("audios", "music")):
        for material in content.get("materials", {}).get(kind, []):
            records.append({
                "create_time": now_us // 1_000_000,
                "duration": material.get("duration", 0),
                "extra_info": material.get("material_name") or Path(material["path"]).name,
                "file_Path": material["path"],
                "height": material.get("height", 0),
                "id": uuid.uuid4().hex,
                "import_time": now_us // 1_000_000,
                "import_time_ms": now_us,
                "item_source": 1,
                "md5": "",
                "metetype": "photo" if material.get("type") == "photo" else default_type,
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": material.get("width", 0),
            })
    return records


def macify(draft_dir: Path, name: str, draft_root: Path, allow_missing_fingerprint: bool) -> dict[str, Any]:
    target = _validate_name(name, draft_root)
    content_path = draft_dir / "draft_content.json"
    meta_path = draft_dir / "draft_meta_info.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    platform = _platform_from_donor(draft_root)
    if platform is None:
        if not allow_missing_fingerprint:
            raise DraftCompatibilityError("未找到明文设备指纹；只有冒烟测试可以走无指纹路径")
        platform = {
            "os": "mac",
            "app_version": current_jianying_version() or "11.3.0",
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


def _registry_entry(info: dict[str, Any], name: str, root: Path) -> dict[str, Any]:
    path = Path(info["fold_path"])
    return {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": str(path / "draft_cover.jpg"),
        "draft_fold_path": str(path),
        "draft_id": info["draft_id"],
        "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_invisible": False,
        "draft_is_pippit_draft": False,
        "draft_is_web_article_video": False,
        "draft_json_file": str(path / "draft_info.json"),
        "draft_name": name,
        "draft_new_version": "",
        "draft_root_path": str(root),
        "draft_timeline_materials_size": info["materials_size"],
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "pippit_avatar_url": "",
        "pippit_extra_info": "",
        "pippit_id": "",
        "pippit_user_name": "",
        "streaming_edit_draft_ready": True,
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": info["tm"],
        "tm_draft_modified": info["tm"],
        "tm_draft_removed": 0,
        "tm_duration": info["duration"],
    }


def install(draft_dir: Path, name: str, draft_root: Path, info: dict[str, Any]) -> Path:
    if jianying_running():
        raise DraftCompatibilityError("剪映正在运行，请完全退出（Cmd+Q）后重试")
    target = _validate_name(name, draft_root)
    root_meta = draft_root / "root_meta_info.json"
    root_data = json.loads(root_meta.read_text(encoding="utf-8"))
    backup = root_meta.with_name(root_meta.name + time.strftime(".%Y%m%d-%H%M%S.bak"))
    shutil.copy2(root_meta, backup)
    trash = draft_root / ".autocut-trash"
    trash.mkdir(exist_ok=True)
    old: Path | None = None
    if target.exists():
        old = trash / f"{name}.replaced-{time.strftime('%Y%m%d-%H%M%S')}"
        os.rename(target, old)
    try:
        shutil.move(str(draft_dir), str(target))
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
                shutil.move(str(target), str(draft_dir))
        if old and old.exists():
            os.rename(old, target)
        shutil.copy2(backup, root_meta)
        raise
    if old:
        shutil.rmtree(old, ignore_errors=True)
    return backup


def uninstall(name: str, draft_root: Path) -> None:
    if jianying_running():
        raise DraftCompatibilityError("剪映正在运行，请完全退出（Cmd+Q）后重试")
    target = _validate_name(name, draft_root)
    root_meta = draft_root / "root_meta_info.json"
    root_data = json.loads(root_meta.read_text(encoding="utf-8"))
    backup = root_meta.with_name(root_meta.name + time.strftime(".%Y%m%d-%H%M%S.bak"))
    shutil.copy2(root_meta, backup)
    temporary = draft_root / ".autocut-trash" / f"{name}.uninstall-{int(time.time())}"
    temporary.parent.mkdir(exist_ok=True)
    if target.exists():
        os.rename(target, temporary)
    try:
        root_data["all_draft_store"] = [
            item for item in root_data.get("all_draft_store", [])
            if item.get("draft_name") != name
        ]
        _write_json_atomic(root_meta, root_data)
    except BaseException:
        if temporary.exists():
            os.rename(temporary, target)
        raise
    shutil.rmtree(temporary, ignore_errors=True)


def smoke_is_approved(state_path: Path) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(state.get("passed")) and state.get("app_version") == current_jianying_version()


def approve_smoke(state_path: Path) -> None:
    version = current_jianying_version()
    if not version:
        raise DraftCompatibilityError("没有找到剪映专业版")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(state_path, {
        "passed": True,
        "app_version": version,
        "approved_at": int(time.time()),
        "scope": "视频、音频、原生文本三轨无指纹明文草稿安装",
    })


def require_smoke(state_path: Path) -> None:
    if not smoke_is_approved(state_path):
        raise DraftCompatibilityError(
            f"剪映 {current_jianying_version() or '未知版本'} 尚未通过冒烟测试。"
            "请先运行 script-to-capcutdraft smoke create，人工检查后运行 "
            "script-to-capcutdraft smoke approve --confirmed"
        )
