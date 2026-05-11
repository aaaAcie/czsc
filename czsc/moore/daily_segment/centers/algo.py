# -*- coding: utf-8 -*-
"""日线级别线段中枢的纯算法模块。"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from czsc.py.enum import Direction

from ...objects import MooreSegment
from ..utils import seg_end_index, seg_start_index


def _turning_index(tk) -> int:
    return tk.turning_k_index if tk.turning_k_index is not None else tk.k_index


def _local_extremes(
    ma_array: Sequence[Optional[float]],
    start_idx: int,
    end_idx: int,
    want_peak: bool,
    sign: int,
) -> list[Tuple[int, float]]:
    left = min(start_idx, end_idx)
    right = max(start_idx, end_idx)
    found: list[Tuple[int, float]] = []
    for i in range(left, right + 1):
        if i <= 0 or i >= len(ma_array) - 1:
            continue
        values = (ma_array[i - 1], ma_array[i], ma_array[i + 1])
        if any(v is None for v in values):
            continue
        prev_norm = values[0] * sign
        curr_norm = values[1] * sign
        next_norm = values[2] * sign
        if want_peak:
            if curr_norm >= prev_norm and curr_norm >= next_norm and not (prev_norm == curr_norm == next_norm):
                found.append((i, values[1]))
        else:
            if curr_norm <= prev_norm and curr_norm <= next_norm and not (prev_norm == curr_norm == next_norm):
                found.append((i, values[1]))
    return found


def find_best_local_extreme(
    ma_array: Sequence[Optional[float]],
    start_idx: int,
    end_idx: int,
    want_peak: bool,
    sign: int,
) -> Tuple[Optional[int], Optional[float]]:
    extremes = _local_extremes(ma_array, start_idx, end_idx, want_peak, sign)
    if not extremes:
        return None, None
    if want_peak:
        return max(extremes, key=lambda x: (x[1] * sign, x[0]))
    return min(extremes, key=lambda x: (x[1] * sign, -x[0]))


def find_local_extreme(
    ma_array: Sequence[Optional[float]],
    start_idx: int,
    stop_idx: int,
    step: int,
    want_peak: bool,
    sign: int,
) -> Tuple[Optional[int], Optional[float]]:
    for i in range(start_idx, stop_idx, step):
        if i <= 0 or i >= len(ma_array) - 1:
            continue
        values = (ma_array[i - 1], ma_array[i], ma_array[i + 1])
        if any(v is None for v in values):
            continue
        left = values[0] * sign
        mid = values[1] * sign
        right = values[2] * sign
        if want_peak:
            if mid >= left and mid >= right and not (left == mid == right):
                return i, values[1]
        else:
            if mid <= left and mid <= right and not (left == mid == right):
                return i, values[1]
    return None, None


def check_ma34_overlap(
    seg: MooreSegment,
    a_norm: float,
    b_norm: float,
    ma_array: Sequence[Optional[float]],
    sign: int,
) -> bool:
    for i in range(seg_start_index(seg), seg_end_index(seg) + 1):
        if i >= len(ma_array):
            break
        ma_val = ma_array[i]
        if ma_val is None:
            continue
        norm_val = ma_val * sign
        if a_norm < norm_val < b_norm:
            return True
    return False


def check_price_reentry(
    seg: MooreSegment,
    a_norm: float,
    b_norm: float,
    sign: int,
) -> bool:
    for bar in seg.bars:
        low = min(bar.low, bar.high) * sign
        high = max(bar.low, bar.high) * sign
        if max(low, a_norm) < min(high, b_norm):
            return True
    start_norm = seg.start_k.price * sign
    end_norm = seg.end_k.price * sign
    low = min(start_norm, end_norm)
    high = max(start_norm, end_norm)
    return max(low, a_norm) < min(high, b_norm)


def find_b_point(
    seg_23: MooreSegment,
    seg_34: MooreSegment,
    ma_array: Sequence[Optional[float]],
    sign: int,
) -> Tuple[Optional[int], Optional[float]]:
    start_idx = _turning_index(seg_23.start_k)
    end_idx = _turning_index(seg_34.end_k)
    return find_best_local_extreme(ma_array, start_idx, end_idx, True, sign)


def find_a_point(
    seg_12: MooreSegment,
    b_idx: int,
    ma_array: Sequence[Optional[float]],
    sign: int,
) -> Tuple[Optional[int], Optional[float]]:
    start_idx = _turning_index(seg_12.start_k)
    return find_best_local_extreme(ma_array, start_idx, b_idx, False, sign)


def find_d_point(
    b_idx: int,
    scan_end_index: int,
    ma_array: Sequence[Optional[float]],
    sign: int,
) -> Tuple[Optional[int], Optional[float]]:
    return find_best_local_extreme(ma_array, b_idx + 1, scan_end_index, True, sign)


def find_c_point(
    b_idx: int,
    d_idx: int,
    ma_array: Sequence[Optional[float]],
    sign: int,
) -> Tuple[Optional[int], Optional[float]]:
    return find_best_local_extreme(ma_array, b_idx + 1, d_idx, False, sign)


def find_center(
    segments: Sequence[MooreSegment],
    ma_array: Sequence[Optional[float]],
    trend_direction: Optional[Direction] = None,
) -> Optional[dict]:
    i = 0
    while i + 3 < len(segments):
        seg_12 = segments[i]
        seg_23 = segments[i + 1]
        seg_34 = segments[i + 2]
        seg_45 = segments[i + 3]
        seg_56 = segments[i + 4] if i + 4 < len(segments) else None

        direction = trend_direction or seg_12.direction
        sign = 1 if direction == Direction.Down else -1

        b_idx, b_val = find_b_point(seg_23, seg_34, ma_array, sign)
        if b_val is None:
            i += 2
            continue

        a_idx, a_val = find_a_point(seg_12, b_idx, ma_array, sign)
        if a_val is None:
            i += 2
            continue

        a_norm = a_val * sign
        b_norm = b_val * sign
        if not check_price_reentry(seg_45, a_norm, b_norm, sign):
            return {
                "high": max(a_val, b_val),
                "low": min(a_val, b_val),
                "overlap_type": 0,
                "center_kind": "turning",
                "status": "FINAL",
                "segments": list(segments[i : i + 4]),
                "points": {"A": (a_idx, a_val), "B": (b_idx, b_val)},
            }

        ma34_in_ab = check_ma34_overlap(seg_45, a_norm, b_norm, ma_array, sign)
        if not ma34_in_ab:
            return {
                "high": max(a_val, b_val),
                "low": min(a_val, b_val),
                "overlap_type": 1,
                "center_kind": "trend_class",
                "status": "FINAL",
                "segments": list(segments[i : i + 4]),
                "points": {"A": (a_idx, a_val), "B": (b_idx, b_val)},
            }

        if not seg_56:
            return {
                "high": max(a_val, b_val),
                "low": min(a_val, b_val),
                "overlap_type": 3,
                "center_kind": "trend_class",
                "status": "TEMPORARY",
                "segments": list(segments[i : i + 4]),
                "points": {"A": (a_idx, a_val), "B": (b_idx, b_val)},
            }

        d_search_end = seg_end_index(seg_56)
        d_idx, d_val = find_d_point(b_idx, d_search_end, ma_array, sign)
        if d_val is None:
            i += 2
            continue

        c_idx, c_val = find_c_point(b_idx, d_idx, ma_array, sign)
        if c_val is None:
            i += 2
            continue

        upper_norm = min(b_val * sign, d_val * sign)
        lower_norm = max(a_val * sign, c_val * sign)
        if lower_norm >= upper_norm:
            i += 2
            continue
        high = max(upper_norm * sign, lower_norm * sign)
        low = min(upper_norm * sign, lower_norm * sign)

        return {
            "high": high,
            "low": low,
            "overlap_type": 3,
            "center_kind": "trend_class",
            "status": "FINAL",
            "segments": list(segments[i : i + 5]),
            "points": {
                "A": (a_idx, a_val),
                "B": (b_idx, b_val),
                "C": (c_idx, c_val),
                "D": (d_idx, d_val),
            },
        }

    return None
