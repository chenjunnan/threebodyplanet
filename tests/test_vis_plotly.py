from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from threebody.cli import build_parser, execute
from threebody.common.json import write_json_atomic
from threebody.errors import VisualizationError
from threebody.experiments.simulation import run_simulation_archive
from threebody.physics.nbody.models import SimulationEvent
from threebody.visualization.plotly_viewer import (
    DYNAMIC_TRACE_INDICES,
    _plotly_graph_objects,
    build_trajectory_figure,
    export_trajectory_html,
)
from threebody.visualization.reader import load_m2_archive
from threebody.visualization.transforms import AU_M
from threebody.world.generators import generate_initial_state


def test_figure_has_four_static_paths_events_dynamic_points_and_bounded_frames(
    m2_viz_archive_factory,
):
    archive = load_m2_archive(m2_viz_archive_factory())
    figure = build_trajectory_figure(archive, units="au", frame_cap=3)
    assert len(figure.data) == 9
    assert [trace.name for trace in figure.data[:4]] == [
        "Star 1 trajectory",
        "Star 2 trajectory",
        "Star 3 trajectory",
        "Planet trajectory",
    ]
    assert figure.data[0].x[0] == 0.0
    assert figure.data[0].x[-1] == pytest.approx(20.0 / AU_M)
    assert figure.data[4].name == "Runtime events (display estimates)"
    assert len(figure.frames) == 3
    assert all(tuple(frame.traces) == DYNAMIC_TRACE_INDICES for frame in figure.frames)
    assert all(len(frame.data) == 4 for frame in figure.frames)
    assert figure.layout.scene.aspectmode == "data"
    assert figure.layout.scene.xaxis.title.text == "x (AU)"
    assert len(figure.layout.sliders[0].steps) == 3
    assert {button.label for button in figure.layout.updatemenus[0].buttons} == {
        "Play",
        "Pause",
    }
    assert figure.layout.meta["displayed_frame_count"] == 3


def test_single_sample_and_partial_archive_are_displayed_safely(m2_viz_archive_factory):
    single = load_m2_archive(
        m2_viz_archive_factory(times=(0.0,), events=(), name="single")
    )
    figure = build_trajectory_figure(single, units="m", frame_cap=1)
    assert len(figure.data) == 9
    assert len(figure.frames) == 0
    assert not figure.layout.sliders
    assert figure.layout.scene.xaxis.title.text == "x (m)"

    partial = load_m2_archive(
        m2_viz_archive_factory(status="FAILED", name="partial")
    )
    partial_figure = build_trajectory_figure(partial)
    assert "PARTIAL TRAJECTORY" in partial_figure.layout.title.text
    assert any(
        "not a completed orbit" in annotation.text
        for annotation in partial_figure.layout.annotations
    )


def test_offline_html_embeds_plotly_and_escapes_event_text(
    tmp_path, m2_viz_archive_factory
):
    malicious = SimulationEvent(
        event_type="CLOSE_ENCOUNTER",
        t_s=5.0,
        body_ids=("star-1", "planet-1"),
        observed={},
        message="</script><script>alert(1)</script>",
        detection_method="unsafe<method>",
    )
    run = m2_viz_archive_factory(events=(malicious,))
    output = tmp_path / "trajectory.html"
    summary = export_trajectory_html(run, output, frame_cap=3)
    document = output.read_text(encoding="utf-8")
    assert summary["plotly_js"] == "embedded"
    assert summary["displayed_frames"] == 3
    assert summary["file_size_bytes"] == output.stat().st_size
    assert summary["file_size_bytes"] > 1_000_000
    assert "plotly.js" in document.lower()
    assert '<script src="https://cdn.plot.ly' not in document
    assert "</script><script>alert(1)</script>" not in document
    assert "&lt;script&gt;alert(1)" in document
    assert "\\u002fscript" in document
    with pytest.raises(VisualizationError, match="already exists"):
        export_trajectory_html(run, output)


def test_cdn_export_is_explicit_and_smaller(tmp_path, m2_viz_archive_factory):
    run = m2_viz_archive_factory(events=())
    output = tmp_path / "trajectory-cdn.html"
    summary = export_trajectory_html(run, output, cdn=True, frame_cap=2)
    document = output.read_text(encoding="utf-8")
    assert summary["plotly_js"] == "cdn"
    assert "cdn.plot.ly" in document
    assert output.stat().st_size < 1_000_000


def test_export_requires_html_extension(tmp_path, m2_viz_archive_factory):
    with pytest.raises(VisualizationError, match="must end with .html"):
        export_trajectory_html(
            m2_viz_archive_factory(), Path(tmp_path) / "trajectory.json"
        )


def test_missing_plotly_has_actionable_optional_dependency_message(monkeypatch):
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "plotly.graph_objects":
            raise ImportError("synthetic missing Plotly")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(VisualizationError, match=r"install.*\.\[viz\]"):
        _plotly_graph_objects()


def test_viz_cli_exports_a_real_short_m2_archive(tmp_path, resolved):
    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    initial = tmp_path / "candidate.initial.json"
    write_json_atomic(initial, state)
    m2_text = Path("configs/m2_ias15.yaml").read_text(encoding="utf-8")
    m2_text = m2_text.replace("duration_s: 31557600.0", "duration_s: 10000.0")
    m2_text = m2_text.replace("output_interval_s: 86400.0", "output_interval_s: 5000.0")
    m2_config = tmp_path / "m2.yaml"
    m2_config.write_text(m2_text, encoding="utf-8")
    archive = tmp_path / "real-m2"
    assert run_simulation_archive(initial, m2_config, archive).status == "COMPLETED"

    output = tmp_path / "real-trajectory.html"
    args = build_parser().parse_args(
        [
            "viz",
            "trajectory",
            "--run",
            str(archive),
            "--out",
            str(output),
            "--frame-cap",
            "3",
        ]
    )
    assert execute(args) == 0
    assert output.is_file()
    assert output.stat().st_size > 1_000_000
    document = output.read_text(encoding="utf-8")
    assert '"displayed_frame_count":3' in document
    assert "BARYCENTRIC_INERTIAL" in document
