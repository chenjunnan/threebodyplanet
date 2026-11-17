from __future__ import annotations

import argparse
import json
from pathlib import Path

from threebody import __version__
from threebody.common.json import write_json_atomic
from threebody.config.loader import load_and_resolve
from threebody.errors import ThreeBodyError
from threebody.experiments.assessment import run_world_assessment
from threebody.experiments.longrun import (
    resume_long_simulation_archive,
    run_long_simulation_archive,
)
from threebody.experiments.runner import run_experiment
from threebody.experiments.simulation import run_simulation_archive
from threebody.experiments.thermal import run_thermal_archive
from threebody.visualization.dashboard import export_dashboard_html
from threebody.visualization.plotly_viewer import export_trajectory_html


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="threebody",
        description="Generate, screen, and evolve reproducible three-star/one-planet states.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    simulate = world_commands.add_parser(
        "simulate", help="Integrate one initial state with REBOUND IAS15"
    )
    simulate.add_argument("--initial", type=Path, required=True)
    simulate.add_argument("--config", type=Path, required=True)
    simulate.add_argument("--out-dir", type=Path, required=True)
    simulate_long = world_commands.add_parser(
        "simulate-long", help="Start a segmented, recoverable REBOUND IAS15 run"
    )
    simulate_long.add_argument("--initial", type=Path, required=True)
    simulate_long.add_argument("--config", type=Path, required=True)
    simulate_long.add_argument("--out-dir", type=Path, required=True)
    simulate_long.add_argument("--run-id")
    resume = world_commands.add_parser(
        "resume", help="Resume a safely paused segmented IAS15 run"
    )
    resume.add_argument("--run", type=Path, required=True)
    resume.add_argument("--max-wall-seconds", type=float)
    checkpoint = world_commands.add_parser(
        "checkpoint", help="Inspect verified M5A binary checkpoint metadata"
    )
    checkpoint_commands = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_inspect = checkpoint_commands.add_parser(
        "inspect", help="Verify and print checkpoint metadata without resuming"
    )
    checkpoint_inspect.add_argument("--run", type=Path, required=True)
    checkpoint_inspect.add_argument("--id", type=int)
    verify_long = world_commands.add_parser(
        "verify-long-run", help="Verify an M5A archive and its immutable segment chain"
    )
    verify_long.add_argument("--run", type=Path, required=True)
    thermal = world_commands.add_parser(
        "thermal", help="Run M3 radiation and C0/C1 thermal reference models"
    )
    thermal.add_argument("--config", type=Path, required=True)
    thermal.add_argument("--out-dir", type=Path, required=True)
    assess = world_commands.add_parser(
        "assess", help="Classify archived dynamics and environmental exposures"
    )
    assess.add_argument("--initial", type=Path, required=True)
    assess.add_argument("--m2-archive", type=Path)
    assess.add_argument("--m3-archive", type=Path)
    assess.add_argument("--initial-outcome", type=Path)
    assess.add_argument("--config", type=Path, required=True)
    assess.add_argument("--out-dir", type=Path, required=True)

    viz = groups.add_parser("viz", help="Read-only visualization of scientific archives")
    viz_commands = viz.add_subparsers(dest="command", required=True)
    trajectory = viz_commands.add_parser(
        "trajectory", help="Export an interactive M2 four-body trajectory as HTML"
    )
    trajectory.add_argument("--run", type=Path, required=True)
    trajectory.add_argument("--out", type=Path, required=True)
    trajectory.add_argument("--frame-cap", type=int, default=1000)
    trajectory.add_argument("--max-input-samples", type=int, default=100_000)
    trajectory.add_argument("--units", choices=("au", "m"), default="au")
    trajectory.add_argument("--center", choices=("barycentric",), default="barycentric")
    trajectory.add_argument("--cdn", action="store_true")
    trajectory.add_argument("--overwrite", action="store_true")
    dashboard = viz_commands.add_parser(
        "dashboard", help="Export a linked M2/M3 orbit-radiation-temperature dashboard"
    )
    dashboard.add_argument("--m2-run", type=Path, required=True)
    dashboard.add_argument("--m3-run", type=Path, required=True)
    dashboard.add_argument("--m4-run", type=Path)
    dashboard.add_argument("--out", type=Path, required=True)
    dashboard.add_argument("--frame-cap", type=int, default=1000)
    dashboard.add_argument("--max-input-samples", type=int, default=100_000)
    dashboard.add_argument("--units", choices=("au", "m"), default="au")
    dashboard.add_argument("--cdn", action="store_true")
    dashboard.add_argument("--overwrite", action="store_true")
    return parser


def execute(args: argparse.Namespace) -> int:
    if args.group == "config":
        config, digest = load_and_resolve(args.config)
        if args.command == "validate":
            print(f"valid: {args.config} (sha256={digest})")
            return 0
        if args.command == "resolve":
            write_json_atomic(args.out, config)
            print(f"resolved config written to {args.out} (sha256={digest})")
            return 0
    if args.group == "world":
        if args.command == "generate":
            config, digest = load_and_resolve(args.config)
            summary = run_experiment(config, digest, args.out_dir, args.count, args.fail_fast)
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 0 if summary["invalid"] == 0 else 2
        if args.command == "simulate":
            result = run_simulation_archive(args.initial, args.config, args.out_dir)
            print(result.model_dump_json())
            return 1 if result.status in {"FAILED", "STOPPED"} else 0
        if args.command == "simulate-long":
            result = run_long_simulation_archive(
                args.initial,
                args.config,
                args.out_dir,
                run_id=args.run_id,
            )
            print(result.manifest.model_dump_json())
            return 1 if result.manifest.task.status == "FAILED" else 0
        if args.command == "resume":
            result = resume_long_simulation_archive(
                args.run, max_wall_seconds=args.max_wall_seconds
            )
            print(result.manifest.model_dump_json())
            return 1 if result.manifest.task.status == "FAILED" else 0
        if args.command == "checkpoint" and args.checkpoint_command == "inspect":
            from threebody.physics.longrun.checkpoint import inspect_checkpoint

            checkpoints = args.run / "checkpoints"
            if args.id is None:
                available = sorted(
                    path
                    for path in checkpoints.iterdir()
                    if path.is_dir() and len(path.name) >= 8 and path.name.isdecimal()
                )
                if not available:
                    raise ValueError("run has no retained checkpoints")
                source = available[-1]
            else:
                if args.id < 0:
                    raise ValueError("checkpoint id must be non-negative")
                source = checkpoints / f"{args.id:08d}"
            result = inspect_checkpoint(source)
            print(result.model_dump_json())
            return 0
        if args.command == "verify-long-run":
            from threebody.physics.longrun.archive import verify_run_archive

            result = verify_run_archive(args.run)
            print(result.model_dump_json())
            return 0
        if args.command == "thermal":
            result = run_thermal_archive(args.config, args.out_dir)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 1 if result["status"] == "FAILED" else 0
        if args.command == "assess":
            result = run_world_assessment(
                initial_path=args.initial,
                m2_dir=args.m2_archive,
                m3_dir=args.m3_archive,
                initial_outcome_path=args.initial_outcome,
                config_path=args.config,
                out_dir=args.out_dir,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
    if args.group == "viz" and args.command == "trajectory":
        summary = export_trajectory_html(
            args.run,
            args.out,
            units=args.units,
            frame_cap=args.frame_cap,
            max_input_samples=args.max_input_samples,
            center=args.center,
            cdn=args.cdn,
            overwrite=args.overwrite,
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    if args.group == "viz" and args.command == "dashboard":
        summary = export_dashboard_html(
            args.m2_run,
            args.m3_run,
            args.out,
            m4_dir=args.m4_run,
            units=args.units,
            frame_cap=args.frame_cap,
            max_input_samples=args.max_input_samples,
            cdn=args.cdn,
            overwrite=args.overwrite,
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    raise AssertionError("unreachable command")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    try:
        status = execute(parser.parse_args(argv))
    except (ThreeBodyError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    raise SystemExit(status)

