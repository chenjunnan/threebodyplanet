from __future__ import annotations

import json

from threebody.cli import build_parser, execute
from threebody.experiments.runner import run_experiment


def test_t18_batch_ten_has_complete_artifacts(tmp_path, resolved):
    config, digest = resolved
    summary = run_experiment(config, digest, tmp_path, count=10)
    assert summary["total"] == 10
    assert summary["accepted"] + summary["rejected"] + summary["invalid"] == 10
    assert (tmp_path / "resolved_config.json").is_file()
    assert (tmp_path / "manifest.json").is_file()
    assert (tmp_path / "summary.json").is_file()
    for index in range(10):
        assert (tmp_path / "candidates" / f"{index:06d}.initial.json").is_file()
        assert (tmp_path / "candidates" / f"{index:06d}.screening.json").is_file()
    for path in tmp_path.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "NaN" not in text and "Infinity" not in text
        json.loads(text)


def test_cli_validate_resolve_generate_and_help(tmp_path, example_path, capsys):
    parser = build_parser()
    assert execute(parser.parse_args(["config", "validate", str(example_path)])) == 0
    resolved_path = tmp_path / "resolved.json"
    assert execute(
        parser.parse_args(["config", "resolve", str(example_path), "--out", str(resolved_path)])
    ) == 0
    out_dir = tmp_path / "run"
    assert execute(
        parser.parse_args(
            [
                "world",
                "generate",
                "--config",
                str(example_path),
                "--count",
                "2",
                "--out-dir",
                str(out_dir),
            ]
        )
    ) == 0
    assert resolved_path.is_file()
    assert (out_dir / "summary.json").is_file()
    assert "sha256=" in capsys.readouterr().out
