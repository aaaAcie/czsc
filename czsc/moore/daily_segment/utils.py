# -*- coding: utf-8 -*-
"""日线级别线段模块的公共工具函数。"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Dict, List, Optional, Sequence, Tuple

from czsc.py.objects import RawBar

from ..objects import MooreSegment
from .objects import DailySegment


def seg_start_price(seg: MooreSegment) -> float:
    return seg.start_k.price


def seg_end_price(seg: MooreSegment) -> float:
    return seg.end_k.price


def seg_start_index(seg: MooreSegment) -> int:
    return seg.start_k.k_index


def seg_end_index(seg: MooreSegment) -> int:
    return seg.end_k.k_index


def collect_bars_by_index(segments: Sequence[MooreSegment]) -> Dict[int, RawBar]:
    bars_by_index: Dict[int, RawBar] = {}
    for seg in segments:
        start_idx = seg_start_index(seg)
        end_idx = seg_end_index(seg)
        if not seg.bars:
            bars_by_index.setdefault(start_idx, seg.start_k.raw_bar)
            bars_by_index.setdefault(end_idx, seg.end_k.raw_bar)
            continue

        for offset, bar in enumerate(seg.bars):
            bar_idx = start_idx + offset
            if bar_idx > end_idx:
                break
            bars_by_index[bar_idx] = bar

        bars_by_index.setdefault(start_idx, seg.start_k.raw_bar)
        bars_by_index.setdefault(end_idx, seg.end_k.raw_bar)
    return bars_by_index


def build_sma_array(bars_by_index: Dict[int, RawBar], window: int) -> List[Optional[float]]:
    if not bars_by_index:
        return []
    max_idx = max(bars_by_index)
    arr: List[Optional[float]] = [None] * (max_idx + 1)
    q: deque = deque(maxlen=window)
    for idx in range(max_idx + 1):
        bar = bars_by_index.get(idx)
        if not bar:
            q.clear()
            continue
        q.append(bar.close)
        if len(q) == window:
            arr[idx] = sum(q) / window
    return arr


def safe_ma_value(ma_array: Sequence[Optional[float]], idx: int) -> Optional[float]:
    if idx < 0 or idx >= len(ma_array):
        return None
    return ma_array[idx]


def slice_segments_from_anchor(
    segments: Sequence[MooreSegment],
    anchor_k_index: Optional[int],
    anchor_dt,
) -> Tuple[Optional[List[MooreSegment]], bool]:
    if anchor_k_index is None:
        return None, False

    danger = [seg for seg in segments if seg_start_index(seg) >= anchor_k_index]
    if not danger:
        return [], False

    if anchor_dt is not None and danger[0].start_k.dt != anchor_dt:
        return None, True
    return danger, False


def clone_completed_segments_snapshot(segments: Sequence[DailySegment]) -> List[DailySegment]:
    return deepcopy(list(segments))
