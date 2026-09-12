"""文案向量召回、重排与无循环时间线规划。"""

from __future__ import annotations

import math
from collections import Counter

from .ai import OpenAICompatibleClient
from .config import MatchingConfig
from .errors import AutocutError, ExternalServiceError
from .models import Candidate, ClipRecord, JobPlan, ScriptUnit, TimelineClip, TimelineUnit


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


class TimelinePlanner:
    def __init__(self, config: MatchingConfig, ai: OpenAICompatibleClient):
        self.config = config
        self.ai = ai

    def create_plan(
        self,
        name: str,
        script_path: str,
        library_path: str | list[str],
        units: list[ScriptUnit],
        clips: list[ClipRecord],
        canvas: dict[str, int],
    ) -> JobPlan:
        if not units:
            raise AutocutError("文案拆句结果为空")
        if not clips:
            raise AutocutError("素材索引中没有可用镜头")
        vectors = self.ai.embed([unit.text for unit in units])
        timeline = 0.0
        used: Counter[int] = Counter()
        previous_file_id: int | None = None
        timeline_units: list[TimelineUnit] = []
        job_warnings: list[str] = []
        for unit, vector in zip(units, vectors):
            timeline = round(timeline, 3)
            # 草稿时间轴使用毫秒精度。向下取整可确保源区间永不会
            # 因四舍五入比 FFprobe 的真实音频时长多 1ms。
            unit.audio_duration = math.floor(unit.audio_duration * 1000) / 1000
            candidates = self._candidates(vector, clips, used, previous_file_id)
            reranked = False
            rerank_warning = ""
            try:
                ordered_ids = self.ai.rerank(unit.text, candidates)
                reranked = bool(self.ai.config.rerank_model)
            except ExternalServiceError as exc:
                # 重排是增强项，外部服务抖动不应让整条草稿失败。
                ordered_ids = [candidate.clip.id for candidate in candidates]
                rerank_warning = f"LLM 重排失败，已降级为本地向量排序：{exc}"
            by_id = {candidate.clip.id: candidate for candidate in candidates}
            ordered = [by_id[clip_id] for clip_id in ordered_ids if clip_id in by_id]
            planned, warnings = self._fill_unit(unit, timeline, ordered, used, reranked)
            if rerank_warning:
                warnings.append(rerank_warning)
            timeline_unit = TimelineUnit(
                index=unit.index,
                text=unit.text,
                audio_path=unit.audio_path,
                timeline_start=round(timeline, 3),
                duration=round(unit.audio_duration, 3),
                clips=planned,
                warnings=warnings,
            )
            if planned:
                previous_file_id = by_id[planned[-1].clip_id].clip.file_id
            timeline_units.append(timeline_unit)
            job_warnings.extend(f"第 {unit.index} 句：{warning}" for warning in warnings)
            timeline = round(timeline + unit.audio_duration, 3)
        return JobPlan(
            name=name,
            script_path=script_path,
            library_path=library_path,
            canvas=canvas,
            units=timeline_units,
            warnings=job_warnings,
        )

    def _candidates(
        self,
        vector: list[float],
        clips: list[ClipRecord],
        used: Counter[int],
        previous_file_id: int | None,
    ) -> list[Candidate]:
        ranked: list[Candidate] = []
        for clip in clips:
            score = cosine_similarity(vector, clip.embedding)
            reason_parts = [f"向量相似度 {score:.3f}"]
            if clip.file_id == previous_file_id:
                score -= self.config.same_source_penalty
                reason_parts.append("相邻同源惩罚")
            if used[clip.id]:
                score -= self.config.reuse_penalty * used[clip.id]
                reason_parts.append("重复使用惩罚")
            pollution = float(clip.metadata.get("text_pollution", 0) or 0)
            if pollution:
                score -= self.config.text_pollution_penalty * pollution
                reason_parts.append("画面文字惩罚")
            ranked.append(Candidate(clip=clip, score=score, reason="；".join(reason_parts)))
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked[:self.config.candidate_count]

    def _fill_unit(
        self,
        unit: ScriptUnit,
        timeline_start: float,
        candidates: list[Candidate],
        used: Counter[int],
        reranked: bool = False,
    ) -> tuple[list[TimelineClip], list[str]]:
        remaining = unit.audio_duration
        cursor = timeline_start
        result: list[TimelineClip] = []
        warnings: list[str] = []
        for candidate in candidates:
            if remaining <= 0.001:
                break
            fps = candidate.clip.fps if candidate.clip.fps > 0 else 30.0
            head_trim = max(0, self.config.head_trim_frames) / fps
            available = candidate.clip.duration - head_trim
            if available <= 0.05:
                continue
            desired = min(self.config.max_segment_seconds, remaining)
            take = min(available, desired)
            if remaining > self.config.max_segment_seconds and take < self.config.min_segment_seconds:
                continue
            occurrence = used[candidate.clip.id]
            free_space = max(0.0, available - take)
            offset = min(free_space, occurrence * self.config.min_segment_seconds)
            source_start = candidate.clip.source_start + head_trim + offset
            low = candidate.score < self.config.confidence_threshold
            if low:
                warnings.append(
                    f"低置信度 {candidate.score:.3f}，使用 {candidate.clip.path} "
                    f"{source_start:.3f}s"
                )
            result.append(TimelineClip(
                clip_id=candidate.clip.id,
                source_path=candidate.clip.path,
                source_start=round(source_start, 3),
                source_duration=round(take, 3),
                timeline_start=round(cursor, 3),
                timeline_duration=round(take, 3),
                score=round(candidate.score, 4),
                reason=candidate.reason + ("；LLM 重排" if reranked else ""),
                low_confidence=low,
                width=candidate.clip.width,
                height=candidate.clip.height,
            ))
            used[candidate.clip.id] += 1
            remaining = round(remaining - take, 3)
            cursor = round(cursor + take, 3)
        if remaining > 0.02:
            raise AutocutError(
                f"第 {unit.index} 句需要 {unit.audio_duration:.2f}s 画面，"
                f"候选镜头仍缺 {remaining:.2f}s；已禁止循环播放"
            )
        return result, warnings


def fill_scale(source_width: int, source_height: int, canvas_width: int, canvas_height: int) -> float:
    if min(source_width, source_height, canvas_width, canvas_height) <= 0:
        return 1.0
    width_ratio = canvas_width / source_width
    height_ratio = canvas_height / source_height
    smaller = min(width_ratio, height_ratio)
    return max(width_ratio, height_ratio) / smaller if smaller else 1.0
