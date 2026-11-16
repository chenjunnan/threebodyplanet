from __future__ import annotations

from pathlib import Path

import pytest

from threebody.config.loader import load_and_resolve


@pytest.fixture
def example_path() -> Path:
    return Path(__file__).parents[1] / "configs" / "example_experiment.yaml"


@pytest.fixture
def resolved(example_path):
    return load_and_resolve(example_path)

