# ruff: noqa: E501 -- Node smoke harness is intentionally embedded JavaScript.
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from threebody.cli import build_parser, execute
from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import VisualizationError
from threebody.physics.nbody.models import SimulationEvent
from threebody.visualization.dashboard import (
    _dashboard_payload,
    _select_times,
    export_dashboard_html,
)
from threebody.visualization.dashboard_reader import load_dashboard_archive
from threebody.visualization.reader import load_m2_archive


def _write_jsonl(path: Path, values) -> None:
    path.write_text(
        "".join(json.dumps(value, allow_nan=False) + "\n" for value in values),
        encoding="utf-8",
    )


def _m3_archive(tmp_path, m2_run, *, times=(0.0, 10.0, 20.0), name="m3"):
    m2 = load_m2_archive(m2_run)
    directory = tmp_path / name
    directory.mkdir()
    flux = []
    c0 = []
    c1 = []
    for sample_index, time in enumerate(times):
        stars = [
            {
                "body_id": f"star-{index}",
                "luminosity_w": 1.0e26 * index,
                "distance_m": 1.0e11 * index,
                "direction_inertial_unit": [1.0, 0.0, 0.0],
                "flux_w_m2": 100.0 * index + sample_index,
            }
            for index in range(1, 4)
        ]
        flux.append(
            {
                "t_s": time,
                "planet_id": "planet-1",
                "stars": stars,
                "planet_center_flux_w_m2": sum(star["flux_w_m2"] for star in stars),
                "globally_averaged_incoming_w_m2": 150.0,
                "model": "isotropic_bolometric_v1",
                "occultation": "disabled",
                "reference_equator_prime_meridian_normal": [1.0, 0.0, 0.0],
                "reference_equator_prime_meridian_toa_w_m2": 600.0,
            }
        )
        c0.append(
            {
                "t_s": time,
                "temperature_k": 280.0 + sample_index,
                "planet_center_flux_w_m2": 600.0,
                "globally_averaged_incoming_w_m2": 150.0,
                "absorbed_w_m2": 100.0,
                "internal_heat_flux_w_m2": 0.0,
                "emitted_w_m2": 100.0,
                "net_heating_w_m2": 0.0,
            }
        )
        bands = [270.0 + sample_index + band for band in range(4)]
        c1.append(
            {
                "t_s": time,
                "latitude_center_rad": [-1.0, -0.3, 0.3, 1.0],
                "temperature_k": bands,
                "zonal_incoming_w_m2": [0.0] * 4,
                "absorbed_w_m2": [0.0] * 4,
                "emitted_w_m2": [0.0] * 4,
                "meridional_convergence_w_m2": [0.0] * 4,
                "global_mean_temperature_k": sum(bands) / 4.0,
                "globally_averaged_incoming_w_m2": 0.0,
            }
        )
    _write_jsonl(directory / "stellar_flux.jsonl", flux)
    _write_jsonl(directory / "c0_temperature.jsonl", c0)
    _write_jsonl(directory / "c1_latitude_temperature.jsonl", c1)
    write_json_atomic(directory / "energy_budget.json", {"c0": {}, "c1": {}})
    manifest = {
        "schema_version": "0.3.0",
        "status": "COMPLETED",
        "world_id": m2.manifest.world_id,
        "initial_state_sha256": m2.manifest.initial_state_sha256,
        "m2_manifest_sha256": sha256_json(m2.manifest),
        "t_start_s": times[0],
        "t_final_s": times[-1],
        "sample_count": len(times),
    }
    write_json_atomic(directory / "thermal_manifest.json", manifest)
    return directory


def _m4_archive(tmp_path, m2_run, m3_run, *, message="reviewed outcome"):
    m2 = load_m2_archive(m2_run)
    m3_manifest = json.loads((m3_run / "thermal_manifest.json").read_text())
    directory = tmp_path / "m4"
    directory.mkdir()
    manifest = {
        "schema_version": "0.4.0",
        "world_id": m2.manifest.world_id,
        "source_hashes": {
            "m2_manifest_sha256": sha256_json(m2.manifest),
            "m3_manifest_sha256": sha256_json(m3_manifest),
        },
    }
    write_json_atomic(directory / "world_outcome_manifest.json", manifest)
    write_json_atomic(
        directory / "world_classification.json",
        {"dynamical_outcome": "ESCAPE_CANDIDATE", "environmental_outcomes": []},
    )
    _write_jsonl(
        directory / "world_timeline.jsonl",
        [
            {
                "event_id": "m4-1",
                "event_type": "HEAT_EXPOSURE",
                "t_start_s": 10.0,
                "body_ids": ["planet-1"],
                "message": message,
                "detection_method": "piecewise_linear_threshold_crossing",
                "terminal": False,
            }
        ],
    )
    return directory


def test_adapter_validates_provenance_and_overlap(tmp_path, m2_viz_archive_factory):
    m2_run = m2_viz_archive_factory()
    m3_run = _m3_archive(tmp_path, m2_run)
    archive = load_dashboard_archive(m2_run, m3_run)
    assert archive.t_start_s == 0.0
    assert archive.t_final_s == 20.0
    assert len(archive.flux) == 3

    manifest_path = m3_run / "thermal_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["m2_manifest_sha256"] = "0" * 64
    write_json_atomic(manifest_path, manifest)
    with pytest.raises(VisualizationError, match="m2_manifest_sha256"):
        load_dashboard_archive(m2_run, m3_run)


def test_payload_uses_one_overlap_time_and_never_extrapolates(
    tmp_path, m2_viz_archive_factory
):
    m2_run = m2_viz_archive_factory(times=(0.0, 10.0, 20.0))
    m3_run = _m3_archive(tmp_path, m2_run, times=(5.0, 15.0))
    archive = load_dashboard_archive(m2_run, m3_run)
    payload = _dashboard_payload(archive, "m", 10)
    assert payload["times_s"] == [5.0, 15.0]
    assert payload["positions"]["barycentric"]["star-1"][0][0] == pytest.approx(5.0)
    assert payload["positions"]["planet-relative"]["planet-1"] == [
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
    ]
    assert payload["display_semantics"]["range"].endswith("without extrapolation")


def test_offline_dashboard_has_single_controller_all_panels_and_safe_text(
    tmp_path, m2_viz_archive_factory
):
    malicious = SimulationEvent(
        event_type="ESCAPE_CANDIDATE",
        t_s=5.0,
        body_ids=("planet-1",),
        observed={},
        message="</script><script>alert(1)</script>",
        detection_method="heuristic<unsafe>",
    )
    m2_run = m2_viz_archive_factory(events=(malicious,))
    m3_run = _m3_archive(tmp_path, m2_run)
    m4_run = _m4_archive(tmp_path, m2_run, m3_run, message="<img src=x onerror=1>")
    output = tmp_path / "dashboard.html"
    summary = export_dashboard_html(
        m2_run, m3_run, output, m4_dir=m4_run, frame_cap=10
    )
    document = output.read_text(encoding="utf-8")
    assert summary["m4_loaded"]
    assert summary["plotly_js"] == "embedded"
    assert summary["file_size_bytes"] > 1_000_000
    assert all(f'id="{name}"' in document for name in ("orbit", "flux", "c0", "c1"))
    assert "function updateDashboard" in document
    assert 'Plotly.relayout("flux"' in document
    assert 'Plotly.relayout("c0"' in document
    assert 'Plotly.relayout("c1"' in document
    assert "</script><script>alert(1)</script>" not in document
    assert "<img src=x onerror=1>" not in document
    assert "\\u003c/script" in document


def test_collision_stops_at_archive_end_but_escape_candidate_continues(
    tmp_path, m2_viz_archive_factory
):
    collision = SimulationEvent(
        event_type="COLLISION",
        t_s=20.0,
        body_ids=("star-1", "planet-1"),
        observed={},
        message="collision",
        detection_method="callback",
    )
    collision_run = m2_viz_archive_factory(
        status="COLLISION", events=(collision,), name="collision"
    )
    collision_m3 = _m3_archive(tmp_path, collision_run, name="collision-m3")
    collision_archive = load_dashboard_archive(collision_run, collision_m3)
    assert collision_archive.t_final_s == 20.0
    assert collision_archive.events[0].terminal

    escape = SimulationEvent(
        event_type="ESCAPE_CANDIDATE",
        t_s=5.0,
        body_ids=("planet-1",),
        observed={},
        message="candidate",
        detection_method="heuristic",
    )
    escape_run = m2_viz_archive_factory(events=(escape,), name="escape")
    escape_m3 = _m3_archive(tmp_path, escape_run, name="escape-m3")
    escape_archive = load_dashboard_archive(escape_run, escape_m3)
    payload = _dashboard_payload(escape_archive, "au", 10)
    assert payload["times_s"][-1] == 20.0
    assert not escape_archive.events[0].terminal


@pytest.mark.parametrize("sample_count", [365, 3650, 100_000])
def test_frame_selection_is_bounded_for_large_archives(sample_count):
    archive = SimpleNamespace(
        flux=tuple(SimpleNamespace(t_s=float(index)) for index in range(sample_count)),
        events=(),
        t_start_s=0.0,
        t_final_s=float(sample_count - 1),
    )
    selected = _select_times(archive, 1000)
    assert selected[0] == 0.0
    assert selected[-1] == sample_count - 1
    assert len(selected) <= 1000


def test_dashboard_cli_and_vis1_command_coexist(tmp_path, m2_viz_archive_factory):
    m2_run = m2_viz_archive_factory(events=())
    m3_run = _m3_archive(tmp_path, m2_run)
    output = tmp_path / "cli-dashboard.html"
    parser = build_parser()
    args = parser.parse_args(
        [
            "viz",
            "dashboard",
            "--m2-run",
            str(m2_run),
            "--m3-run",
            str(m3_run),
            "--out",
            str(output),
            "--cdn",
            "--frame-cap",
            "10",
        ]
    )
    assert execute(args) == 0
    assert output.is_file()
    assert "cdn.plot.ly" in output.read_text(encoding="utf-8")
    trajectory_args = parser.parse_args(
        ["viz", "trajectory", "--run", str(m2_run), "--out", str(tmp_path / "v1.html")]
    )
    assert trajectory_args.command == "trajectory"


def test_generated_controller_updates_all_panels_in_javascript_runtime(
    tmp_path, m2_viz_archive_factory
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js runtime is unavailable")
    m2_run = m2_viz_archive_factory(events=())
    m3_run = _m3_archive(tmp_path, m2_run)
    output = tmp_path / "controller.html"
    export_dashboard_html(m2_run, m3_run, output, cdn=True, frame_cap=10)
    harness = tmp_path / "controller-smoke.js"
    harness.write_text(
        r"""
const fs=require("fs"),vm=require("vm");
const html=fs.readFileSync(process.argv[2],"utf8");
const data=html.match(/<script id="dashboard-data"[^>]*>([\s\S]*?)<\/script>/)[1];
const scripts=[...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)];
const app=scripts[scripts.length-1][1];
class Element{constructor(){this.value="";this.textContent="";this.layout={};this.listeners={};this.children=[];}addEventListener(n,f){this.listeners[n]=f;}appendChild(x){this.children.push(x);}}
const elements={};for(const id of ["dashboard-data","time-slider","center","speed","event-select","event-detail","time-label","play","pause","status","semantics","fate","orbit","flux","c0","c1"]){elements[id]=new Element();}
elements["dashboard-data"].textContent=data;elements.center.value="barycentric";elements.speed.value="1";
global.document={getElementById:id=>elements[id],createElement:()=>new Element()};
const calls=[];global.Plotly={newPlot:(id,d,l)=>{elements[id].layout=l;calls.push(["new",id]);},react:(id)=>calls.push(["react",id]),relayout:(id)=>calls.push(["relayout",id])};
global.setInterval=fn=>{fn();return 1;};global.clearInterval=()=>{};
vm.runInThisContext(app);
elements["time-slider"].listeners.input({target:{value:"1"}});
for(const id of ["flux","c0","c1"]){if(!calls.some(c=>c[0]==="relayout"&&c[1]===id))throw new Error("missing linked update "+id);}
if(!calls.some(c=>c[0]==="react"&&c[1]==="orbit"))throw new Error("missing orbit update");
if(!elements["time-label"].textContent.includes("t="))throw new Error("missing shared time label");
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node, str(harness), str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
