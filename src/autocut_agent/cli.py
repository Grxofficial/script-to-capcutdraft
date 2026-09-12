"""Script to CapCut Draft 命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ai import OpenAICompatibleClient
from .batch import create_batch
from .compose import create_white_job
from .config import load_config
from .doctor import diagnose
from .errors import AutocutError
from .media import MediaIndexer
from .pipeline import create_job, install_job
from .smoke import approve as approve_smoke
from .smoke import create_smoke, restore as restore_smoke, status as smoke_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="script-to-capcutdraft", description="文案驱动的 AI 自动混剪与剪映草稿生成器")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="项目根目录")
    parser.add_argument("--config", type=Path, help="配置文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="检查依赖、接口和剪映兼容状态")

    index = subparsers.add_parser("index", help="建立或更新本地素材索引")
    index.add_argument("--library", type=Path, required=True)
    index.add_argument("--rebuild", action="store_true", help="忽略缓存并重新分析")

    create = subparsers.add_parser("create", help="从文案和素材库生成剪映草稿")
    create.add_argument("--script", type=Path, required=True)
    create.add_argument("--library", type=Path, required=True)
    create.add_argument("--name", required=True)

    batch = subparsers.add_parser("batch", help="按 Markdown --- 分隔批量生成草稿")
    batch.add_argument("--scripts", type=Path, required=True, help="Markdown 文案集")
    batch.add_argument("--library", type=Path, required=True)
    batch.add_argument("--name-prefix", required=True, help="草稿名前缀，后接原文编号")

    compose = subparsers.add_parser(
        "compose", help="白底图口播草稿：只拆句配音，不使用素材库",
    )
    compose.add_argument("--script", type=Path, required=True)
    compose.add_argument("--name", required=True)
    compose.add_argument("--voice", help="覆盖豆包语音合成 2.0 speaker")
    compose.add_argument("--no-install", action="store_true", help="只生成 staging 草稿，不安装到剪映")

    inspect = subparsers.add_parser("inspect", help="输出任务匹配报告")
    inspect.add_argument("job_dir", type=Path)

    install_parser = subparsers.add_parser("install", help="安装已经生成的 staging 草稿，不调用模型")
    install_parser.add_argument("job_dir", type=Path)

    smoke = subparsers.add_parser("smoke", help="管理剪映三轨草稿冒烟门禁")
    smoke_sub = smoke.add_subparsers(dest="smoke_command", required=True)
    smoke_sub.add_parser("create", help="创建并安装 6 秒测试草稿")
    approve = smoke_sub.add_parser("approve", help="确认当前剪映版本已通过人工检查")
    approve.add_argument("--confirmed", action="store_true", help="确认已在剪映中完成三项人工检查")
    smoke_sub.add_parser("status", help="查看当前版本是否已通过")
    smoke_sub.add_parser("restore", help="移除测试草稿")

    serve = subparsers.add_parser("serve", help="启动本地网页控制台（仅本机可访问）")
    serve.add_argument("--port", type=int, default=8420)
    serve.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.project_root, args.config)
        if args.command == "doctor":
            print(json.dumps(diagnose(config), ensure_ascii=False, indent=2))
        elif args.command == "index":
            config.require_ai()
            result = MediaIndexer(
                config,
                OpenAICompatibleClient(config.ai, config.state_dir / "ai-cache"),
            ).run(
                args.library, rebuild=args.rebuild,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "create":
            result = create_job(config, args.script, args.library, args.name)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "batch":
            result = create_batch(config, args.scripts, args.library, args.name_prefix)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "compose":
            result = create_white_job(
                config, args.script, args.name,
                speaker=args.voice,
                install=not args.no_install,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "inspect":
            report = args.job_dir.expanduser().resolve() / "report.md"
            if not report.is_file():
                raise AutocutError(f"任务报告不存在：{report}")
            print(report.read_text(encoding="utf-8"))
        elif args.command == "install":
            print(json.dumps(install_job(config, args.job_dir), ensure_ascii=False, indent=2))
        elif args.command == "smoke":
            if args.smoke_command == "create":
                print(json.dumps(create_smoke(config), ensure_ascii=False, indent=2))
                print("请打开剪映检查：草稿能打开、三条轨道存在、字幕可编辑。")
                print("确认无误后运行：script-to-capcutdraft smoke approve --confirmed")
            elif args.smoke_command == "approve":
                if not args.confirmed:
                    raise AutocutError("必须人工检查后显式传入 --confirmed，不能由 Agent 自动代替确认")
                approve_smoke(config)
                print("当前剪映版本已通过三轨草稿冒烟测试")
            elif args.smoke_command == "status":
                print(json.dumps({"approved": smoke_status(config)}, ensure_ascii=False))
            elif args.smoke_command == "restore":
                restore_smoke(config)
                print("已移除 autocut-smoke-test")
        elif args.command == "serve":
            from .webapp import serve as serve_web
            serve_web(
                port=args.port, open_browser=not args.no_open,
                config_path=args.config, project_root=args.project_root,
            )
        return 0
    except (AutocutError, OSError, ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
