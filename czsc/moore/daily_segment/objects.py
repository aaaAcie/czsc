# -*- coding: utf-8 -*-
"""日线级别线段模块的数据对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from ..objects import MooreSegment
from czsc.py.enum import Direction


def _seg_start_index(seg: MooreSegment) -> int:
    return seg.start_k.k_index


def _seg_end_index(seg: MooreSegment) -> int:
    return seg.end_k.k_index


def _turning_index(tk) -> int:
    return tk.turning_k_index if tk.turning_k_index is not None else tk.k_index


def _turning_dt(tk) -> datetime:
    turning_k = getattr(tk, "turning_k", None)
    if turning_k is not None:
        return turning_k.dt
    return tk.dt


@dataclass
class DailySegmentCenter:
    """日线级别线段中枢候选 / 活跃态对象。"""

    segments: List[MooreSegment]
    high: float
    low: float
    overlap_type: int
    is_active: bool = True
    status: str = "FINAL"
    points: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)

    @property
    def start_dt(self) -> datetime:
        return _turning_dt(self.segments[0].start_k)

    @property
    def end_dt(self) -> datetime:
        return _turning_dt(self.segments[-1].end_k)

    @property
    def start_index(self) -> int:
        return _seg_start_index(self.segments[0])

    @property
    def end_index(self) -> int:
        return max(_seg_end_index(seg) for seg in self.segments)

    def check_price_overlap(self, other: "DailySegmentCenter") -> bool:
        return max(self.low, other.low) <= min(self.high, other.high)

    def endpoint_keys(self) -> set:
        endpoint_keys = set()
        for seg in self.segments:
            endpoint_keys.add(
                (
                    seg.start_k.k_index,
                    _turning_dt(seg.start_k),
                    seg.start_k.price,
                    seg.start_k.mark.value,
                )
            )
            endpoint_keys.add(
                (
                    seg.end_k.k_index,
                    _turning_dt(seg.end_k),
                    seg.end_k.price,
                    seg.end_k.mark.value,
                )
            )
        return endpoint_keys

    def shared_endpoint_count(self, other: "DailySegmentCenter") -> int:
        self_axis = self.cache.get("endpoint_axis")
        other_axis = other.cache.get("endpoint_axis")
        self_span = self.cache.get("endpoint_span")
        other_span = other.cache.get("endpoint_span")
        if self_axis and self_axis == other_axis and self_span and other_span:
            left = max(self_span[0], other_span[0])
            right = min(self_span[1], other_span[1])
            return max(0, right - left + 1)
        return len(self.endpoint_keys() & other.endpoint_keys())

    def check_segment_overlap(self, other: "DailySegmentCenter", min_shared_endpoints: int = 3) -> bool:
        return self.shared_endpoint_count(other) >= min_shared_endpoints


@dataclass
class DailySegment:
    """由多个 30F 宏观线段合成的日线级别线段。"""

    symbol: str
    direction: Direction
    start_seg: MooreSegment
    end_seg: MooreSegment
    segments: List[MooreSegment] = field(default_factory=list)
    centers: List[DailySegmentCenter] = field(default_factory=list)
    cache: dict = field(default_factory=dict)

    @property
    def sdt(self) -> datetime:
        return self.confirm_start_dt

    @property
    def edt(self) -> datetime:
        return self.confirm_end_dt

    @property
    def price_start_dt(self) -> datetime:
        return self.start_seg.start_k.dt

    @property
    def price_end_dt(self) -> datetime:
        return self.end_seg.end_k.dt

    @property
    def confirm_start_dt(self) -> datetime:
        return _turning_dt(self.start_seg.start_k)

    @property
    def confirm_end_dt(self) -> datetime:
        return _turning_dt(self.end_seg.end_k)

    @property
    def confirm_start_index(self) -> int:
        return _turning_index(self.start_seg.start_k)

    @property
    def confirm_end_index(self) -> int:
        return _turning_index(self.end_seg.end_k)

    @property
    def start_price(self) -> float:
        return self.start_seg.start_k.price

    @property
    def end_price(self) -> float:
        return self.end_seg.end_k.price

    @property
    def power(self) -> float:
        return abs(self.end_price - self.start_price)
