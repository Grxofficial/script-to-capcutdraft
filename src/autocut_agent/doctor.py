"""运行环境只读诊断。"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from pathlib import Path

from .config import AppConfig
from .db import LibraryDB
from .media import resolve_transnet_weights
from .platform_adapter import (
    current_jianying_version,
    platform_name,
    resolve_draft_root,
    smoke_is_approved,
)


def diagnose(config: AppConfig) -> dict[str, object]:
    draft_root = resolve_draft_root(config.draft.draft_root)
    weights = resolve_transnet_weights(config)
    with LibraryDB(config.database_path) as database:
        stats = database.stats()
    return {
        "platform": platform_name(),
        "python": platform.python_version(),
        "python_supported": sys.version_info[:2] == (3, 11),
        "ffmpeg": shutil.which("ffmpeg") or "",
        "ffprobe": shutil.which("ffprobe") or "",
        "pyjianyingdraft": bool(importlib.util.find_spec("pyJianYingDraft")),
        "pymediainfo": bool(importlib.util.find_spec("pymediainfo")),
        "transnet_package": bool(importlib.util.find_spec("transnetv2_pytorch")),
        "transnet_weights": str(weights),
        "transnet_ready": weights.exists() and bool(importlib.util.find_spec("transnetv2_pytorch")),
        "scene_fallback": "ffmpeg",
        "ai_key_configured": bool(config.ai.api_key),
        "ai_api_mode": config.ai.api_mode,
        "vlm_model": config.ai.vlm_model,
        "embedding_model": config.ai.embedding_model,
        "rerank_model": config.ai.rerank_model,
        "rerank_api_mode": config.ai.rerank_api_mode,
        "ark_agent_plan_configured": bool(config.ai.api_key and config.doubao_tts.api_key),
        "doubao_tts_key_configured": bool(config.doubao_tts.api_key),
        "doubao_tts_speaker": config.doubao_tts.speaker,
        "jianying_version": current_jianying_version(),
        "draft_root": str(draft_root),
        "draft_root_exists": draft_root.is_dir(),
        "smoke_approved_for_current_version": smoke_is_approved(config.compatibility_path),
        "index": stats,
    }
