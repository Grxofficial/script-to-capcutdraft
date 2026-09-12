"""火山方舟 Agent Plan 豆包语音合成 2.0 适配。"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .config import DoubaoTTSConfig
from .errors import ExternalServiceError
from .http import post_json_lines
from .models import ScriptUnit


def tighten_audio(
    source: Path,
    output: Path,
    *,
    threshold_db: float,
    keep_seconds: float,
    sample_rate: int,
    enabled: bool = True,
) -> None:
    """保守删除旁白首尾静音，保留句内停顿和原始 TTS 音频。"""
    temporary = output.with_name(output.stem + ".tight.tmp.wav")
    if not enabled:
        shutil.copy2(source, temporary)
        os.replace(temporary, output)
        return

    threshold = f"{threshold_db:g}dB"
    keep = max(0.0, keep_seconds)
    trim_edge = (
        "silenceremove="
        f"start_periods=1:start_duration=0:start_threshold={threshold}:start_silence={keep}:"
        "detection=rms:window=0.02"
    )
    process = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
        "-af", f"{trim_edge},areverse,{trim_edge},areverse",
        "-ar", str(sample_rate), "-ac", "1", "-c:a", "pcm_s16le",
        str(temporary),
    ], capture_output=True, text=True)
    if process.returncode != 0 or not temporary.exists() or temporary.stat().st_size <= 44:
        temporary.unlink(missing_ok=True)
        raise ExternalServiceError(f"旁白压紧失败：{process.stderr[-500:]}")
    os.replace(temporary, output)


def probe_media_audio(path: Path) -> float:
    """用 FFprobe 读取真实音频时长。"""
    import json

    process = subprocess.run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", str(path),
    ], check=True, capture_output=True, text=True)
    duration = float(json.loads(process.stdout).get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise ExternalServiceError(f"无法读取配音时长：{path}")
    return duration


_DOUBAO_DONE_CODE = 20000000


class DoubaoTTS:
    """火山方舟 Agent Plan 专属 HTTP 接口（doubao-seed-tts-2.0）。"""

    def __init__(self, config: DoubaoTTSConfig, progress: Callable[[str], None] = print):
        self.config = config
        self.progress = progress

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": self.config.api_key,
            "X-Api-Resource-Id": self.config.resource_id,
        }

    def synthesize_units(self, units: list[ScriptUnit], output_dir: Path) -> list[ScriptUnit]:
        output_dir.mkdir(parents=True, exist_ok=True)
        for unit in units:
            output = output_dir / f"{unit.index:04d}.wav"
            source = output_dir / f"{unit.index:04d}.source.wav"
            metadata_path = output.with_suffix(".json")
            synthesis_key = hashlib.sha256(json.dumps({
                "text": unit.text,
                "provider": "doubao-seed-tts",
                "resource_id": self.config.resource_id,
                "speaker": self.config.speaker,
                "audio_format": self.config.audio_format,
                "sample_rate": self.config.sample_rate,
            }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            processing_key = hashlib.sha256(json.dumps({
                "synthesis_key": synthesis_key,
                "tighten_audio": self.config.tighten_audio,
                "silence_threshold_db": self.config.silence_threshold_db,
                "keep_silence_seconds": self.config.keep_silence_seconds,
            }, sort_keys=True).encode("utf-8")).hexdigest()
            try:
                cached = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = {}

            cached_synthesis_key = cached.get("synthesis_key") or cached.get("cache_key")
            if (
                not source.exists()
                and output.exists()
                and output.stat().st_size > 44
                and cached_synthesis_key == synthesis_key
            ):
                shutil.copy2(output, source)

            if not source.exists() or source.stat().st_size <= 44 or cached_synthesis_key != synthesis_key:
                self.progress(f"生成配音：{unit.index:04d}/{len(units):04d}")
                self.synthesize(unit.text, source)
            elif output.exists() and output.stat().st_size > 44 and cached.get("processing_key") == processing_key:
                unit.audio_path = str(output.resolve())
                unit.audio_duration = probe_media_audio(output)
                self.progress(f"配音缓存命中：{unit.index:04d}")
                continue

            self.progress(f"压紧配音：{unit.index:04d}/{len(units):04d}")
            tighten_audio(
                source,
                output,
                threshold_db=self.config.silence_threshold_db,
                keep_seconds=self.config.keep_silence_seconds,
                sample_rate=32000,
                enabled=self.config.tighten_audio,
            )
            metadata_path.write_text(json.dumps({
                "synthesis_key": synthesis_key,
                "processing_key": processing_key,
                "text": unit.text,
                "speaker": self.config.speaker,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            unit.audio_path = str(output.resolve())
            unit.audio_duration = probe_media_audio(output)
        return units

    def synthesize(self, text: str, output: Path) -> None:
        payload = {
            "req_params": {
                "text": text,
                "speaker": self.config.speaker,
                "audio_params": {
                    "format": self.config.audio_format,
                    "sample_rate": self.config.sample_rate,
                },
            }
        }
        audio = bytearray()
        finished = False
        response = post_json_lines(
            self.config.base_url, payload, self.headers,
            timeout=120, retries=self.config.retries,
        )
        try:
            with response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ExternalServiceError(f"豆包语音合成返回了非法 JSON 行：{line[:200]}") from exc
                    code = data.get("code", 0)
                    if code == 0 and data.get("data"):
                        try:
                            audio.extend(base64.b64decode(data["data"]))
                        except (binascii.Error, ValueError) as exc:
                            raise ExternalServiceError("豆包语音合成音频不是合法 base64 数据") from exc
                    if code == _DOUBAO_DONE_CODE:
                        finished = True
                        break
                    if code > 0:
                        raise ExternalServiceError(f"豆包语音合成返回错误：code={code} {data.get('message', '')[:300]}")
        except OSError as exc:
            raise ExternalServiceError(f"豆包语音合成流中断：{exc}") from exc
        if not finished:
            raise ExternalServiceError("豆包语音合成流提前结束，未收到完成标记")
        if len(audio) <= 44:
            raise ExternalServiceError("豆包语音合成返回的音频内容为空或损坏")

        compressed = output.with_name(output.stem + ".dl.mp3")
        compressed.write_bytes(bytes(audio))
        temporary = output.with_suffix(output.suffix + ".tmp.wav")
        process = subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(compressed),
            "-ar", str(self.config.sample_rate), "-ac", "1", "-c:a", "pcm_s16le",
            str(temporary),
        ], capture_output=True, text=True)
        if process.returncode != 0 or not temporary.exists() or temporary.stat().st_size <= 44:
            temporary.unlink(missing_ok=True)
            compressed.unlink(missing_ok=True)
            raise ExternalServiceError(f"豆包配音转 WAV 失败：{process.stderr[-500:]}")
        compressed.unlink(missing_ok=True)
        os.replace(temporary, output)
