"""白底图时间线：无素材库、无镜头匹配的口播草稿入口。

视频轨整条用纯白底图铺满，时长逐句对齐旁白音频；旁白轨和字幕轨与普通任务一致。
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Callable

from .config import AppConfig
from .draft import build_draft, validate_draft
from .errors import ExternalServiceError
from .models import JobPlan, TimelineUnit
from .pipeline import install_staged_draft, safe_name
from .report import write_artifacts
from .script import split_units
from .tts import DoubaoTTS


def make_white_image(output: Path, width: int, height: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp.png")
    process = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=white:s={width}x{height}",
        "-frames:v", "1", str(temporary),
    ], capture_output=True, text=True)
    if process.returncode != 0 or not temporary.exists():
        temporary.unlink(missing_ok=True)
        raise ExternalServiceError(f"白底图生成失败：{process.stderr[-300:]}")
    temporary.replace(output)


def create_white_job(
    config: AppConfig,
    script_path: Path,
    name: str,
    speaker: str | None = None,
    install: bool = True,
    progress: Callable[[str], None] = print,
) -> dict[str, object]:
    config.require_doubao_tts()

    script_file = script_path.expanduser().resolve()
    if not script_file.is_file():
        raise FileNotFoundError(f"文案文件不存在：{script_file}")
    original = script_file.read_text(encoding="utf-8").strip()
    # 配置了 AI 时用 LLM 语义拆句（带原文校验），否则回退逗号级规则
    ai_client = None
    if config.ai.api_key and config.ai.rerank_model:
        from .ai import OpenAICompatibleClient
        ai_client = OpenAICompatibleClient(config.ai, config.state_dir / "ai-cache")
    progress("语义拆分文案" if ai_client else "规则拆分文案")
    units = split_units(original, ai_client)
    if not units:
        raise ValueError("文案文件没有可用文字")
    draft_name = safe_name(config.draft.name_prefix + name)
    job_dir = config.jobs_dir / safe_name(name)
    job_dir.mkdir(parents=True, exist_ok=True)

    white = job_dir / "assets" / f"white_{config.canvas.width}x{config.canvas.height}.png"
    if not white.exists():
        progress("生成白底图")
        make_white_image(white, config.canvas.width, config.canvas.height)

    progress("生成逐句旁白")
    tts_config = replace(config.doubao_tts, speaker=speaker) if speaker else config.doubao_tts
    DoubaoTTS(tts_config, progress).synthesize_units(units, job_dir / "voice")

    progress("规划白底图时间线")
    timeline = 0.0
    plan_units: list[TimelineUnit] = []
    for unit in units:
        duration = math.floor(unit.audio_duration * 1000) / 1000
        plan_units.append(TimelineUnit(
            index=unit.index,
            text=unit.text,
            audio_path=unit.audio_path,
            timeline_start=round(timeline, 3),
            duration=round(duration, 3),
        ))
        timeline = round(timeline + duration, 3)
    plan = JobPlan(
        name=draft_name,
        script_path=str(script_file),
        library_path="",
        canvas={"width": config.canvas.width, "height": config.canvas.height, "fps": config.canvas.fps},
        units=plan_units,
    )
    write_artifacts(plan, job_dir, original)
    progress("生成三轨剪映草稿")
    draft_dir = build_draft(plan, job_dir, config, white_image=white)
    validation = validate_draft(draft_dir, plan, expected_video_segments=len(plan_units))
    result: dict[str, object] = {
        "job_dir": str(job_dir),
        "draft_dir": str(draft_dir),
        "report": str(job_dir / "report.md"),
        "tts_provider": "doubao-agent-plan",
        "speaker": speaker,
        "validation": validation,
        "installed": False,
    }
    if install and config.draft.auto_install:
        result.update(install_staged_draft(draft_dir, draft_name, config, progress))
    (job_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return result
