# -*- coding: utf-8 -*-
"""Pure visible-center detection helpers."""

from dataclasses import dataclass

from czsc.py.enum import Direction

from .geometry import is_direction_progress, is_reverse_progress


@dataclass(frozen=True)
class VisibilityResult:
    is_visible: bool
    upper_rail: float | None = None
    lower_rail: float | None = None


def detect_visible_center(
    *,
    direction: Direction,
    bars: list,
    center_anchor_idx: int,
    center_line_k_index: int,
    center_end_k_index: int,
) -> VisibilityResult:
    """Detect visible center status and optional MA5 rail correction."""
    if center_line_k_index < 0:
        return VisibilityResult(False)

    search_start = center_anchor_idx if center_anchor_idx >= 0 else 0
    window_to_confirm = bars[search_start : center_line_k_index + 1]
    if not window_to_confirm:
        return VisibilityResult(False)

    ext_idx = search_start
    if direction == Direction.Up:
        ext_val = window_to_confirm[0].high
        for i, bar in enumerate(window_to_confirm):
            if bar.high > ext_val:
                ext_val, ext_idx = bar.high, search_start + i
    else:
        ext_val = window_to_confirm[0].low
        for i, bar in enumerate(window_to_confirm):
            if bar.low < ext_val:
                ext_val, ext_idx = bar.low, search_start + i

    rev_count = 1
    last_k = bars[ext_idx]
    rev_end_idx = ext_idx
    for i in range(ext_idx + 1, center_line_k_index + 1):
        curr_k = bars[i]
        if is_reverse_progress(direction, curr_k, last_k):
            rev_count += 1
            last_k, rev_end_idx = curr_k, i

    fwd_start_idx = rev_end_idx if rev_count >= 3 else center_line_k_index
    fwd_count = 1
    last_k = bars[fwd_start_idx]
    fwd_formation_idx = -1
    for i in range(fwd_start_idx + 1, center_end_k_index + 1):
        curr_k = bars[i]
        if is_direction_progress(direction, curr_k, last_k):
            fwd_count += 1
            last_k = curr_k
            if fwd_count == 3:
                fwd_formation_idx = i
                break

    if fwd_formation_idx == -1:
        return VisibilityResult(False)

    obs_start = center_line_k_index
    obs_bars = bars[obs_start : fwd_formation_idx + 1]
    for i in range(obs_start + 1, fwd_formation_idx + 1):
        curr_ma5 = bars[i].cache.get("ma5", 0)
        prev_ma5 = bars[i - 1].cache.get("ma5", 0)
        if direction == Direction.Up:
            if curr_ma5 <= prev_ma5:
                return VisibilityResult(
                    True,
                    upper_rail=max(b.cache.get("ma5", 0) for b in obs_bars),
                )
        else:
            if curr_ma5 >= prev_ma5:
                return VisibilityResult(
                    True,
                    lower_rail=min(b.cache.get("ma5", 0) for b in obs_bars),
                )

    return VisibilityResult(False)
