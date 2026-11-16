from __future__ import annotations

import html

import pytest

from threebody.errors import VisualizationError
from threebody.physics.nbody.models import SimulationEvent
from threebody.visualization.reader import load_m2_archive
from threebody.visualization.transforms import (
    AU_M,
    convert_position,
    event_display_marker,
    select_frame_indices,
)


def test_au_and_meter_coordinate_conversion_is_explicit():
    position = (AU_M, -2.0 * AU_M, 0.5 * AU_M)
    assert convert_position(position, "au") == pytest.approx((1.0, -2.0, 0.5))
    assert convert_position(position, "m") == position
    with pytest.raises(VisualizationError, match="unsupported display units"):
        convert_position(position, "km")  # type: ignore[arg-type]


def test_event_position_uses_declared_body_ids_and_linear_sample_interpolation(
    m2_viz_archive_factory,
):
    archive = load_m2_archive(m2_viz_archive_factory())
    event = archive.events[0]
    marker = event_display_marker(archive.samples, event)
    # At t=5: star-1 x=5 and planet-1 x=3020, so the declared-pair midpoint is 1512.5.
    assert marker.position_m == pytest.approx((1512.5, 3000.0, -750.0))
    assert marker.body_ids == ("star-1", "planet-1")
    assert marker.interpolation_method == "piecewise_linear_sample_estimate"
    assert "not exact physical contact" in marker.safe_hover_text


def test_event_hover_text_escapes_archive_strings(m2_viz_archive_factory):
    event = SimulationEvent(
        event_type="CLOSE_ENCOUNTER",
        t_s=5.0,
        body_ids=("star-1", "planet-1"),
        observed={},
        message="</script><script>alert(1)</script>",
        detection_method="<img src=x onerror=alert(2)>",
    )
    archive = load_m2_archive(m2_viz_archive_factory(events=(event,)))
    marker = event_display_marker(archive.samples, archive.events[0])
    assert "</script>" not in marker.safe_hover_text
    assert html.escape(event.message) in marker.safe_hover_text
    assert html.escape(event.detection_method) in marker.safe_hover_text


def test_frame_selection_preserves_endpoints_and_event_neighbors(
    m2_viz_archive_factory,
):
    event = SimulationEvent(
        event_type="COLLISION",
        t_s=15.0,
        body_ids=("star-2", "star-3"),
        observed={},
        message="test",
        detection_method="test",
    )
    archive = load_m2_archive(
        m2_viz_archive_factory(
            times=(0.0, 10.0, 20.0, 30.0, 40.0), events=(event,)
        )
    )
    indices = select_frame_indices(archive, frame_cap=4)
    assert indices == (0, 1, 2, 4)
    with pytest.raises(VisualizationError, match="at least 4 frames"):
        select_frame_indices(archive, frame_cap=3)


def test_single_sample_selects_one_frame(m2_viz_archive_factory):
    archive = load_m2_archive(m2_viz_archive_factory(times=(0.0,), events=()))
    assert select_frame_indices(archive, frame_cap=1) == (0,)


def test_frame_downsampling_is_distributed_across_full_interval(
    m2_viz_archive_factory,
):
    archive = load_m2_archive(
        m2_viz_archive_factory(times=tuple(float(value) for value in range(100)), events=())
    )
    indices = select_frame_indices(archive, frame_cap=10)
    assert len(indices) == 10
    assert indices[0] == 0 and indices[-1] == 99
    assert indices[5] > 40
