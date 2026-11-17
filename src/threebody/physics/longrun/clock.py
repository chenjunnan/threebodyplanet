from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from threebody.physics.longrun.models import LongRunSettings, SegmentIndexEntry


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


class PhysicalClock:
    """Integer-indexed schedule for the one authoritative SI physical clock."""

    def __init__(self, settings: LongRunSettings):
        self.settings = settings
        self._start = _decimal(settings.time.start_time_s)
        self._end = _decimal(settings.time.end_time_s)
        self._output = _decimal(settings.time.output_interval_s)
        self._segment = _decimal(settings.segmentation.simulated_duration_s)

    @property
    def output_count(self) -> int:
        intervals = int(
            ((self._end - self._start) / self._output).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        return intervals + 1

    @property
    def segment_count(self) -> int:
        return int(
            ((self._end - self._start) / self._segment).to_integral_value(
                rounding=ROUND_CEILING
            )
        )

    def output_time(self, index: int) -> float:
        if index < 0 or index >= self.output_count:
            raise IndexError(f"output index {index} is outside the physical schedule")
        if index == self.output_count - 1:
            return float(self._end)
        return float(self._start + index * self._output)

    def segment_bounds(self, index: int) -> tuple[float, float]:
        if index < 0 or index >= self.segment_count:
            raise IndexError(f"segment index {index} is outside the physical schedule")
        start = self._start + index * self._segment
        end = min(start + self._segment, self._end)
        return float(start), float(end)

    def first_output_after(self, t_s: float) -> int:
        time = _decimal(t_s)
        if time < self._start or time >= self._end:
            raise ValueError("time must be inside [start, end) for next output lookup")
        return int(
            ((time - self._start) / self._output).to_integral_value(
                rounding=ROUND_FLOOR
            )
        ) + 1


def build_segment_index(settings: LongRunSettings) -> tuple[SegmentIndexEntry, ...]:
    clock = PhysicalClock(settings)
    entries: list[SegmentIndexEntry] = []
    for segment_index in range(clock.segment_count):
        start, end = clock.segment_bounds(segment_index)
        first = 0 if segment_index == 0 else clock.first_output_after(start)
        if segment_index == clock.segment_count - 1:
            last = clock.output_count - 1
        else:
            start_decimal = _decimal(settings.time.start_time_s)
            interval = _decimal(settings.time.output_interval_s)
            last = int(
                ((_decimal(end) - start_decimal) / interval).to_integral_value(
                    rounding=ROUND_FLOOR
                )
            )
        if first > last:
            first_index = None
            last_index = None
        else:
            first_index = first
            last_index = last
        entries.append(
            SegmentIndexEntry(
                segment_index=segment_index,
                t_start_s=start,
                t_end_s=end,
                owns_start_sample=segment_index == 0,
                first_output_index=first_index,
                last_output_index=last_index,
            )
        )
    return tuple(entries)
