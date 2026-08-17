"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .errors import AgentDError
from .logging_config import configure_logging
from .service import AgentDService


def _read_payload(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取输入JSON: {exc}") from exc


def _write(data: dict, path: str | None) -> None:
    rendered = json.dumps(data, ensure_ascii=False, indent=2)
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(destination)
    else:
        sys.stdout.write(rendered + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentd",
        description="Agent D 引物与探针设计智能体",
    )
    parser.add_argument("--config", help="Override JSON configuration")
    parser.add_argument("--state-dir", help="Job result directory")
    parser.add_argument("--log", help="Structured JSON log file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run Agent D WF-01 through WF-07 from a JSON request")
    run.add_argument("--workflow", required=True, choices=[f"WF-{index:02d}" for index in range(1, 8)])
    run.add_argument("--input", required=True, help="Request JSON path")
    run.add_argument("--output", help="Result JSON path; stdout when omitted")
    run.add_argument("--allow-fallback", action="store_true", help="Enable clearly-labeled development fallbacks")
    run.add_argument("--no-persist", action="store_true")

    status = subparsers.add_parser("tool-status", help="Show scientific tool readiness")
    status.add_argument("--allow-fallback", action="store_true")

    subparsers.add_parser("capabilities", help="Show workflow and server-integration capabilities")
    subparsers.add_parser("readiness", help="Evaluate deployment readiness")

    serve = subparsers.add_parser("serve", help="Start optional FastAPI service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log)
    try:
        if args.command == "serve":
            # Uvicorn imports ``agentd.api`` after this CLI module.  The API
            # owns its AgentDService instance, so propagate CLI overrides via
            # the same documented environment contract used by containers.
            if args.config:
                os.environ["AGENTD_CONFIG"] = str(Path(args.config).resolve())
            if args.state_dir:
                os.environ["AGENTD_STATE_DIR"] = str(Path(args.state_dir).resolve())
            try:
                import uvicorn  # type: ignore
            except ImportError as exc:
                raise SystemExit("API依赖未安装，请执行: pip install -e .[api]") from exc
            uvicorn.run("agentd.api:app", host=args.host, port=args.port, factory=False)
            return 0

        service = AgentDService(config_path=args.config, state_dir=args.state_dir)
        if args.command == "tool-status":
            _write(service.tool_status(allow_fallback=args.allow_fallback), None)
            return 0
        if args.command == "capabilities":
            _write(service.capabilities(), None)
            return 0
        if args.command == "readiness":
            readiness = service.readiness()
            _write(readiness, None)
            return 0 if readiness["status"] == "ready" else 2
        if args.command == "run":
            payload = _read_payload(args.input)
            if args.allow_fallback:
                payload["allow_fallback"] = True
            result = service.run(args.workflow, payload, persist=not args.no_persist)
            _write(result, args.output)
            return 0 if result["status"] != "FAILED" else 2
    except AgentDError as exc:
        _write({"status": "FAILED", "error": exc.to_dict()}, None)
        return 2
    return 1
