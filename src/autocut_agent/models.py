"""内部数据契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    rotation: int = 0


@dataclass(slots=True)
class ClipRecord:
    id: int
    file_id: int
    path: str
    source_start: float
    source_end: float
    width: int
    height: int
    caption: str
    metadata: dict[str, Any]
    embedding: list[float]
    fps: float = 30.0

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start


@dataclass(slots=True)
class ScriptUnit:
    index: int
    text: str
    audio_path: str = ""
    audio_duration: float = 0.0


@dataclass(slots=True)
class Candidate:
    clip: ClipRecord
    score: float
    reason: str = "向量语义相似度"


@dataclass(slots=True)
class TimelineClip:
    clip_id: int
    source_path: str
    source_start: float
    source_duration: float
    timeline_start: float
    timeline_duration: float
    score: float
    reason: str
    low_confidence: bool
    width: int
    height: int


@dataclass(slots=True)
class TimelineUnit:
    index: int
    text: str
    audio_path: str
    timeline_start: float
    duration: float
    clips: list[TimelineClip] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class JobPlan:
    name: str
    script_path: str
    library_path: str
    canvas: dict[str, int]
    units: list[TimelineUnit]
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(unit.duration for unit in self.units)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
