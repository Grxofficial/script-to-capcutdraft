"""本地网页控制台：可视化提交白底/混剪任务并管理剪映进程。

仅绑定 127.0.0.1，单用户使用，不引入第三方 web 框架。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig, load_config
from .errors import AutocutError
from .platform_adapter import (
    browse_roots,
    jianying_running,
    platform_name,
    quit_jianying,
    smoke_is_approved,
)
from .pipeline import create_job, install_job, safe_name
from .compose import create_white_job

# 用户已试听选定的常用豆包音色（voice_type -> 展示名）
COMMON_VOICES = [
    ("zh_female_xiaohe_uranus_bigtts", "小何 2.0（默认）"),
    ("zh_female_vv_uranus_bigtts", "Vivi 2.0"),
    ("zh_female_shuangkuaisisi_uranus_bigtts", "爽快思思 2.0"),
    ("zh_female_linjianvhai_uranus_bigtts", "邻家女孩 2.0"),
    ("zh_female_tianmeixiaoyuan_uranus_bigtts", "甜美小源 2.0"),
    ("zh_female_qingxinnvsheng_uranus_bigtts", "清新女声 2.0"),
    ("zh_female_cancan_uranus_bigtts", "知性灿灿 2.0"),
]

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
    library = str(payload.get("library") or "").strip()
    if not text:
        raise ValueError("文案不能为空")
    if not name:
        raise ValueError("项目名不能为空")
    if not library:
        raise ValueError("素材匹配模式必须填写素材库路径")
    library_path = Path(library).expanduser()
    if not library_path.is_dir():
        raise ValueError(f"素材库目录不存在：{library_path}")

    script_path = runner.config.state_dir / "web-scripts" / f"{safe_name(name)}-{uuid.uuid4().hex[:6]}.txt"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(text, encoding="utf-8")

    label = f"素材混剪 · {name}"

    def work(job: WebJob) -> dict[str, Any]:
        return create_job(runner.config, script_path, library_path, name, progress=job.log)

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
            from urllib.parse import parse_qs, urlparse

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

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
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
            elif self.path == "/api/status":
                self._send_json({
                    "platform": platform_name(),
                    "jianying_running": jianying_running(),
                    "smoke_approved": smoke_is_approved(config.compatibility_path),
                    "browse_roots": browse_roots(),
                    "home": str(Path.home()),
                    "voices": [{"id": v, "name": n} for v, n in COMMON_VOICES],
                    "ark_configured": bool(config.ai.api_key and config.doubao_tts.api_key),
                })
            elif self.path.startswith("/api/browse"):
                self._send_json(self._browse())
            elif self.path == "/api/jobs":
                self._send_json(runner.list_jobs())
            elif self.path.startswith("/api/jobs/"):
                job_id = self.path.split("/api/jobs/", 1)[1].split("?", 1)[0]
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
    print(f"autocut 控制台已启动：{url}（Ctrl+C 停止）")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
