"""端到端自动混剪编排。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from .ai import OpenAICompatibleClient
from .config import AppConfig
from .db import LibraryDB
from .draft import build_draft, validate_draft
from .errors import AutocutError, DraftCompatibilityError
from .platform_adapter import (
    install,
    jianying_running,
    prepare_draft,
    require_smoke,
    resolve_draft_root,
    smoke_is_approved,
)
from .matching import TimelinePlanner
from .media import MediaIndexer
from .report import write_artifacts
from .script import split_units
from .tts import DoubaoTTS


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", value).strip(" .-")
    return cleaned[:80] or "未命名"


def install_staged_draft(
    draft_dir: Path,
    draft_name: str,
    config: AppConfig,
    progress: Callable[[str], None] = print,
) -> dict[str, object]:
    """把 staging 草稿安装进剪映；被兼容门禁拦下时返回 install_blocked 而不抛错。"""
    result: dict[str, object] = {}
    try:
        if config.draft.require_smoke_approval:
            require_smoke(config.compatibility_path)
        draft_root = resolve_draft_root(config.draft.draft_root)
        info = prepare_draft(
            draft_dir, draft_name, draft_root,
            allow_missing_fingerprint=smoke_is_approved(config.compatibility_path),
        )
        backup = install(draft_dir, draft_name, draft_root, info)
    except DraftCompatibilityError as exc:
        result["install_blocked"] = str(exc)
        progress(f"草稿已生成但未安装：{exc}")
    else:
        result.update({
            "installed": True,
            "installed_draft": info["fold_path"],
            "registry_backup": str(backup),
        })
        progress(f"已安装到剪映：{info['fold_path']}")
    return result


def create_job(
    config: AppConfig,
    script_path: Path,
    library_path: Path | list[Path],
    name: str,
    progress: Callable[[str], None] = print,
) -> dict[str, object]:
    config.require_ai()
    config.require_doubao_tts()
    script_file = script_path.expanduser().resolve()
    raw_libraries = [library_path] if isinstance(library_path, Path) else library_path
    libraries = list(dict.fromkeys(path.expanduser().resolve() for path in raw_libraries))
    if not script_file.is_file():
        raise AutocutError(f"文案文件不存在：{script_file}")
    if not libraries:
        raise AutocutError("至少需要一个素材文件夹")
    for library in libraries:
        if not library.is_dir():
            raise AutocutError(f"素材库目录不存在：{library}")
    original = script_file.read_text(encoding="utf-8").strip()
    ai = OpenAICompatibleClient(config.ai, config.state_dir / "ai-cache")
    progress("语义拆分文案")
    units = split_units(original, ai)
    if not units:
        raise AutocutError("文案文件没有可用文字")
    draft_name = safe_name(config.draft.name_prefix + name)
    job_dir = config.jobs_dir / safe_name(name)
    job_dir.mkdir(parents=True, exist_ok=True)

    progress(f"更新素材索引（{len(libraries)} 个文件夹）")
    roots: list[dict[str, object]] = []
    totals = {"scanned": 0, "indexed": 0, "skipped": 0, "failed": 0, "pruned": 0}
    indexer = MediaIndexer(config, ai, progress)
    for library in libraries:
        stats = indexer.run(library, allow_empty=True)
        roots.append({"path": str(library), **stats})
        for key in totals:
            totals[key] += int(stats.get(key, 0))
    index_stats: dict[str, object] = {**totals, "libraries": len(libraries), "roots": roots}
    with LibraryDB(config.database_path) as database:
        clips = database.clips_for_libraries(libraries)
    if not clips:
        raise AutocutError("索引完成后仍没有可用镜头，请运行 inspect 检查失败项")

    progress("生成逐句旁白")
    DoubaoTTS(config.doubao_tts, progress).synthesize_units(units, job_dir / "voice")
    progress("匹配镜头并规划时间线")
    plan = TimelinePlanner(config.matching, ai).create_plan(
        draft_name,
        str(script_file),
        str(libraries[0]) if len(libraries) == 1 else [str(path) for path in libraries],
        units,
        clips,
        {"width": config.canvas.width, "height": config.canvas.height, "fps": config.canvas.fps},
    )
    write_artifacts(plan, job_dir, original)
    progress("生成三轨剪映草稿")
    draft_dir = build_draft(plan, job_dir, config)
    validation = validate_draft(draft_dir, plan)
    result: dict[str, object] = {
        "job_dir": str(job_dir),
        "draft_dir": str(draft_dir),
        "report": str(job_dir / "report.md"),
        "index": index_stats,
        "validation": validation,
        "installed": False,
    }
    if config.draft.auto_install:
        result.update(install_staged_draft(draft_dir, draft_name, config, progress))
    (job_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result


def install_job(config: AppConfig, job_dir: Path) -> dict[str, object]:
    """安装已经生成的 staging 草稿，不重新调用任何模型。"""
    job = job_dir.expanduser().resolve()
    result_path = job / "result.json"
    if not result_path.is_file():
        raise AutocutError(f"任务缺少 result.json：{result_path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("installed"):
        installed = Path(str(result.get("installed_draft", "")))
        if installed.is_dir():
            return result
    draft_dir = Path(str(result.get("draft_dir", ""))).expanduser().resolve()
    if not draft_dir.is_dir():
        raise AutocutError(f"待安装草稿不存在：{draft_dir}")

    require_smoke(config.compatibility_path)
    if jianying_running():
        raise DraftCompatibilityError("剪映正在运行，请完全退出后重试")
    draft_root = resolve_draft_root(config.draft.draft_root)
    draft_name = draft_dir.name
    info = prepare_draft(
        draft_dir,
        draft_name,
        draft_root,
        allow_missing_fingerprint=smoke_is_approved(config.compatibility_path),
    )
    backup = install(draft_dir, draft_name, draft_root, info)
    result.update({
        "installed": True,
        "installed_draft": info["fold_path"],
        "registry_backup": str(backup),
    })
    result.pop("install_blocked", None)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
