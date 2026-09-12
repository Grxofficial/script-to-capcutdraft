"""OpenAI 兼容的视觉理解、向量与重排接口。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .config import AIConfig
from .errors import ExternalServiceError
from .http import post_json
from .models import Candidate


class OpenAICompatibleClient:
    def __init__(self, config: AIConfig, cache_dir: Path | None = None):
        self.config = config
        self.cache_dir = cache_dir
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key}"}

    def _url(self, endpoint: str) -> str:
        return self.config.base_url.rstrip("/") + "/" + endpoint.lstrip("/")

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """调用外部模型；相同请求命中本地缓存时不重复计费。"""
        cache_path: Path | None = None
        if self.cache_dir is not None:
            cache_key = hashlib.sha256(json.dumps({
                "endpoint": endpoint,
                "payload": payload,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            cache_path = self.cache_dir / f"{cache_key}.json"
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
            if isinstance(cached, dict):
                return cached

        response = post_json(
            self._url(endpoint), payload, self.headers,
            self.config.timeout_seconds,
        )
        if cache_path is not None:
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, cache_path)
        return response

    def _model_json(
        self,
        model: str,
        prompt: str,
        images: list[str] | None = None,
        api_mode: str | None = None,
    ) -> dict[str, Any]:
        """调用文本/视觉模型并把模型正文解析为 JSON。"""
        mode = api_mode or self.config.api_mode
        if mode == "responses":
            content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
            for image_url in images or []:
                content.append({"type": "input_image", "image_url": image_url, "detail": "low"})
            payload = {
                "model": model,
                "input": [{"role": "user", "content": content}],
                "store": False,
            }
            response = self._post_json("responses", payload)
            return _parse_response_json(response)

        if mode != "chat_completions":
            raise ExternalServiceError(
                f"不支持的 AI 接口模式：{mode}，应为 responses 或 chat_completions"
            )

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_url in images or []:
            content.append({"type": "image_url", "image_url": {"url": image_url, "detail": "low"}})
        payload = {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": content}],
        }
        response = self._post_json("chat/completions", payload)
        return _parse_message_json(response)

    def describe_clip(self, frames: list[Path]) -> dict[str, Any]:
        prompt = (
            "这些图片按时间顺序来自同一个视频镜头。请只描述画面事实，只返回 JSON 对象，"
            "不要使用 Markdown。字段必须为 caption、subjects、actions、scene、objects、"
            "emotion、shot_scale、orientation、text_pollution。subjects/actions/objects 是字符串数组，"
            "text_pollution 是 0 到 1 的数字。caption 用简洁中文，不推测品牌或未出现的事件。"
        )
        images: list[str] = []
        for frame in frames:
            encoded = base64.b64encode(frame.read_bytes()).decode("ascii")
            images.append(f"data:image/jpeg;base64,{encoded}")
        return self._model_json(self.config.vlm_model, prompt, images)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        batch_size = self.config.embedding_batch_size
        if batch_size < 1:
            raise ExternalServiceError("embedding_batch_size 必须大于 0")
        results: list[list[float]] = []
        for offset in range(0, len(texts), batch_size):
            batch = texts[offset:offset + batch_size]
            payload = {"model": self.config.embedding_model, "input": batch}
            response = self._post_json("embeddings", payload)
            data = response.get("data")
            if not isinstance(data, list):
                raise ExternalServiceError("向量接口缺少 data 数组")
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            vectors = [item.get("embedding") for item in ordered]
            if len(vectors) != len(batch) or any(not isinstance(vector, list) for vector in vectors):
                raise ExternalServiceError("向量接口返回数量或格式不正确")
            results.extend([[float(value) for value in vector] for vector in vectors])
        return results

    def rerank(self, text: str, candidates: list[Candidate]) -> list[int]:
        if not self.config.rerank_model or not candidates:
            return [candidate.clip.id for candidate in candidates]
        compact = [{
            "clip_id": item.clip.id,
            "caption": item.clip.caption,
            "metadata": item.clip.metadata,
            "vector_score": round(item.score, 4),
        } for item in candidates]
        prompt = (
            "为旁白选择最贴切的真实画面。不要按列表原顺序照抄。只返回 JSON，不要使用 Markdown。"
            "格式为 {\"clip_ids\":[整数...]}，包含所有候选且最合适的在前。\n"
            f"旁白：{text}\n候选：{json.dumps(compact, ensure_ascii=False)}"
        )
        parsed = self._model_json(
            self.config.rerank_model,
            prompt,
            api_mode=self.config.rerank_api_mode,
        )
        requested = parsed.get("clip_ids", [])
        valid = {candidate.clip.id for candidate in candidates}
        result = [int(value) for value in requested if isinstance(value, int) and value in valid]
        result.extend(candidate.clip.id for candidate in candidates if candidate.clip.id not in result)
        return result

    def segment_script(self, text: str) -> list[str] | None:
        """LLM 语义拆句；模型未配置或返回异常时返回 None，由规则兜底。"""
        if not self.config.rerank_model:
            return None
        prompt = (
            "你是口播文案切分器，把文案按人类口播的换气节奏切成小段，用于逐句配音和字幕卡片。\n"
            "规则：\n"
            "1. 严格保留原文的用字和顺序，禁止增字、删字、改字、翻译、纠错。\n"
            "2. 切分点：逗号、顿号、句号处必须断开；没有标点但语义换气的地方"
            "（信息点转换、长修饰语之后）也要断开。\n"
            "3. 每段 6~16 个字；特别短的小句（不超过 5 个字）应与相邻小句合并为一段，"
            "段内用空格分隔。\n"
            "4. 输出中删除所有标点符号和 emoji；原本的顿号位置用空格代替；"
            "保留数字、字母、小数点。\n"
            '只返回 JSON：{"segments": ["第一段", "第二段", ...]}，不要 Markdown。\n'
            f"文案：{text}"
        )
        try:
            parsed = self._model_json(
                self.config.rerank_model,
                prompt,
                api_mode=self.config.rerank_api_mode,
            )
        except ExternalServiceError:
            return None
        segments = parsed.get("segments")
        if not isinstance(segments, list) or not segments:
            return None
        if not all(isinstance(item, str) and item.strip() for item in segments):
            return None
        return [item.strip() for item in segments]


def _parse_message_json(response: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ExternalServiceError("模型接口缺少 choices[0].message.content") from exc
    if isinstance(raw, list):
        raw = "".join(str(item.get("text", "")) for item in raw if isinstance(item, dict))
    if not isinstance(raw, str):
        raise ExternalServiceError("模型返回内容不是字符串")
    return _parse_json_text(raw)


def _parse_response_json(response: dict[str, Any]) -> dict[str, Any]:
    """提取 Responses API 的 output_text 正文。"""
    texts: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    texts.append(text)
    if not texts:
        raise ExternalServiceError("Responses API 缺少 output_text")
    return _parse_json_text("".join(texts))


def _parse_json_text(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExternalServiceError(f"模型没有返回合法 JSON：{cleaned[:300]}") from exc
    if not isinstance(parsed, dict):
        raise ExternalServiceError("模型 JSON 不是对象")
    return parsed


def metadata_to_text(metadata: dict[str, Any]) -> str:
    parts = [str(metadata.get("caption", ""))]
    for key in ("subjects", "actions", "scene", "objects", "emotion", "shot_scale", "orientation"):
        value = metadata.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    return "；".join(part.strip() for part in parts if part and part.strip())
