"""文案拆句：LLM 语义拆分优先，逗号级规则兜底，段文本统一去标点。

不改写用字与顺序；切分只决定断点。段内标点与 emoji 按用户要求删除，
顿号位置以空格代替，数字、字母、小数点保留。
"""

from __future__ import annotations

import re
import unicodedata

from .models import ScriptUnit

# 硬边界：句末标点 + 逗号；换行作为分隔符被消耗
_BOUNDARY = re.compile(r"(?<=[。！？!?；;，,])|\n+")
# 软边界：顿号/冒号，仅用于切分超长的无逗号小句
_SOFT_BOUNDARY = re.compile(r"(?<=[、:：])")

# 段文本中要删除的标点；小数点和百分号保留
_PUNCT_STRIP = set("。！？；：．!?;:…—～·“”‘’\"'()（）【】《》〈〉「」『』")
# 顿号与段内逗号替换为空格（用户合并短句时的习惯写法："入口顺滑 苦感很轻"）
_PUNCT_TO_SPACE = set("、，,")
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
    (0xFE00, 0xFE0F), (0x200D, 0x200D), (0x2049, 0x2049), (0x203C, 0x203C),
)


def _is_emoji(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _EMOJI_RANGES)


def clean_segment_text(text: str) -> str:
    """删除段内标点与 emoji；顿号/逗号换空格，空白折叠为单个空格。"""
    cleaned = []
    for ch in text:
        if ch in _PUNCT_TO_SPACE:
            cleaned.append(" ")
        elif ch in _PUNCT_STRIP or _is_emoji(ch):
            continue
        else:
            cleaned.append(ch)
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()


def normalize_for_compare(text: str) -> str:
    """校验用归一化：删除所有空白、标点、emoji，只比文字本身。"""
    kept = []
    for ch in text:
        if ch.isspace() or _is_emoji(ch):
            continue
        if unicodedata.category(ch).startswith("P"):
            continue
        kept.append(ch)
    return "".join(kept)


def split_script(text: str, max_chars: int = 28) -> list[ScriptUnit]:
    """规则兜底拆句：逗号级粒度，段文本去标点。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    chunks: list[str] = []
    for sentence in _BOUNDARY.split(normalized):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue
        parts = [part for part in _SOFT_BOUNDARY.split(sentence) if part]
        buffer = ""
        for part in parts:
            if buffer and len(buffer) + len(part) > max_chars:
                chunks.append(buffer)
                buffer = part
            else:
                buffer += part
        if buffer:
            chunks.append(buffer)
    cleaned = [clean for clean in (clean_segment_text(chunk) for chunk in chunks) if clean]
    return [ScriptUnit(index=i + 1, text=chunk) for i, chunk in enumerate(cleaned)]


def split_units(text: str, ai_client=None) -> list[ScriptUnit]:
    """先用 LLM 语义拆句（校验不改写），失败回退规则拆句。"""
    if ai_client is not None:
        try:
            segments = ai_client.segment_script(text)
        except Exception:  # noqa: BLE001 — 语义拆句失败不影响兜底
            segments = None
        if segments and normalize_for_compare("".join(segments)) == normalize_for_compare(text):
            cleaned = [clean for clean in (clean_segment_text(s) for s in segments) if clean]
            if cleaned:
                return [ScriptUnit(index=i + 1, text=chunk) for i, chunk in enumerate(cleaned)]
    return split_script(text)
