# -*- coding: utf-8 -*-
"""日线级别线段中枢的纯算法模块。"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from czsc.py.enum import Direction

from ...objects import MooreSegment
from ..utils import seg_end_index, seg_start_index


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
    end_23 = seg_end_index(seg_23)
    start_23 = seg_start_index(seg_23)
    b_idx, b_val = find_local_extreme(ma_array, end_23, start_23 - 1, -1, True, sign)
    if b_val is not None:
        return b_idx, b_val
    right_scan_end_index = seg_end_index(seg_34)
    return find_local_extreme(ma_array, end_23, right_scan_end_index + 1, 1, True, sign)


def find_a_point(
    seg_12: MooreSegment,
    b_idx: int,
    ma_array: Sequence[Optional[float]],
    sign: int,
) -> Tuple[Optional[int], Optional[float]]:
    return find_local_extreme(ma_array, b_idx, seg_start_index(seg_12) - 1, -1, False, sign)


def find_d_point(
    b_idx: int,
    scan_end_index: int,
    ma_array: Sequence[Optional[float]],
    sign: int,
) -> Tuple[Optional[int], Optional[float]]:
    return find_local_extreme(ma_array, b_idx + 1, scan_end_index + 1, 1, True, sign)


def find_c_point(
    b_idx: int,
    d_idx: int,
    ma_array: Sequence[Optional[float]],
    sign: int,
) -> Tuple[Optional[int], Optional[float]]:
    return find_local_extreme(ma_array, b_idx + 1, d_idx + 1, 1, False, sign)


def find_center(
    segments: Sequence[MooreSegment],
    ma_array: Sequence[Optional[float]],
) -> Optional[dict]:
    i = 0
    while i + 3 < len(segments):
        seg_12 = segments[i]
        seg_23 = segments[i + 1]
        seg_34 = segments[i + 2]
        seg_45 = segments[i + 3]
        seg_56 = segments[i + 4] if i + 4 < len(segments) else None

        sign = 1 if seg_12.direction == Direction.Down else -1

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
            i += 2
            continue

        ma34_in_ab = check_ma34_overlap(seg_45, a_norm, b_norm, ma_array, sign)
        if not ma34_in_ab:
            return {
                "high": max(a_val, b_val),
                "low": min(a_val, b_val),
                "overlap_type": 1,
                "status": "FINAL",
                "segments": list(segments[i : i + 4]),
                "points": {"A": (a_idx, a_val), "B": (b_idx, b_val)},
            }

        if not seg_56:
            return {
                "high": max(a_val, b_val),
                "low": min(a_val, b_val),
                "overlap_type": 3,
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

        center_high = min(b_val * sign, d_val * sign) * sign
        center_low = max(a_val * sign, c_val * sign) * sign
        high = max(center_high, center_low)
        low = min(center_high, center_low)
        if low >= high:
            i += 2
            continue

        return {
            "high": high,
            "low": low,
            "overlap_type": 3,
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
