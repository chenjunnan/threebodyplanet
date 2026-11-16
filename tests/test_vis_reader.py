from __future__ import annotations

import json

import pytest

from threebody.common.json import write_json_atomic
from threebody.errors import VisualizationError
from threebody.visualization.reader import CANONICAL_BODY_IDS, load_m2_archive


def test_reader_loads_valid_archive_and_marks_partial_status(m2_viz_archive_factory):
    completed = load_m2_archive(m2_viz_archive_factory())
    assert len(completed.samples) == 3
    assert len(completed.events) == 1
    assert tuple(body.id for body in completed.samples[0].bodies) == CANONICAL_BODY_IDS
    assert completed.is_partial is False
    stopped = load_m2_archive(
        m2_viz_archive_factory(status="STOPPED", name="stopped")
    )
    assert stopped.is_partial is True
    assert stopped.manifest.error == "partial"


@pytest.mark.parametrize("content", ["\n", "not-json\n"])
def test_reader_rejects_blank_or_damaged_jsonl(m2_viz_archive_factory, content):
    directory = m2_viz_archive_factory()
    (directory / "trajectory.jsonl").write_text(content, encoding="utf-8")
    with pytest.raises(VisualizationError, match="trajectory line"):
        load_m2_archive(directory)


def test_reader_rejects_nonfinite_values_and_input_limit(m2_viz_archive_factory):
    directory = m2_viz_archive_factory()
    path = directory / "trajectory.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[1]["bodies"][0]["position_m"][0] = float("nan")
    path.write_text(
        "".join(json.dumps(record, allow_nan=True) + "\n" for record in records),
        encoding="utf-8",
    )
    with pytest.raises(VisualizationError, match="invalid trajectory line"):
        load_m2_archive(directory)

    limited = m2_viz_archive_factory(name="limited")
    with pytest.raises(VisualizationError, match="max_input_samples=2"):
        load_m2_archive(limited, max_samples=2)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda records: records[1].update({"t_s": 0.0}), "strictly increasing"),
        (
            lambda records: records[1]["bodies"][0].update({"id": "other"}),
            "body IDs/order",
        ),
    ],
)
def test_reader_rejects_time_and_body_order_corruption(
    m2_viz_archive_factory, mutation, message
):
    directory = m2_viz_archive_factory()
    path = directory / "trajectory.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    mutation(records)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    with pytest.raises(VisualizationError, match=message):
        load_m2_archive(directory)


def test_reader_rejects_manifest_counts_endpoint_and_out_of_range_event(
    m2_viz_archive_factory,
):
    directory = m2_viz_archive_factory()
    manifest_path = directory / "simulation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sample_count"] = 99
    write_json_atomic(manifest_path, manifest)
    with pytest.raises(VisualizationError, match="sample_count"):
        load_m2_archive(directory)

    endpoint = m2_viz_archive_factory(name="endpoint")
    manifest_path = endpoint / "simulation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["t_final_s"] = 21.0
    write_json_atomic(manifest_path, manifest)
    with pytest.raises(VisualizationError, match="final time"):
        load_m2_archive(endpoint)

    event_directory = m2_viz_archive_factory(name="event")
    events_path = event_directory / "events.json"
    events = json.loads(events_path.read_text())
    events[0]["t_s"] = 21.0
    write_json_atomic(events_path, events)
    with pytest.raises(VisualizationError, match="outside trajectory interval"):
        load_m2_archive(event_directory)


def test_reader_accepts_single_complete_sample_and_zero_events(m2_viz_archive_factory):
    directory = m2_viz_archive_factory(times=(0.0,), events=())
    archive = load_m2_archive(directory)
    assert len(archive.samples) == 1
    assert archive.events == ()


def test_reader_rejects_missing_files_and_invalid_utf8(tmp_path, m2_viz_archive_factory):
    with pytest.raises(VisualizationError, match="does not exist"):
        load_m2_archive(tmp_path / "missing")
    missing_events = m2_viz_archive_factory(name="missing-events")
    (missing_events / "events.json").unlink()
    with pytest.raises(VisualizationError, match="cannot read M2 events"):
        load_m2_archive(missing_events)
    invalid_utf8 = m2_viz_archive_factory(name="invalid-utf8")
    (invalid_utf8 / "trajectory.jsonl").write_bytes(b"\xff\xfe\n")
    with pytest.raises(VisualizationError, match="cannot read M2 trajectory"):
        load_m2_archive(invalid_utf8)
