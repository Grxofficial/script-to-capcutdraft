"""配置加载与密钥隔离。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigurationError


def _load_env(path: Path) -> None:
    """加载简单 KEY=VALUE 文件，不覆盖已经存在的进程环境变量。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass(slots=True)
class CanvasConfig:
    width: int = 1080
    height: int = 1920
    fps: int = 30


@dataclass(slots=True)
class MediaConfig:
    scene_backend: str = "auto"
    transnet_weights: str = ".autocut/models/transnetv2-pytorch-weights.pth"
    transnet_device: str = "cpu"
    scene_threshold: float = 0.40
    min_shot_seconds: float = 1.0
    max_shot_seconds: float = 30.0
    keyframes_per_clip: int = 3


@dataclass(slots=True)
class AIConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_mode: str = "chat_completions"
    vlm_model: str = ""
    embedding_model: str = ""
    embedding_batch_size: int = 10
    rerank_model: str = ""
    rerank_api_mode: str = "chat_completions"
    timeout_seconds: int = 120
    analyzer_version: str = "v1"


@dataclass(slots=True)
class DoubaoTTSConfig:
    base_url: str = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"
    api_key: str = ""
    resource_id: str = "seed-tts-2.0"
    speaker: str = "zh_female_xiaohe_uranus_bigtts"
    sample_rate: int = 24000
    audio_format: str = "mp3"
    retries: int = 3
    tighten_audio: bool = True
    silence_threshold_db: float = -45.0
    keep_silence_seconds: float = 0.04


@dataclass(slots=True)
class MatchingConfig:
    confidence_threshold: float = 0.55
    candidate_count: int = 12
    min_segment_seconds: float = 1.5
    max_segment_seconds: float = 4.5
    same_source_penalty: float = 0.12
    reuse_penalty: float = 0.30
    text_pollution_penalty: float = 0.12
    head_trim_frames: int = 10


@dataclass(slots=True)
class DraftConfig:
    name_prefix: str = "AI混剪-"
    auto_install: bool = True
    require_smoke_approval: bool = True
    draft_root: str = "auto"
    text_size: float = 6.0
    text_y: float = -0.78


@dataclass(slots=True)
class AppConfig:
    root: Path
    state_dir: Path
    jobs_dir: Path
    canvas: CanvasConfig = field(default_factory=CanvasConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    doubao_tts: DoubaoTTSConfig = field(default_factory=DoubaoTTSConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    draft: DraftConfig = field(default_factory=DraftConfig)

    @property
    def database_path(self) -> Path:
        return self.state_dir / "library.sqlite3"

    @property
    def compatibility_path(self) -> Path:
        return self.state_dir / "jianying_compat.json"

    def require_ai(self) -> None:
        missing = []
        for name, value in (
            ("AUTOCUT_ARK_API_KEY", self.ai.api_key),
            ("AUTOCUT_VLM_MODEL", self.ai.vlm_model),
            ("AUTOCUT_EMBEDDING_MODEL", self.ai.embedding_model),
        ):
            if not value:
                missing.append(name)
        if missing:
            raise ConfigurationError("缺少 AI 配置：" + ", ".join(missing))

    def require_doubao_tts(self) -> None:
        if not self.doubao_tts.api_key:
            raise ConfigurationError("缺少 AUTOCUT_ARK_API_KEY")


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def load_config(root: Path | None = None, config_path: Path | None = None) -> AppConfig:
    project_root = (root or Path.cwd()).resolve()
    _load_env(project_root / ".env")
    path = config_path or project_root / "config.toml"
    if not path.exists():
        path = project_root / "config.example.toml"
    data: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)

    project = _section(data, "project")
    state_dir = (project_root / project.get("state_dir", ".autocut")).resolve()
    jobs_dir = (project_root / project.get("jobs_dir", "jobs")).resolve()

    shared_ark_key = os.getenv("AUTOCUT_ARK_API_KEY", "")
    ai_data = _section(data, "ai")
    ai_data.update({
        "base_url": os.getenv("AUTOCUT_AI_BASE_URL", ai_data.get("base_url", "https://api.openai.com/v1")),
        "api_key": os.getenv("AUTOCUT_AI_API_KEY", shared_ark_key),
        "api_mode": os.getenv("AUTOCUT_AI_API_MODE", ai_data.get("api_mode", "chat_completions")),
        "vlm_model": os.getenv("AUTOCUT_VLM_MODEL", ai_data.get("vlm_model", "")),
        "embedding_model": os.getenv("AUTOCUT_EMBEDDING_MODEL", ai_data.get("embedding_model", "")),
        "rerank_model": os.getenv("AUTOCUT_RERANK_MODEL", ai_data.get("rerank_model", "")),
    })
    doubao_data = _section(data, "doubao_tts")
    doubao_data.update({
        "base_url": os.getenv("AUTOCUT_DOUBAO_TTS_BASE_URL", doubao_data.get("base_url", "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional")),
        "api_key": os.getenv("AUTOCUT_ARK_PLAN_TTS_API_KEY", shared_ark_key),
        "resource_id": os.getenv("AUTOCUT_DOUBAO_TTS_RESOURCE_ID", doubao_data.get("resource_id", "seed-tts-2.0")),
        "speaker": os.getenv("AUTOCUT_DOUBAO_TTS_SPEAKER", doubao_data.get("speaker", "zh_female_vv_uranus_bigtts")),
    })

    cfg = AppConfig(
        root=project_root,
        state_dir=state_dir,
        jobs_dir=jobs_dir,
        canvas=CanvasConfig(**_section(data, "canvas")),
        media=MediaConfig(**_section(data, "media")),
        ai=AIConfig(**ai_data),
        doubao_tts=DoubaoTTSConfig(**doubao_data),
        matching=MatchingConfig(**_section(data, "matching")),
        draft=DraftConfig(**_section(data, "draft")),
    )
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
    return cfg
