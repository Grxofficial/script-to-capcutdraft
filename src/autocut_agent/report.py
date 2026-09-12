"""任务计划、字幕和可读报告输出。"""

from __future__ import annotations

import json
from pathlib import Path

from .models import JobPlan


def write_artifacts(plan: JobPlan, job_dir: Path, original_script: str) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "script.original.txt").write_text(original_script, encoding="utf-8")
    (job_dir / "plan.json").write_text(
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (job_dir / "subtitles.srt").write_text(to_srt(plan), encoding="utf-8")
    (job_dir / "report.md").write_text(to_markdown(plan), encoding="utf-8")


def to_srt(plan: JobPlan) -> str:
    lines: list[str] = []
    for unit in plan.units:
        lines.extend([
            str(unit.index),
            f"{_srt_time(unit.timeline_start)} --> {_srt_time(unit.timeline_start + unit.duration)}",
            unit.text,
            "",
        ])
    return "\n".join(lines)


def _srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_markdown(plan: JobPlan) -> str:
    lines = [
        f"# {plan.name} 匹配报告",
        "",
        f"- 总时长：{plan.duration:.3f} 秒",
        f"- 文案单元：{len(plan.units)}",
    ]
    if isinstance(plan.library_path, list):
        lines.append(f"- 素材文件夹：{len(plan.library_path)} 个")
        lines.extend(f"  - `{path}`" for path in plan.library_path)
    else:
        library_label = plan.library_path or "白底图（无素材匹配）"
        lines.append(f"- 素材库：`{library_label}`")
    lines.extend(["- 红色警告标记：`🔴 建议人工替换`", ""])
    for unit in plan.units:
        lines.extend([
            f"## {unit.index}. {unit.text}",
            "",
            f"旁白：`{unit.timeline_start:.3f}s → {unit.timeline_start + unit.duration:.3f}s`",
            "",
        ])
        for clip in unit.clips:
            warning = " 🔴 建议人工替换" if clip.low_confidence else ""
            lines.append(
                f"- `{Path(clip.source_path).name}` "
                f"`{clip.source_start:.3f}s + {clip.source_duration:.3f}s` "
                f"得分 `{clip.score:.4f}`{warning}；{clip.reason}"
            )
        if unit.warnings:
            lines.extend(["", *[f"> 🔴 {warning}" for warning in unit.warnings]])
        lines.append("")
    if plan.warnings:
        lines.extend(["## 汇总警告", "", *[f"- 🔴 {warning}" for warning in plan.warnings], ""])
    return "\n".join(lines)
