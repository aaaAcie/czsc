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
        return self.segments[0].sdt

    @property
    def end_dt(self) -> datetime:
        return self.segments[-1].edt

    @property
    def start_index(self) -> int:
        return _seg_start_index(self.segments[0])

    @property
    def end_index(self) -> int:
        return max(_seg_end_index(seg) for seg in self.segments)

    def check_price_overlap(self, other: "DailySegmentCenter") -> bool:
        return max(self.low, other.low) <= min(self.high, other.high)

    def check_segment_overlap(self, other: "DailySegmentCenter") -> bool:
        a_keys = {(seg.sdt, seg.edt) for seg in self.segments}
        b_keys = {(seg.sdt, seg.edt) for seg in other.segments}
        return bool(a_keys & b_keys)


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
        return self.start_seg.sdt

    @property
    def edt(self) -> datetime:
        return self.end_seg.edt

    @property
    def start_price(self) -> float:
        return self.start_seg.start_k.price

    @property
    def end_price(self) -> float:
        return self.end_seg.end_k.price

    @property
    def power(self) -> float:
        return abs(self.end_price - self.start_price)
