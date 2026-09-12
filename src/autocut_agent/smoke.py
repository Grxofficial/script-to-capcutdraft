"""剪映 11.x 三轨草稿冒烟测试。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import AppConfig
from .platform_adapter import (
    approve_smoke,
    install,
    prepare_draft,
    resolve_draft_root,
    smoke_is_approved,
    uninstall,
)

SMOKE_NAME = "autocut-smoke-test"


def create_smoke(config: AppConfig) -> dict[str, str]:
    import pyJianYingDraft as draft

    root = config.state_dir / "smoke"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    video = root / "smoke-video.mp4"
    audio = root / "smoke-audio.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc2=duration=6:size=1080x1920:rate=30", "-pix_fmt", "yuv420p", str(video),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=6", "-ac", "1", str(audio),
    ], check=True)
    staging = root / "staging"
    staging.mkdir()
    folder = draft.DraftFolder(str(staging))
    script = folder.create_draft(SMOKE_NAME, 1080, 1920, fps=30, allow_replace=True)
    script.append_track(draft.TrackSpec(draft.TrackType.video, name="画面"))
    script.append_track(draft.TrackSpec(draft.TrackType.audio, name="旁白"))
    script.append_track(draft.TrackSpec(draft.TrackType.text, name="字幕"))
    full = draft.trange("0s", "6s")
    script.add_segment(draft.VideoSegment(str(video), full, volume=0.0), "画面")
    script.add_segment(draft.AudioSegment(str(audio), full), "旁白")
    script.add_segment(draft.TextSegment(
        "Script to CapCut Draft 冒烟测试：请尝试修改这行文字",
        draft.trange("0s", "6s"),
        style=draft.TextStyle(size=6.0, align=1, auto_wrapping=True),
        clip_settings=draft.ClipSettings(transform_y=-0.78),
    ), "字幕")
    script.save()
    draft_dir = staging / SMOKE_NAME
    draft_root = resolve_draft_root(config.draft.draft_root)
    info = prepare_draft(draft_dir, SMOKE_NAME, draft_root, allow_missing_fingerprint=True)
    backup = install(draft_dir, SMOKE_NAME, draft_root, info)
    return {"draft": str(Path(info["fold_path"])), "backup": str(backup)}


def approve(config: AppConfig) -> None:
    approve_smoke(config.compatibility_path)


def restore(config: AppConfig) -> None:
    uninstall(SMOKE_NAME, resolve_draft_root(config.draft.draft_root))


def status(config: AppConfig) -> bool:
    return smoke_is_approved(config.compatibility_path)
