"""小型 JSON HTTP 客户端，避免把服务商 SDK 绑进核心流程。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .errors import ExternalServiceError


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    retries: int = 3,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **headers}
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ExternalServiceError("接口返回的 JSON 不是对象")
                return decoded
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            last_error = ExternalServiceError(f"HTTP {exc.code}: {detail}")
            if 400 <= exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
        if attempt + 1 < retries:
            time.sleep(min(2 ** attempt, 4))
    raise ExternalServiceError(f"接口调用失败：{last_error}")


def download(url: str, headers: dict[str, str], timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ExternalServiceError(f"音频下载失败：{exc}") from exc


def post_json_lines(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    retries: int = 3,
):
    """POST 并按行迭代流式 JSON 响应；由调用方决定终止条件。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **headers}
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            last_error = ExternalServiceError(f"HTTP {exc.code}: {detail}")
            if 400 <= exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        else:
            return response
        if attempt + 1 < retries:
            time.sleep(min(2 ** attempt, 4))
    raise ExternalServiceError(f"接口调用失败：{last_error}")
