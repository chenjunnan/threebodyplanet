from __future__ import annotations

import html
import os
import tempfile
from pathlib import Path
from typing import Literal

from threebody.errors import VisualizationError
from threebody.visualization.reader import CANONICAL_BODY_IDS, TrajectoryArchive, load_m2_archive
from threebody.visualization.transforms import (
    SECONDS_PER_DAY,
    DisplayUnits,
    convert_position,
    event_display_markers,
    select_frame_indices,
)

BODY_LABELS = {
    "star-1": "Star 1",
    "star-2": "Star 2",
    "star-3": "Star 3",
    "planet-1": "Planet",
}
BODY_COLORS = {
    "star-1": "#f9c74f",
    "star-2": "#f9844a",
    "star-3": "#90be6d",
    "planet-1": "#577590",
}
BODY_MARKER_SIZES_PX = {
    "star-1": 9,
    "star-2": 9,
    "star-3": 9,
    "planet-1": 6,
}
DYNAMIC_TRACE_INDICES = (5, 6, 7, 8)
ABSOLUTE_MAX_FRAMES = 10_000


def _plotly_graph_objects():
    try:
        import plotly.graph_objects as go
    except ImportError as exc:
        raise VisualizationError(
            "Plotly is required for trajectory visualization; "
            "install with `python -m pip install -e '.[viz]'`"
        ) from exc
    return go


def _body_position(sample, body_id: str, units: DisplayUnits):
    body = next(body for body in sample.bodies if body.id == body_id)
    return convert_position(body.position_m, units)


def _dynamic_trace(go, sample, body_id: str, units: DisplayUnits, epoch_s: float):
    x, y, z = _body_position(sample, body_id, units)
    elapsed_s = sample.t_s - epoch_s
    return go.Scatter3d(
        x=[x],
        y=[y],
        z=[z],
        mode="markers+text",
        text=[BODY_LABELS[body_id]],
        textposition="top center",
        marker={
            "size": BODY_MARKER_SIZES_PX[body_id],
            "color": BODY_COLORS[body_id],
            "line": {"width": 1, "color": "#ffffff"},
        },
        name=f"{BODY_LABELS[body_id]} current",
        showlegend=False,
        hovertemplate=(
            f"{BODY_LABELS[body_id]}<br>delta t={elapsed_s:.9g} s"
            f"<br>delta t={elapsed_s / SECONDS_PER_DAY:.9g} d<extra></extra>"
        ),
    )


def build_trajectory_figure(
    archive: TrajectoryArchive,
    *,
    units: DisplayUnits = "au",
    frame_cap: int = 1000,
    center: Literal["barycentric"] = "barycentric",
):
    if center != "barycentric":
        raise VisualizationError("VIS1 only implements the barycentric display frame")
    if frame_cap > ABSOLUTE_MAX_FRAMES:
        raise VisualizationError(f"frame_cap cannot exceed {ABSOLUTE_MAX_FRAMES}")
    go = _plotly_graph_objects()
    frame_indices = select_frame_indices(archive, frame_cap)
    epoch_s = archive.samples[0].t_s
    axis_units = "AU" if units == "au" else "m"
    traces = []
    for body_id in CANONICAL_BODY_IDS:
        positions = [
            _body_position(sample, body_id, units) for sample in archive.samples
        ]
        traces.append(
            go.Scatter3d(
                x=[position[0] for position in positions],
                y=[position[1] for position in positions],
                z=[position[2] for position in positions],
                mode="lines",
                line={"color": BODY_COLORS[body_id], "width": 4},
                name=f"{BODY_LABELS[body_id]} trajectory",
                legendgroup=body_id,
                customdata=[
                    [sample.t_s - epoch_s, (sample.t_s - epoch_s) / SECONDS_PER_DAY]
                    for sample in archive.samples
                ],
                hovertemplate=(
                    f"{BODY_LABELS[body_id]} trajectory"
                    "<br>t=%{customdata[0]:.9g} s"
                    "<br>t=%{customdata[1]:.9g} d<extra></extra>"
                ),
            )
        )
    display_events = event_display_markers(archive)
    converted_events = [convert_position(event.position_m, units) for event in display_events]
    traces.append(
        go.Scatter3d(
            x=[position[0] for position in converted_events],
            y=[position[1] for position in converted_events],
            z=[position[2] for position in converted_events],
            mode="markers",
            marker={"size": 7, "color": "#e63946", "symbol": "diamond"},
            name="Runtime events (display estimates)",
            text=[event.safe_hover_text for event in display_events],
            hovertemplate="%{text}<extra></extra>",
        )
    )
    first_frame_sample = archive.samples[frame_indices[0]]
    traces.extend(
        _dynamic_trace(go, first_frame_sample, body_id, units, epoch_s)
        for body_id in CANONICAL_BODY_IDS
    )

    frames = []
    slider_steps = []
    if len(archive.samples) > 1:
        for sample_index in frame_indices:
            sample = archive.samples[sample_index]
            elapsed_s = sample.t_s - epoch_s
            frame_name = f"sample-{sample_index}"
            frames.append(
                go.Frame(
                    name=frame_name,
                    data=[
                        _dynamic_trace(go, sample, body_id, units, epoch_s)
                        for body_id in CANONICAL_BODY_IDS
                    ],
                    traces=list(DYNAMIC_TRACE_INDICES),
                )
            )
            slider_steps.append(
                {
                    "args": [
                        [frame_name],
                        {
                            "frame": {"duration": 0, "redraw": True},
                            "mode": "immediate",
                            "transition": {"duration": 0},
                        },
                    ],
                    "label": (
                        f"{elapsed_s:.6g} s / {elapsed_s / SECONDS_PER_DAY:.6g} d"
                    ),
                    "method": "animate",
                }
            )

    status_text = archive.manifest.status
    partial_text = " — PARTIAL TRAJECTORY" if archive.is_partial else ""
    title = (
        f"Four-body M2 trajectory — {html.escape(archive.manifest.world_id)} "
        f"[{status_text}{partial_text}]"
    )
    annotations = [
        {
            "text": (
                f"Frame: BARYCENTRIC_INERTIAL · coordinates: {axis_units} · "
                f"time shown as delta from epoch t0={epoch_s:.9g} s · "
                "marker sizes are screen symbols, not physical radii · "
                "animation timing is not physical simulation speed"
            ),
            "xref": "paper",
            "yref": "paper",
            "x": 0.0,
            "y": 1.08,
            "showarrow": False,
            "align": "left",
        }
    ]
    if archive.is_partial:
        annotations.append(
            {
                "text": (
                    f"WARNING: archive status is {status_text}; only {len(archive.samples)} "
                    "available complete samples are shown. This is not a completed orbit."
                ),
                "xref": "paper",
                "yref": "paper",
                "x": 0.0,
                "y": 1.02,
                "showarrow": False,
                "font": {"color": "#b00020", "size": 14},
                "align": "left",
            }
        )
    layout_options = {
        "title": title,
        "scene": {
            "xaxis_title": f"x ({axis_units})",
            "yaxis_title": f"y ({axis_units})",
            "zaxis_title": f"z ({axis_units})",
            "aspectmode": "data",
        },
        "legend": {"orientation": "h", "y": -0.12},
        "annotations": annotations,
        "margin": {"l": 0, "r": 0, "t": 120, "b": 80},
        "meta": {
            "source_status": archive.manifest.status,
            "partial": archive.is_partial,
            "input_sample_count": len(archive.samples),
            "displayed_frame_count": len(frames),
            "event_count": len(archive.events),
            "display_units": units,
            "coordinate_frame": "BARYCENTRIC_INERTIAL",
            "event_position_semantics": "piecewise_linear_sample_estimate",
        },
    }
    if frames:
        layout_options["updatemenus"] = [
            {
                "type": "buttons",
                "direction": "left",
                "x": 0.0,
                "y": 0.0,
                "buttons": [
                    {
                        "label": "Play",
                        "method": "animate",
                        "args": [
                            None,
                            {
                                "frame": {"duration": 80, "redraw": True},
                                "fromcurrent": True,
                                "transition": {"duration": 0},
                            },
                        ],
                    },
                    {
                        "label": "Pause",
                        "method": "animate",
                        "args": [
                            [None],
                            {
                                "frame": {"duration": 0, "redraw": False},
                                "mode": "immediate",
                                "transition": {"duration": 0},
                            },
                        ],
                    },
                ],
            }
        ]
        layout_options["sliders"] = [
            {
                "active": 0,
                "currentvalue": {"prefix": "Archived time: "},
                "pad": {"t": 45},
                "steps": slider_steps,
            }
        ]
    figure = go.Figure(data=traces, layout=go.Layout(**layout_options), frames=frames)
    return figure


def export_trajectory_html(
    run_dir: str | Path,
    output_path: str | Path,
    *,
    units: DisplayUnits = "au",
    frame_cap: int = 1000,
    max_input_samples: int = 100_000,
    center: Literal["barycentric"] = "barycentric",
    cdn: bool = False,
    overwrite: bool = False,
) -> dict[str, object]:
    output = Path(output_path)
    if output.suffix.lower() != ".html":
        raise VisualizationError("trajectory output path must end with .html")
    if output.exists() and not overwrite:
        raise VisualizationError(
            f"output already exists: {output}; pass --overwrite to replace it"
        )
    archive = load_m2_archive(run_dir, max_samples=max_input_samples)
    figure = build_trajectory_figure(
        archive, units=units, frame_cap=frame_cap, center=center
    )
    try:
        import plotly.io as pio
    except ImportError as exc:
        raise VisualizationError(
            "Plotly is required for trajectory visualization; install with `.[viz]`"
        ) from exc
    document = pio.to_html(
        figure,
        full_html=True,
        include_plotlyjs="cdn" if cdn else True,
        auto_play=False,
        config={"responsive": True, "displaylogo": False},
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, output)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return {
        "output": str(output),
        "status": archive.manifest.status,
        "partial": archive.is_partial,
        "input_samples": len(archive.samples),
        "displayed_frames": len(figure.frames),
        "events": len(archive.events),
        "units": units,
        "center": center,
        "plotly_js": "cdn" if cdn else "embedded",
        "file_size_bytes": output.stat().st_size,
    }
