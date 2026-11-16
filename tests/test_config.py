from __future__ import annotations

import pytest

from threebody.config.loader import load_and_resolve
from threebody.errors import ConfigError, FeatureNotImplementedError


def test_t01_valid_preset_and_stable_hash(resolved, example_path):
    config, digest = resolved
    config_again, digest_again = load_and_resolve(example_path)
    assert config == config_again
    assert digest == digest_again
    assert len(digest) == 64
    assert len(config.screening.initial_physics.rules) == 5


@pytest.mark.parametrize(
    "addition, message",
    [
        ("unknown_top_level: true\n", "extra_forbidden"),
        (
            "",
            "unknown rule",
        ),
    ],
)
def test_t02_unknown_fields_and_rule_ids(tmp_path, example_path, addition, message):
    text = example_path.read_text(encoding="utf-8")
    if message == "unknown rule":
        text = text.replace("rules: {}", "rules:\n      made.up.rule: {enabled: true}")
    else:
        text += addition
    path = tmp_path / "bad.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_and_resolve(path)


def test_t19_exploration_is_explicitly_not_implemented(tmp_path, example_path):
    text = example_path.read_text(encoding="utf-8").replace(
        "exploration:\n    enabled: false", "exploration:\n    enabled: true"
    )
    path = tmp_path / "future.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(FeatureNotImplementedError, match="not implemented"):
        load_and_resolve(path)


def test_t20_hash_changes_for_physical_config_but_not_yaml_format(tmp_path, example_path):
    config, digest = load_and_resolve(example_path)
    reformatted = tmp_path / "equivalent.yaml"
    import yaml

    reformatted.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True), encoding="utf-8"
    )
    _, equivalent_digest = load_and_resolve(reformatted)
    assert equivalent_digest == digest

    changed = tmp_path / "changed.yaml"
    changed.write_text(
        example_path.read_text(encoding="utf-8").replace(
            "characteristic_length_m: 149597870700.0",
            "characteristic_length_m: 149597870701.0",
        ),
        encoding="utf-8",
    )
    _, changed_digest = load_and_resolve(changed)
    assert changed_digest != digest


def test_null_required_and_candidate_limit_are_rejected(tmp_path, example_path):
    text = example_path.read_text(encoding="utf-8").replace("seed: 20261116", "seed: null")
    path = tmp_path / "null.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_and_resolve(path)

    text = example_path.read_text(encoding="utf-8").replace(
        "max_candidates: 1000", "max_candidates: 5"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="candidate_count exceeds"):
        load_and_resolve(path)

