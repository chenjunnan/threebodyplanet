"""Fast 60s smoke of the real M6 benchmark CLI; this is not a 5y result."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_real_ias15_m6_harness_subprocess_short_smoke(tmp_path):
    repo=Path(__file__).resolve().parents[1]
    result=subprocess.run(
        [
            sys.executable, str(repo/"scripts"/"m6_five_year_acceptance.py"),
            "--artifact-root",str(tmp_path),"--smoke-seconds","60",
        ],
        check=True,capture_output=True,text=True,timeout=120,cwd=repo,
    )
    assert "SMOKE_ONLY_NOT_FIVE_YEAR_ACCEPTANCE" in result.stdout
    report=json.loads((tmp_path/"m6_five_year_acceptance.json").read_text())
    assert report["status"]=="SMOKE_ONLY_NOT_FIVE_YEAR_ACCEPTANCE"
    assert report["duration_s"]==60.0
    assert report["verified_physical_samples"]==7
    assert report["source"]=="REAL_REBOUND_IAS15_NOT_INTERPOLATED"
    assert report["thermal_recovery_verified"]
    assert report["normalized_residual_c0"]<1e-4
    assert report["normalized_residual_c1"]<1e-4
