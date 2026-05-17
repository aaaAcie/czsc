# -*- coding: utf-8 -*-
"""MA 交叉与转折确认 K 时间锚。"""
from __future__ import annotations

from typing import Optional, Sequence

from ..utils import seg_end_price, seg_start_price


def turning_index(tk) -> int:
    return tk.turning_k_index if tk.turning_k_index is not None else tk.k_index


def ma_relation_state_from_values(fast, slow) -> int:
    if fast is None or slow is None:
        return 0
    if fast > slow:
        return 1
    if fast < slow:
        return -1
    return 0


def ma_relation_state_at(idx: int, ma_fast, ma_slow) -> int:
    if idx < 0 or idx >= len(ma_fast) or idx >= len(ma_slow):
        return 0
    return ma_relation_state_from_values(ma_fast[idx], ma_slow[idx])


def ma_relation_state_for_tk(tk, ma_fast, ma_slow) -> int:
    for bar in (getattr(tk, "turning_k", None), getattr(tk, "trigger_k", None), getattr(tk, "raw_bar", None)):
        if bar is None:
            continue
        state = ma_relation_state_from_values(bar.cache.get("ma34"), bar.cache.get("ma170"))
        if state != 0:
            return state
    return ma_relation_state_at(turning_index(tk), ma_fast, ma_slow)


def has_ma_cross_between(start_idx: int, end_idx: int, ma_fast, ma_slow) -> bool:
    if start_idx > end_idx:
        start_idx, end_idx = end_idx, start_idx
    prev_state = 0
    for idx in range(start_idx, end_idx + 1):
        state = ma_relation_state_at(idx, ma_fast, ma_slow)
        if state == 0:
            continue
        if prev_state != 0 and state != prev_state:
            return True
        prev_state = state
    return False


def check_ma_cross_correlation(
    segments: Sequence,
    ma_fast,
    ma_slow,
    lag_segment: Optional[object] = None,
) -> bool:
    if not segments:
        return False
    start_idx = turning_index(segments[0].start_k)
    end_tk = lag_segment.end_k if lag_segment is not None else segments[-1].end_k
    end_idx = turning_index(end_tk)
    return has_ma_cross_between(start_idx, end_idx, ma_fast, ma_slow)


def ma_reverses_against_window(window: Sequence, ma_array: Sequence[Optional[float]]) -> bool:
    if not window or not ma_array:
        return False

    start_price = seg_start_price(window[0])
    end_price = seg_end_price(window[-1])
    if end_price == start_price:
        return False

    start_idx = turning_index(window[0].start_k)
    end_idx = turning_index(window[-1].end_k)
    if start_idx > end_idx:
        start_idx, end_idx = end_idx, start_idx

    vals = [
        ma_array[idx]
        for idx in range(start_idx, end_idx + 1)
        if 0 <= idx < len(ma_array) and ma_array[idx] is not None
    ]
    if len(vals) < 2:
        return False

    if end_price > start_price:
        return any(curr < prev for prev, curr in zip(vals, vals[1:]))
    return any(curr > prev for prev, curr in zip(vals, vals[1:]))

