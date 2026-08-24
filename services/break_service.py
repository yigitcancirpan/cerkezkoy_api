"""Planlı mola pencereleri için saf zaman aralığı yardımcıları."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable


def merge_windows(
    windows: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    ordered = sorted((start, end) for start, end in windows if end > start)
    merged: list[list[datetime]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return [(start, end) for start, end in merged]


def intersect_windows(
    left: Iterable[tuple[datetime, datetime]],
    right: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    left = merge_windows(left)
    right = merge_windows(right)
    intersections = []
    i = j = 0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if end > start:
            intersections.append((start, end))
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return intersections


def split_interval_by_breaks(
    start: datetime,
    end: datetime,
    break_windows: Iterable[tuple[datetime, datetime]],
) -> list[dict]:
    """Bir duruşu mola penceresi sınırlarında ardışık parçalara böler."""
    if end <= start:
        return []
    clipped = [
        (max(start, win_start), min(end, win_end))
        for win_start, win_end in break_windows
        if min(end, win_end) > max(start, win_start)
    ]
    clipped = merge_windows(clipped)
    boundaries = {start, end}
    for win_start, win_end in clipped:
        boundaries.add(win_start)
        boundaries.add(win_end)
    ordered = sorted(boundaries)

    raw = []
    for left, right in zip(ordered, ordered[1:]):
        if right <= left:
            continue
        midpoint = left + (right - left) / 2
        is_break = any(a <= midpoint < b for a, b in clipped)
        raw.append({"start": left, "end": right, "is_break": is_break})

    merged = []
    for segment in raw:
        if merged and merged[-1]["is_break"] == segment["is_break"]:
            merged[-1]["end"] = segment["end"]
        else:
            merged.append(dict(segment))
    return merged


def seconds(windows: Iterable[tuple[datetime, datetime]]) -> int:
    return int(sum((end - start).total_seconds() for start, end in windows))
