"""视频探测、镜头切分、关键帧抽取与增量索引。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .ai import OpenAICompatibleClient, metadata_to_text
from .config import AppConfig
from .db import LibraryDB
from .errors import AutocutError
from .models import MediaInfo

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise AutocutError(f"缺少命令：{args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-1500:]
        raise AutocutError(f"命令执行失败：{' '.join(args[:4])}\n{detail}") from exc


def probe_media(path: Path) -> MediaInfo:
    result = run_command([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ])
    data = json.loads(result.stdout)
    streams = [stream for stream in data.get("streams", []) if stream.get("codec_type") == "video"]
    if not streams:
        raise AutocutError(f"没有视频流：{path}")
    stream = streams[0]
    duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise AutocutError(f"无法读取视频时长：{path}")
    fps = _parse_rate(str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"))
    rotation = int(float(stream.get("tags", {}).get("rotate", 0) or 0))
    for item in stream.get("side_data_list", []):
        if "rotation" in item:
            rotation = int(float(item["rotation"]))
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    if abs(rotation) % 180 == 90:
        width, height = height, width
    return MediaInfo(path=path.resolve(), duration=duration, width=width, height=height, fps=fps, rotation=rotation)


def _parse_rate(value: str) -> float:
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator) if float(denominator) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def media_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def enforce_boundaries(
    split_points: list[float], duration: float, minimum: float, maximum: float,
) -> list[tuple[float, float]]:
    points = sorted({round(point, 3) for point in split_points if 0 < point < duration})
    merged: list[float] = []
    last = 0.0
    for point in points:
        if point - last >= minimum:
            merged.append(point)
            last = point
    if merged and duration - merged[-1] < minimum:
        merged.pop()
    boundaries = [0.0, *merged, duration]
    final: list[tuple[float, float]] = []
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        length = end - start
        pieces = max(1, math.ceil(length / maximum)) if maximum > 0 else 1
        for index in range(pieces):
            piece_start = start + length * index / pieces
            piece_end = start + length * (index + 1) / pieces
            final.append((round(piece_start, 3), round(piece_end, 3)))
    return final


def detect_scenes_ffmpeg(path: Path, threshold: float) -> list[float]:
    process = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-filter:v", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-",
    ], capture_output=True, text=True)
    if process.returncode != 0:
        raise AutocutError(f"FFmpeg 场景检测失败：{process.stderr[-1200:]}")
    return [float(value) for value in re.findall(r"pts_time:([0-9.]+)", process.stderr)]


def resolve_transnet_weights(config: AppConfig) -> Path:
    """优先使用显式权重，否则使用 transnetv2-pytorch 包内权重。"""
    configured = (config.root / config.media.transnet_weights).resolve()
    if configured.exists():
        return configured
    spec = importlib.util.find_spec("transnetv2_pytorch")
    if spec and spec.submodule_search_locations:
        packaged = Path(next(iter(spec.submodule_search_locations))) / "transnetv2-pytorch-weights.pth"
        if packaged.exists():
            return packaged.resolve()
    return configured


def detect_scenes_transnet(path: Path, weights: Path, device: str) -> list[float]:
    """采用 FireRed-OpenStoryline 同款 TransNetV2 低分辨率推理思路。"""
    try:
        import numpy as np
        import torch
        from transnetv2_pytorch import TransNetV2
    except ImportError as exc:
        raise AutocutError("TransNetV2 依赖未安装，请安装项目的 scene 可选依赖") from exc
    if not weights.exists():
        raise AutocutError(f"TransNetV2 权重不存在：{weights}")
    process = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-vf", "fps=25,scale=48:27",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ], check=True, capture_output=True)
    frames = np.frombuffer(process.stdout, dtype=np.uint8)
    if not frames.size:
        return []
    frames = frames.reshape((-1, 27, 48, 3))
    model = TransNetV2(device=device)
    model.eval()
    state = torch.load(str(weights), map_location=model.device)
    model.load_state_dict(state)
    tensor = torch.from_numpy(frames.copy()).unsqueeze(0).to(model.device)
    with torch.inference_mode():
        prediction, _ = model.predict_raw(tensor)
    scores = prediction.detach().cpu().numpy().reshape(-1)
    scenes = model.predictions_to_scenes_with_data(scores, fps=25.0, threshold=0.5)
    ends = [float(scene.get("end_time", 0)) for scene in scenes]
    return ends[:-1] if len(ends) > 1 else []


def detect_clips(path: Path, info: MediaInfo, config: AppConfig) -> list[tuple[float, float]]:
    backend = config.media.scene_backend.lower()
    weights = resolve_transnet_weights(config)
    if backend not in {"auto", "transnet", "ffmpeg"}:
        raise AutocutError(f"未知切镜后端：{backend}")
    if backend in {"auto", "transnet"}:
        try:
            points = detect_scenes_transnet(path, weights, config.media.transnet_device)
        except (AutocutError, subprocess.CalledProcessError):
            if backend == "transnet":
                raise
        else:
            return enforce_boundaries(
                points, info.duration, config.media.min_shot_seconds, config.media.max_shot_seconds,
            )
    points = detect_scenes_ffmpeg(path, config.media.scene_threshold)
    return enforce_boundaries(
        points, info.duration, config.media.min_shot_seconds, config.media.max_shot_seconds,
    )


def extract_keyframes(
    path: Path, start: float, end: float, count: int, destination: Path,
) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    length = max(0.05, end - start)
    fractions = [(index + 1) / (count + 1) for index in range(max(1, count))]
    frames: list[Path] = []
    for index, fraction in enumerate(fractions, start=1):
        at = min(end - 0.02, start + length * fraction)
        output = destination / f"frame-{index:02d}.jpg"
        run_command([
            "ffmpeg", "-y", "-v", "error", "-ss", f"{max(start, at):.3f}",
            "-i", str(path), "-frames:v", "1",
            "-vf", "scale=768:-2:force_original_aspect_ratio=decrease", str(output),
        ])
        frames.append(output)
    return frames


def normalize_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    list_fields = ("subjects", "actions", "objects")
    result: dict[str, Any] = {"caption": str(raw.get("caption", "")).strip()}
    for key in list_fields:
        value = raw.get(key, [])
        if isinstance(value, str):
            value = [value]
        result[key] = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
    for key in ("scene", "emotion", "shot_scale", "orientation"):
        result[key] = str(raw.get(key, "")).strip()
    try:
        result["text_pollution"] = min(1.0, max(0.0, float(raw.get("text_pollution", 0))))
    except (TypeError, ValueError):
        result["text_pollution"] = 0.0
    if not result["caption"]:
        result["caption"] = metadata_to_text(result) or "未识别画面"
    return result


class MediaIndexer:
    def __init__(
        self, config: AppConfig, ai: OpenAICompatibleClient,
        progress: Callable[[str], None] = print,
    ):
        self.config = config
        self.ai = ai
        self.progress = progress

    @property
    def analyzer_signature(self) -> str:
        values = (
            self.config.ai.analyzer_version,
            self.config.ai.vlm_model,
            self.config.ai.embedding_model,
            self.config.media.scene_backend,
            str(self.config.media.scene_threshold),
            str(self.config.media.min_shot_seconds),
            str(self.config.media.max_shot_seconds),
            str(self.config.media.keyframes_per_clip),
        )
        return "|".join(values)

    def run(self, library: Path, rebuild: bool = False, allow_empty: bool = False) -> dict[str, int]:
        root = library.expanduser().resolve()
        if not root.is_dir():
            raise AutocutError(f"素材库目录不存在：{root}")
        paths = sorted(
            path.resolve() for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS and not any(part.startswith(".") for part in path.relative_to(root).parts)
        )
        if not paths:
            if allow_empty:
                with LibraryDB(self.config.database_path) as database:
                    pruned = database.prune_missing(root, set())
                return {"scanned": 0, "indexed": 0, "skipped": 0, "failed": 0, "pruned": pruned}
            raise AutocutError(f"素材库里没有支持的视频：{root}")
        counters = {"scanned": len(paths), "indexed": 0, "skipped": 0, "failed": 0, "pruned": 0}
        with LibraryDB(self.config.database_path) as database:
            existing_paths = {str(path) for path in paths}
            for number, path in enumerate(paths, start=1):
                stat = path.stat()
                state = database.file_state(path)
                if (
                    not rebuild and state and not state["error"]
                    and int(state["size"]) == stat.st_size
                    and int(state["mtime_ns"]) == stat.st_mtime_ns
                    and state["analyzer_version"] == self.analyzer_signature
                ):
                    counters["skipped"] += 1
                    self.progress(f"[{number}/{len(paths)}] 缓存命中：{path.name}")
                    continue
                content_hash = media_hash(path)
                same_content = database.file_by_hash(root, content_hash, self.analyzer_signature)
                if not rebuild and same_content and same_content["path"] == str(path):
                    database.update_file_location(int(same_content["id"]), path, stat.st_size, stat.st_mtime_ns)
                    counters["skipped"] += 1
                    self.progress(f"[{number}/{len(paths)}] 内容未变，仅更新文件状态：{path.name}")
                    continue
                if not rebuild and same_content and same_content["path"] not in existing_paths:
                    database.update_file_location(int(same_content["id"]), path, stat.st_size, stat.st_mtime_ns)
                    counters["skipped"] += 1
                    self.progress(f"[{number}/{len(paths)}] 检测到素材改名，复用索引：{path.name}")
                    continue
                self.progress(f"[{number}/{len(paths)}] 分析：{path.name}")
                try:
                    self._index_file(database, root, path, stat.st_size, stat.st_mtime_ns, content_hash)
                except Exception as exc:
                    database.record_error(
                        root, path, stat.st_size, stat.st_mtime_ns,
                        self.analyzer_signature, str(exc),
                    )
                    counters["failed"] += 1
                    self.progress(f"  失败：{exc}")
                else:
                    counters["indexed"] += 1
            counters["pruned"] = database.prune_missing(root, existing_paths)
        return counters

    def _index_file(
        self, database: LibraryDB, root: Path, path: Path, size: int, mtime_ns: int,
        content_hash: str,
    ) -> None:
        info = probe_media(path)
        ranges = detect_clips(path, info, self.config)
        prepared: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="autocut-frames-") as temp:
            temp_root = Path(temp)
            for index, (start, end) in enumerate(ranges, start=1):
                frames = extract_keyframes(
                    path, start, end, self.config.media.keyframes_per_clip,
                    temp_root / f"clip-{index:04d}",
                )
                metadata = normalize_metadata(self.ai.describe_clip(frames))
                prepared.append({
                    "source_start": start,
                    "source_end": end,
                    "caption": metadata["caption"],
                    "metadata": metadata,
                })
        texts = [metadata_to_text(item["metadata"]) for item in prepared]
        embeddings: list[list[float]] = []
        for offset in range(0, len(texts), 64):
            embeddings.extend(self.ai.embed(texts[offset:offset + 64]))
        for item, embedding in zip(prepared, embeddings):
            item["embedding"] = embedding
        database.replace_file(
            root, info, content_hash, size, mtime_ns,
            self.analyzer_signature, prepared,
        )
