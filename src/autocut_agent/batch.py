"""Markdown 多文案的可断点续跑批量生产。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable

from .config import AppConfig
from .errors import AutocutError
from .pipeline import create_job, safe_name


def split_markdown_scripts(text: str) -> list[str]:
    """以独立的 Markdown `---` 为分隔符，保留每条内容和行序。"""
    blocks = re.split(r"(?m)^\s*---\s*$", text.replace("\r\n", "\n").replace("\r", "\n"))
    return [block.strip() for block in blocks if block.strip()]


def normalized_script(text: str) -> str:
    """只忽略行首尾空白，用于识别已完成的同一篇文案。"""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _existing_scripts(jobs_dir: Path) -> set[str]:
    existing: set[str] = set()
    for path in jobs_dir.glob("*/script.original.txt"):
        # 只有已生成 result.json 的任务才算完成；保留中间产物的
        # 失败任务必须允许再次进入 create_job，由配音和 AI 缓存断点续跑。
        if not (path.parent / "result.json").is_file():
            continue
        try:
            existing.add(normalized_script(path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return existing


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def create_batch(
    config: AppConfig,
    markdown_path: Path,
    library_path: Path,
    name_prefix: str,
    progress: Callable[[str], None] = print,
) -> dict[str, object]:
    """逐条生成草稿；已完成原文自动跳过，单条失败不中断整批。"""
    source = markdown_path.expanduser().resolve()
    library = library_path.expanduser().resolve()
    if not source.is_file():
        raise AutocutError(f"批量文案不存在：{source}")
    if not library.is_dir():
        raise AutocutError(f"素材库不存在：{library}")
    scripts = split_markdown_scripts(source.read_text(encoding="utf-8"))
    if not scripts:
        raise AutocutError("批量文案中没有可用内容")

    batch_name = safe_name(source.stem)
    input_dir = config.root / "inputs" / "batches" / batch_name
    state_path = config.jobs_dir / "_batches" / f"{batch_name}.json"
    input_dir.mkdir(parents=True, exist_ok=True)
    existing = _existing_scripts(config.jobs_dir)
    items: list[dict[str, object]] = []
    state: dict[str, object] = {
        "source": str(source),
        "library": str(library),
        "name_prefix": name_prefix,
        "total": len(scripts),
        "items": items,
    }

    for index, script in enumerate(scripts, start=1):
        name = safe_name(f"{name_prefix}-{index:02d}")
        normalized = normalized_script(script)
        if normalized in existing:
            item = {"index": index, "name": name, "status": "skipped_existing"}
            items.append(item)
            progress(f"[{index:02d}/{len(scripts):02d}] 跳过已完成文案：{name}")
            _write_state(state_path, state)
            continue

        script_path = input_dir / f"{index:02d}.txt"
        script_path.write_text(script + "\n", encoding="utf-8")
        progress(f"[{index:02d}/{len(scripts):02d}] 开始：{name}")
        try:
            result = create_job(config, script_path, library, name, progress)
        except (AutocutError, OSError, ValueError, RuntimeError) as exc:
            item = {"index": index, "name": name, "status": "failed", "error": str(exc)}
            progress(f"[{index:02d}/{len(scripts):02d}] 失败：{name}：{exc}")
        else:
            status = "installed" if result.get("installed") else "generated"
            item = {"index": index, "name": name, "status": status, "result": result}
            existing.add(normalized)
            progress(f"[{index:02d}/{len(scripts):02d}] 完成：{name}（{status}）")
        items.append(item)
        _write_state(state_path, state)

    counts = {
        status: sum(item.get("status") == status for item in items)
        for status in ("installed", "generated", "skipped_existing", "failed")
    }
    state["counts"] = counts
    state["state_path"] = str(state_path)
    _write_state(state_path, state)
    return state
