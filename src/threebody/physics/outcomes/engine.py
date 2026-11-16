from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from threebody.physics.outcomes.adapter import M4Inputs
from threebody.physics.outcomes.config import M4Settings
from threebody.physics.outcomes.models import (
    EnvironmentStatistics,
    ExposureEpisode,
    OutcomeEvent,
    WorldClassification,
)


def _episodes(
    times: list[float],
    values: list[float],
    threshold: float,
    predicate: Callable[[float, float], bool],
    kind: str,
) -> tuple[ExposureEpisode, ...]:
    raw: list[tuple[float, float, float]] = []
    for t0, t1, y0, y1 in zip(
        times[:-1], times[1:], values[:-1], values[1:], strict=True
    ):
        inside0 = predicate(y0, threshold)
        inside1 = predicate(y1, threshold)
        uncertainty = t1 - t0
        if inside0 and inside1:
            raw.append((t0, t1, uncertainty))
        elif inside0 != inside1:
            if y1 == y0:
                crossing = t0
            else:
                fraction = (threshold - y0) / (y1 - y0)
                crossing = t0 + min(max(fraction, 0.0), 1.0) * (t1 - t0)
            raw.append((t0, crossing, uncertainty) if inside0 else (crossing, t1, uncertainty))
    merged: list[tuple[float, float, float]] = []
    for start, end, uncertainty in raw:
        if end <= start:
            continue
        if merged and math.isclose(start, merged[-1][1], abs_tol=1.0e-9):
            merged[-1] = (merged[-1][0], end, max(merged[-1][2], uncertainty))
        else:
            merged.append((start, end, uncertainty))
    return tuple(
        ExposureEpisode(
            kind=kind,
            t_start_s=start,
            t_end_s=end,
            duration_s=end - start,
            threshold_k=threshold,
            detection_method="piecewise_linear_threshold_crossing",
            sampling_uncertainty_s=uncertainty,
        )
        for start, end, uncertainty in merged
    )


def _environment_statistics(
    inputs: M4Inputs, settings: M4Settings
) -> tuple[EnvironmentStatistics, tuple[ExposureEpisode, ...], tuple[OutcomeEvent, ...]]:
    if inputs.flux:
        coverage_start = inputs.flux[0].t_s
        coverage_end = inputs.flux[-1].t_s
        flux_values = [point.planet_center_flux_w_m2 for point in inputs.flux]
        global_flux = [point.globally_averaged_incoming_w_m2 for point in inputs.flux]
        flux_times = [point.t_s for point in inputs.flux]
        integrated_energy = float(np.trapezoid(global_flux, flux_times))
        flux_mean = float(np.mean(flux_values))
        flux_variation = (
            (max(flux_values) - min(flux_values)) / flux_mean if flux_mean > 0.0 else None
        )
    else:
        coverage_start = None
        coverage_end = None
        flux_values = []
        global_flux = []
        flux_times = []
        integrated_energy = 0.0
        flux_variation = None

    c0_times = [point.t_s for point in inputs.c0]
    temperatures = [point.temperature_k for point in inputs.c0]
    cold = _episodes(
        c0_times,
        temperatures,
        settings.classification.cold_threshold_k,
        lambda value, threshold: value <= threshold,
        "COLD",
    )
    hot = _episodes(
        c0_times,
        temperatures,
        settings.classification.hot_threshold_k,
        lambda value, threshold: value >= threshold,
        "HOT",
    )
    rates = [
        abs((second - first) / (t1 - t0))
        for first, second, t0, t1 in zip(
            temperatures[:-1],
            temperatures[1:],
            c0_times[:-1],
            c0_times[1:],
            strict=True,
        )
    ]
    c1_cold = [
        sum(value <= settings.classification.cold_threshold_k for value in point.temperature_k)
        / len(point.temperature_k)
        for point in inputs.c1
    ]
    c1_hot = [
        sum(value >= settings.classification.hot_threshold_k for value in point.temperature_k)
        / len(point.temperature_k)
        for point in inputs.c1
    ]
    statistics = EnvironmentStatistics(
        coverage_start_s=coverage_start,
        coverage_end_s=coverage_end,
        coverage_duration_s=(
            0.0 if coverage_start is None else coverage_end - coverage_start
        ),
        flux_sample_count=len(inputs.flux),
        c0_sample_count=len(inputs.c0),
        c1_sample_count=len(inputs.c1),
        sampled_flux_min_w_m2=min(flux_values) if flux_values else None,
        sampled_flux_max_w_m2=max(flux_values) if flux_values else None,
        sampled_c0_min_k=min(temperatures) if temperatures else None,
        sampled_c0_max_k=max(temperatures) if temperatures else None,
        cold_duration_s=sum(episode.duration_s for episode in cold),
        hot_duration_s=sum(episode.duration_s for episode in hot),
        longest_cold_episode_s=max((episode.duration_s for episode in cold), default=0.0),
        longest_hot_episode_s=max((episode.duration_s for episode in hot), default=0.0),
        maximum_c0_temperature_rate_k_s=max(rates) if rates else None,
        integrated_global_incoming_energy_j_m2=integrated_energy,
        maximum_c1_cold_band_fraction=max(c1_cold) if c1_cold else None,
        maximum_c1_hot_band_fraction=max(c1_hot) if c1_hot else None,
        flux_variation_fraction=flux_variation,
        interval_semantics=(
            "sampled extrema; durations use piecewise-linear threshold crossings; "
            "radiative energy uses trapezoidal interval integration"
        ),
    )
    events: list[OutcomeEvent] = []
    event_index = 0
    for episode in (*cold, *hot):
        if episode.duration_s < settings.classification.persistence_min_s:
            continue
        event_index += 1
        events.append(
            OutcomeEvent(
                event_id=f"m4-environment-{event_index:06d}",
                event_type=f"{episode.kind}_EXPOSURE",
                source="M4",
                t_start_s=episode.t_start_s,
                t_end_s=episode.t_end_s,
                observed={
                    "duration_s": episode.duration_s,
                    "threshold_k": episode.threshold_k,
                },
                detection_method=episode.detection_method,
                sampling_uncertainty_s=episode.sampling_uncertainty_s,
                terminal=False,
                message=(
                    f"C0 {episode.kind.lower()} threshold exposure in the sampled model; "
                    "not a local-weather or habitability conclusion"
                ),
            )
        )
    for first, second in zip(inputs.flux, inputs.flux[1:], strict=False):
        baseline = max(first.planet_center_flux_w_m2, 1.0e-300)
        fraction = abs(second.planet_center_flux_w_m2 - first.planet_center_flux_w_m2) / baseline
        if fraction < settings.classification.rapid_flux_change_fraction:
            continue
        event_index += 1
        events.append(
            OutcomeEvent(
                event_id=f"m4-environment-{event_index:06d}",
                event_type="RAPID_IRRADIANCE_CHANGE",
                source="M4",
                t_start_s=first.t_s,
                t_end_s=second.t_s,
                observed={"endpoint_fractional_change": fraction},
                detection_method="adjacent_sample_endpoint_fraction",
                sampling_uncertainty_s=second.t_s - first.t_s,
                terminal=False,
                message=(
                    "large sampled irradiance change; crossing behavior inside interval is unknown"
                ),
            )
        )
    return statistics, (*cold, *hot), tuple(events)


def _dynamic_events(inputs: M4Inputs) -> tuple[OutcomeEvent, ...]:
    events: list[OutcomeEvent] = []
    for index, outcome in enumerate(inputs.initial_outcomes, 1):
        events.append(
            OutcomeEvent(
                event_id=f"g2-initial-{index:06d}",
                event_type="INITIAL_CONTACT",
                source="G2",
                t_start_s=float(outcome["t_s"]),
                t_end_s=float(outcome["t_s"]),
                body_ids=tuple(outcome["body_ids"]),
                observed={
                    key: value
                    for key, value in outcome.items()
                    if key
                    in {"center_distance_m", "contact_distance_m", "surface_gap_m"}
                },
                detection_method=str(outcome.get("detection_method", "initial_sphere_geometry")),
                sampling_uncertainty_s=0.0,
                terminal=True,
                message="initial contact at t=0; no IAS15 or thermal continuation is inferred",
            )
        )
    if inputs.m2_archive is None:
        return tuple(events)
    for index, event in enumerate(inputs.m2_archive.events, 1):
        if event.event_type == "COLLISION":
            event_type = (
                "PLANET_COLLISION" if "planet-1" in event.body_ids else "STELLAR_COLLISION"
            )
            terminal = True
        else:
            event_type = event.event_type
            terminal = False
        events.append(
            OutcomeEvent(
                event_id=f"m2-runtime-{index:06d}",
                event_type=event_type,
                source="M2",
                t_start_s=event.t_s,
                t_end_s=event.t_s,
                body_ids=event.body_ids,
                observed=event.observed,
                detection_method=event.detection_method,
                sampling_uncertainty_s=0.0,
                terminal=terminal,
                message=event.message,
            )
        )
    return tuple(events)


def assess_world(
    inputs: M4Inputs, settings: M4Settings
) -> tuple[
    EnvironmentStatistics,
    tuple[ExposureEpisode, ...],
    tuple[OutcomeEvent, ...],
    WorldClassification,
]:
    statistics, episodes, environment_events = _environment_statistics(inputs, settings)
    dynamic_events = _dynamic_events(inputs)
    timeline = tuple(
        sorted(
            (*dynamic_events, *environment_events),
            key=lambda event: (event.t_start_s, event.event_id),
        )
    )
    initial_contact = bool(inputs.initial_outcomes)
    collisions = [event for event in dynamic_events if event.event_type.endswith("COLLISION")]
    planet_collision = any(event.event_type == "PLANET_COLLISION" for event in collisions)
    stellar_collision = any(event.event_type == "STELLAR_COLLISION" for event in collisions)
    escape = any(event.event_type == "ESCAPE_CANDIDATE" for event in dynamic_events)
    if initial_contact:
        scientific_status = "INITIAL_CONTACT"
        dynamical = "INITIAL_CONTACT"
    elif inputs.m2_archive is None:
        scientific_status = "NUMERICAL_INPUT_UNFIT"
        dynamical = "NOT_AVAILABLE"
    else:
        scientific_status = "S0_VALID"
        status = inputs.m2_archive.manifest.status
        if status == "COLLISION":
            dynamical = "COLLISION_TERMINATED"
        elif status == "STOPPED":
            dynamical = "INTEGRATION_STOPPED"
        elif status == "FAILED":
            dynamical = "INTEGRATION_FAILED"
        elif escape:
            dynamical = "ESCAPE_CANDIDATE"
        else:
            dynamical = "COMPLETED_OBSERVATION_WINDOW"

    labels: list[str] = []
    if inputs.m3_manifest is not None and inputs.m3_manifest.get("status") == "FAILED":
        labels.extend(("THERMAL_SOLVER_FAILED", "DATA_INSUFFICIENT"))
    elif inputs.m3_manifest is None or not inputs.flux:
        labels.append("DATA_INSUFFICIENT")
    else:
        if statistics.longest_cold_episode_s >= settings.classification.persistence_min_s:
            labels.append("FROZEN_EXPOSURE")
        if statistics.longest_hot_episode_s >= settings.classification.persistence_min_s:
            labels.append("HEAT_EXPOSURE")
        if (
            statistics.flux_variation_fraction is not None
            and statistics.flux_variation_fraction
            >= settings.classification.high_flux_variation_fraction
        ):
            labels.append("HIGH_IRRADIANCE_VARIABILITY")
        if not any(label in labels for label in ("FROZEN_EXPOSURE", "HEAT_EXPOSURE")):
            labels.append("TEMPERATE_INTERVAL")
    if settings.classification.require_complete_thermal_window and inputs.m2_archive is not None:
        if (
            statistics.coverage_start_s != inputs.m2_archive.samples[0].t_s
            or statistics.coverage_end_s != inputs.m2_archive.samples[-1].t_s
        ):
            labels.append("THERMAL_COVERAGE_INCOMPLETE")

    if planet_collision:
        planet_status = "collision_involved"
    elif initial_contact or stellar_collision or dynamical in {
        "INTEGRATION_FAILED",
        "INTEGRATION_STOPPED",
        "NOT_AVAILABLE",
    }:
        planet_status = "unresolved"
    else:
        planet_status = "observed"
    extreme = any(label in labels for label in ("FROZEN_EXPOSURE", "HEAT_EXPOSURE"))
    severity = (
        3.0 * planet_collision
        + 2.0 * stellar_collision
        + 1.0 * escape
        + 1.0 * extreme
        + 0.5 * ("HIGH_IRRADIANCE_VARIABILITY" in labels)
    )
    preference = settings.selection.preference
    score = severity if preference in {"chaotic", "catastrophic"} else -severity
    if preference == "neutral":
        score = 0.0
    selected = not (
        (settings.selection.survival_required and planet_status != "observed")
        or (collisions and not settings.selection.include_collision_terminated)
        or (escape and not settings.selection.include_escape_candidates)
        or (
            dynamical not in {"COMPLETED_OBSERVATION_WINDOW", "ESCAPE_CANDIDATE"}
            and not settings.selection.include_unstable_worlds
        )
    )
    classification = WorldClassification(
        scientific_input_status=scientific_status,
        dynamical_outcome=dynamical,
        environmental_outcomes=tuple(labels),
        planet_status=planet_status,
        collision_with_planet=planet_collision,
        stellar_collision=stellar_collision,
        ejection_candidate=escape,
        thermal_exposure_extreme=extreme,
        narrative_limits=(
            "classification covers only the archived observation window",
            "escape candidate is not proof of permanent escape",
            "grey-body and latitude-band temperatures are not local weather or habitability",
            "collision aftermath is unmodeled and never extrapolated",
        ),
        selection_preference=preference,
        selection_score=score,
        selected_by_preferences=selected,
    )
    return statistics, episodes, timeline, classification
