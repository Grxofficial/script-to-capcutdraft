"""网页创作台：可视化提交白底/混剪任务并管理剪映进程。

界面仅绑定 127.0.0.1；模型推理按配置调用云端服务。
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from .config import AppConfig, load_config
from .errors import AutocutError
from .platform_adapter import (
    browse_roots,
    jianying_running,
    open_jianying,
    platform_name,
    quit_jianying,
    smoke_is_approved,
)
from .pipeline import create_job, install_job, safe_name
from .compose import create_white_job
from .media import VIDEO_EXTENSIONS

# 用户已试听选定的常用豆包音色（voice_type -> 展示名）
COMMON_VOICES = [
    ("zh_female_xiaohe_uranus_bigtts", "小何 2.0（默认）"),
    ("zh_male_dayi_uranus_bigtts", "大壹 2.0"),
    ("zh_male_xionger_uranus_bigtts", "熊二 2.0"),
    ("zh_male_yizhipiannan_uranus_bigtts", "译制片男 2.0"),
    ("zh_male_qingshuangnanda_uranus_bigtts", "清爽男大 2.0"),
    ("zh_male_huolixiaoge_uranus_bigtts", "活力小哥 2.0"),
    ("zh_female_zhishuaiyingzi_uranus_bigtts", "直率英子 2.0"),
    ("zh_female_gujie_uranus_bigtts", "顾姐 2.0"),
]

# 网页试听用的固定样例文案
VOICE_SAMPLE_TEXT = "大家好，这是我的声音，先试听一下再决定。"
VOICE_PREVIEW_DIR = "voice-previews"


def _find_preview_video(library: Path) -> Path | None:
    """稳定选出首条非隐藏视频；这里只做界面预览，不参与镜头匹配。"""
    try:
        candidates = (
            path.resolve()
            for path in library.rglob("*")
            if path.is_file()
            and path.suffix.lower() in VIDEO_EXTENSIONS
            and not any(part.startswith(".") for part in path.relative_to(library).parts)
        )
        return next(iter(sorted(candidates, key=lambda path: str(path).lower())), None)
    except OSError:
        return None

class WebJob:
    """一次生成任务的线程安全状态记录。"""

    def __init__(self, kind: str, label: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind  # compose | create | install
        self.label = label
        self.status = "running"  # running | done | failed
        self.lines: list[str] = []
        self.result: dict[str, Any] | None = None
        self.error: str = ""
        self.started_at = time.time()
        self._lock = threading.Lock()

    def log(self, message: str) -> None:
        with self._lock:
            self.lines.append(message)

    def finish(self, result: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.status = "done"
            self.result = result

    def fail(self, message: str) -> None:
        with self._lock:
            self.status = "failed"
            self.error = message

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "label": self.label,
                "status": self.status,
                "lines": list(self.lines),
                "result": self.result,
                "error": self.error,
                "elapsed": round(time.time() - self.started_at, 1),
            }


class JobRunner:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.jobs: dict[str, WebJob] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, label: str, work: Callable[[WebJob], dict[str, Any] | None]) -> WebJob:
        job = WebJob(kind, label)
        with self._lock:
            self.jobs[job.id] = job

        def wrapped() -> None:
            try:
                result = work(job)
                job.finish(result)
            except (AutocutError, OSError, ValueError, RuntimeError) as exc:
                job.fail(str(exc))

        threading.Thread(target=wrapped, daemon=True).start()
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j.started_at, reverse=True)
            return [j.snapshot() for j in jobs[:30]]


def _run_compose(runner: JobRunner, payload: dict[str, Any]) -> WebJob:
    text = str(payload.get("text") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not text:
        raise ValueError("文案不能为空")
    if not name:
        raise ValueError("项目名不能为空")
    voice = str(payload.get("voice") or "").strip() or None
    install = bool(payload.get("install", True))

    script_path = runner.config.state_dir / "web-scripts" / f"{safe_name(name)}-{uuid.uuid4().hex[:6]}.txt"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(text, encoding="utf-8")

    label = f"白底口播 · {name}"

    def work(job: WebJob) -> dict[str, Any]:
        progress: Callable[[str], None] = job.log
        return create_white_job(
            runner.config, script_path, name,
            speaker=voice, install=install, progress=progress,
        )

    return runner.submit("compose", label, work)


def _run_create(runner: JobRunner, payload: dict[str, Any]) -> WebJob:
    text = str(payload.get("text") or "").strip()
    name = str(payload.get("name") or "").strip()
    raw_libraries = payload.get("libraries")
    if not isinstance(raw_libraries, list):
        legacy_library = str(payload.get("library") or "").strip()
        raw_libraries = [legacy_library] if legacy_library else []
    libraries: list[Path] = []
    seen: set[Path] = set()
    for raw in raw_libraries:
        value = str(raw or "").strip()
        if not value:
            continue
        path = Path(value).expanduser().resolve()
        if path not in seen:
            seen.add(path)
            libraries.append(path)
    if not text:
        raise ValueError("文案不能为空")
    if not name:
        raise ValueError("项目名不能为空")
    if not libraries:
        raise ValueError("素材混剪模式至少需要一个素材文件夹")
    for library in libraries:
        if not library.is_dir():
            raise ValueError(f"素材库目录不存在：{library}")

    script_path = runner.config.state_dir / "web-scripts" / f"{safe_name(name)}-{uuid.uuid4().hex[:6]}.txt"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(text, encoding="utf-8")

    label = f"素材混剪 · {name}"

    def work(job: WebJob) -> dict[str, Any]:
        return create_job(runner.config, script_path, libraries, name, progress=job.log)

    return runner.submit("create", label, work)


def _run_install(runner: JobRunner, payload: dict[str, Any]) -> WebJob:
    job_dir = str(payload.get("job_dir") or "").strip()
    if not job_dir:
        raise ValueError("缺少 job_dir")
    label = f"安装 · {Path(job_dir).name}"

    def work(job: WebJob) -> dict[str, Any]:
        return install_job(runner.config, Path(job_dir))

    return runner.submit("install", label, work)


def make_handler(runner: JobRunner, html_path: Path) -> type[BaseHTTPRequestHandler]:
    config = runner.config
    preview_files: set[Path] = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # 静默访问日志
            pass

        def _send_json(self, data: Any, code: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        def _browse(self) -> dict[str, Any]:
            """列出目录下的子文件夹，供前端文件夹选择器使用。"""
            query = parse_qs(urlparse(self.path).query)
            raw = (query.get("path") or [""])[0]
            target = Path(raw).expanduser() if raw else Path.home()
            try:
                target = target.resolve()
            except OSError:
                return {"error": f"路径无法解析：{raw}"}
            if not target.is_dir():
                return {"error": f"不是文件夹：{target}"}
            dirs: list[str] = []
            try:
                for entry in target.iterdir():
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir():
                            dirs.append(entry.name)
                    except OSError:
                        continue
            except OSError as exc:
                return {"error": f"无法读取：{exc}"}
            return {
                "path": str(target),
                "parent": str(target.parent) if target.parent != target else "",
                "dirs": sorted(dirs, key=str.lower),
            }

        def _library_preview(self) -> dict[str, Any]:
            """从素材文件夹中找一条代表视频，供画布区域做只读预览。"""
            query = parse_qs(urlparse(self.path).query)
            raw = (query.get("path") or [""])[0]
            library = Path(raw).expanduser() if raw else None
            if library is None:
                return {"found": False, "error": "缺少素材文件夹路径"}
            try:
                library = library.resolve()
            except OSError:
                return {"found": False, "error": f"路径无法解析：{raw}"}
            if not library.is_dir():
                return {"found": False, "error": f"不是文件夹：{library}"}
            video = _find_preview_video(library)
            if video is None:
                return {"found": False, "library": str(library)}
            preview_files.add(video)
            return {
                "found": True,
                "library": str(library),
                "path": str(video),
                "name": video.name,
                "url": "/api/media-preview?path=" + quote(str(video)),
            }

        def _voice_sample(self, speaker: str) -> Path | None:
            """返回音色试听样例；首次请求时合成并缓存到本机。"""
            if speaker not in {voice for voice, _ in COMMON_VOICES}:
                return None
            cache = config.state_dir / VOICE_PREVIEW_DIR
            cache.mkdir(parents=True, exist_ok=True)
            sample = cache / f"{speaker}.wav"
            if not sample.is_file() or sample.stat().st_size <= 44:
                from dataclasses import replace

                from .tts import DoubaoTTS
                tts = DoubaoTTS(
                    replace(config.doubao_tts, speaker=speaker),
                    progress=lambda message: None,
                )
                tts.synthesize(VOICE_SAMPLE_TEXT, sample)
            return sample

        def _send_voice_preview(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            speaker = (query.get("voice") or [""])[0]
            sample = self._voice_sample(speaker)
            if sample is None:
                self._send_json({"error": "未知音色"}, 404)
                return
            size = sample.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with sample.open("rb") as source:
                while True:
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _send_preview_video(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            raw = (query.get("path") or [""])[0]
            try:
                video = Path(raw).expanduser().resolve()
            except OSError:
                self._send_json({"error": "视频路径无法解析"}, 400)
                return
            if video not in preview_files or not video.is_file() or video.suffix.lower() not in VIDEO_EXTENSIONS:
                self._send_json({"error": "预览视频不可用"}, 404)
                return
            size = video.stat().st_size
            start, end = 0, size - 1
            status = 200
            range_header = self.headers.get("Range", "")
            if range_header.startswith("bytes="):
                try:
                    first, last = range_header[6:].split(",", 1)[0].split("-", 1)
                    start = int(first) if first else 0
                    end = int(last) if last else min(size - 1, start + 4 * 1024 * 1024 - 1)
                    if start < 0 or start >= size or end < start:
                        raise ValueError
                    end = min(end, size - 1)
                    status = 206
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            length = end - start + 1
            content_type = mimetypes.guess_type(video.name)[0] or "video/mp4"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with video.open("rb") as source:
                source.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = source.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    remaining -= len(chunk)

        def do_GET(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            if route in ("/", "/index.html"):
                # 每次请求重新读取页面，界面修改无需重启服务
                try:
                    body = html_path.read_text(encoding="utf-8").encode("utf-8")
                except OSError:
                    self._send_json({"error": "webui.html 缺失"}, 500)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif route == "/api/status":
                self._send_json({
                    "platform": platform_name(),
                    "jianying_running": jianying_running(),
                    "smoke_approved": smoke_is_approved(config.compatibility_path),
                    "browse_roots": browse_roots(),
                    "home": str(Path.home()),
                    "voices": [{"id": v, "name": n} for v, n in COMMON_VOICES],
                    "ark_configured": bool(config.ai.api_key and config.doubao_tts.api_key),
                })
            elif route == "/api/browse":
                self._send_json(self._browse())
            elif route == "/api/library-preview":
                self._send_json(self._library_preview())
            elif route == "/api/media-preview":
                self._send_preview_video()
            elif route == "/api/voice-preview":
                self._send_voice_preview()
            elif route == "/api/jobs":
                self._send_json(runner.list_jobs())
            elif route.startswith("/api/jobs/"):
                job_id = route.split("/api/jobs/", 1)[1]
                job = runner.jobs.get(job_id)
                if job is None:
                    self._send_json({"error": "任务不存在"}, 404)
                else:
                    self._send_json(job.snapshot())
            else:
                self._send_json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            payload = self._read_body()
            try:
                if self.path == "/api/quit-jianying":
                    self._send_json(quit_jianying())
                elif self.path == "/api/open-jianying":
                    self._send_json(open_jianying())
                elif self.path == "/api/jobs":
                    mode = str(payload.get("mode") or "white")
                    job = (
                        _run_create(runner, payload) if mode == "match"
                        else _run_compose(runner, payload)
                    )
                    self._send_json(job.snapshot(), 201)
                elif self.path == "/api/install":
                    job = _run_install(runner, payload)
                    self._send_json(job.snapshot(), 201)
                else:
                    self._send_json({"error": "not found"}, 404)
            except (AutocutError, OSError, ValueError, RuntimeError) as exc:
                self._send_json({"error": str(exc)}, 400)

    return Handler


def serve(
    port: int = 8420,
    open_browser: bool = True,
    config_path: Path | None = None,
    project_root: Path | None = None,
) -> None:
    config = load_config(project_root or Path.cwd(), config_path)
    html_path = Path(__file__).with_name("webui.html")
    runner = JobRunner(config)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(runner, html_path))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    print(f"Script to Draft 创作台已启动：{url}（Ctrl+C 停止）")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
