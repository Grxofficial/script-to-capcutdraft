"""把匹配计划转换为三轨剪映草稿。"""

from __future__ import annotations

from pathlib import Path

from .config import AppConfig
from .matching import fill_scale
from .models import JobPlan


def seconds_to_microseconds(value: float) -> int:
    return int(round(value * 1_000_000))


def build_draft(plan: JobPlan, job_dir: Path, config: AppConfig, white_image: Path | None = None) -> Path:
    try:
        import pyJianYingDraft as draft
    except ImportError as exc:
        raise RuntimeError("pyJianYingDraft 未安装，请先安装项目依赖") from exc

    staging_root = job_dir / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    folder = draft.DraftFolder(str(staging_root))
    script = folder.create_draft(
        plan.name,
        config.canvas.width,
        config.canvas.height,
        fps=config.canvas.fps,
        allow_replace=True,
    )
    script.append_track(draft.TrackSpec(draft.TrackType.video, name="画面"))
    script.append_track(draft.TrackSpec(draft.TrackType.audio, name="旁白"))
    script.append_track(draft.TrackSpec(draft.TrackType.text, name="字幕"))

    timeline_cursor = 0
    for unit in plan.units:
        unit_duration = seconds_to_microseconds(unit.duration)
        unit_start = timeline_cursor
        unit_end = unit_start + unit_duration
        unit_range = draft.Timerange(unit_start, unit_duration)
        if not unit.clips:
            if white_image is None:
                raise RuntimeError(f"第 {unit.index} 句没有画面素材，且未提供白底图")
            script.add_segment(
                draft.VideoSegment(str(white_image), unit_range, volume=0.0),
                "画面",
            )
        else:
            clip_cursor = unit_start
            for clip_index, clip in enumerate(unit.clips):
                remaining = unit_end - clip_cursor
                if remaining <= 0:
                    raise RuntimeError(f"第 {unit.index} 句的视频时长超过旁白时长")
                if clip_index == len(unit.clips) - 1:
                    clip_duration = remaining
                else:
                    clip_duration = min(seconds_to_microseconds(clip.timeline_duration), remaining)
                target = draft.Timerange(
                    clip_cursor,
                    clip_duration,
                )
                source = draft.Timerange(
                    seconds_to_microseconds(clip.source_start),
                    clip_duration,
                )
                scale = fill_scale(
                    clip.width, clip.height, config.canvas.width, config.canvas.height,
                )
                segment = draft.VideoSegment(
                    clip.source_path,
                    target,
                    source_timerange=source,
                    speed=1.0,
                    volume=0.0,
                    clip_settings=draft.ClipSettings(scale_x=scale, scale_y=scale),
                )
                script.add_segment(segment, "画面")
                clip_cursor += clip_duration

        script.add_segment(
            draft.AudioSegment(
                unit.audio_path,
                unit_range,
                source_timerange=draft.Timerange(0, seconds_to_microseconds(unit.duration)),
                speed=1.0,
                volume=1.0,
            ),
            "旁白",
        )
        script.add_segment(
            draft.TextSegment(
                unit.text,
                unit_range,
                style=draft.TextStyle(
                    size=config.draft.text_size,
                    color=(1.0, 1.0, 1.0),
                    align=1,
                    auto_wrapping=True,
                    max_line_width=0.82,
                ),
                border=draft.TextBorder(color=(0.0, 0.0, 0.0), width=35.0),
                clip_settings=draft.ClipSettings(transform_y=config.draft.text_y),
            ),
            "字幕",
        )
        timeline_cursor = unit_end
    script.save()
    return staging_root / plan.name


def validate_draft(draft_dir: Path, plan: JobPlan, expected_video_segments: int | None = None) -> dict[str, int]:
    import json

    content_path = draft_dir / "draft_content.json"
    if not content_path.exists():
        raise RuntimeError(f"草稿缺少 draft_content.json：{draft_dir}")
    content = json.loads(content_path.read_text(encoding="utf-8"))
    tracks = content.get("tracks", [])
    types = [track.get("type") for track in tracks]
    video_count = sum(1 for item in types if item == "video")
    audio_count = sum(1 for item in types if item == "audio")
    text_count = sum(1 for item in types if item == "text")
    if (video_count, audio_count, text_count) != (1, 1, 1):
        raise RuntimeError(
            f"草稿轨道结构错误：video={video_count}, audio={audio_count}, text={text_count}"
        )
    if any(key in content.get("materials", {}) for key in ("transitions", "video_effects", "audio_effects")):
        populated = {
            key: value for key, value in content.get("materials", {}).items()
            if key in {"transitions", "video_effects", "audio_effects"} and value
        }
        if populated:
            raise RuntimeError(f"草稿意外包含禁用素材：{list(populated)}")
    if expected_video_segments is None:
        expected_video_segments = sum(len(unit.clips) for unit in plan.units)
    segments = {track.get("type"): len(track.get("segments", [])) for track in tracks}
    if segments.get("video") != expected_video_segments:
        raise RuntimeError("视频片段数量与计划不一致")
    if segments.get("audio") != len(plan.units) or segments.get("text") != len(plan.units):
        raise RuntimeError("旁白或字幕片段数量与文案单元不一致")
    return {"video_tracks": video_count, "audio_tracks": audio_count, "text_tracks": text_count}
