# -*- coding: utf-8 -*-
"""日线级别线段连续性规则。"""
from __future__ import annotations

from typing import Sequence


def check_daily_segment_continuity(
    segments: Sequence,
    completed_segments: Sequence,
    previous_end_k=None,
) -> bool:
    if not segments:
        return False
    if previous_end_k is not None:
        curr_start = segments[0].start_k
        return previous_end_k.k_index == curr_start.k_index and previous_end_k.dt == curr_start.dt
    if not completed_segments:
        return True
    prev_daily = completed_segments[-1]
    if prev_daily.cache.get("from_macro_swallow") and prev_daily.segments[0] is segments[0]:
        return True
    prev_end = completed_segments[-1].end_seg.end_k
    curr_start = segments[0].start_k
    return prev_end.k_index == curr_start.k_index and prev_end.dt == curr_start.dt

