from __future__ import annotations

import argparse
import json
from pathlib import Path

from threebody.common.json import write_json_atomic
from threebody.config.loader import load_and_resolve
from threebody.errors import ThreeBodyError
from threebody.experiments.runner import run_experiment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="threebody",
        description="Generate and screen reproducible three-star/one-planet initial states.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    groups = parser.add_subparsers(dest="group", required=True)

    config = groups.add_parser("config", help="Validate or resolve experiment configuration")
    config_commands = config.add_subparsers(dest="command", required=True)
    validate = config_commands.add_parser(
        "validate", help="Validate and fully resolve a YAML config"
    )
    validate.add_argument("config", type=Path)
    resolve = config_commands.add_parser("resolve", help="Write a resolved config JSON snapshot")
    resolve.add_argument("config", type=Path)
    resolve.add_argument("--out", type=Path, required=True)

    world = groups.add_parser("world", help="Initial-state operations")
    world_commands = world.add_subparsers(dest="command", required=True)
    generate = world_commands.add_parser("generate", help="Generate and screen initial states")
    generate.add_argument("--config", type=Path, required=True)
    generate.add_argument("--count", type=int)
    generate.add_argument("--out-dir", type=Path, required=True)
    generate.add_argument("--fail-fast", action="store_true", default=None)
    return parser


def execute(args: argparse.Namespace) -> int:
    config, digest = load_and_resolve(args.config)
    if args.group == "config" and args.command == "validate":
        print(f"valid: {args.config} (sha256={digest})")
        return 0
    if args.group == "config" and args.command == "resolve":
        write_json_atomic(args.out, config)
        print(f"resolved config written to {args.out} (sha256={digest})")
        return 0
    if args.group == "world" and args.command == "generate":
        summary = run_experiment(config, digest, args.out_dir, args.count, args.fail_fast)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["invalid"] == 0 else 2
    raise AssertionError("unreachable command")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    try:
        status = execute(parser.parse_args(argv))
    except (ThreeBodyError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    raise SystemExit(status)

