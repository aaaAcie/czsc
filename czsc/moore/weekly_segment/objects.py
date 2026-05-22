# -*- coding: utf-8 -*-
"""周线级别线段模块的数据对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from czsc.py.enum import Direction

from ..daily_segment.objects import DailySegment


def _turning_dt(tk) -> datetime:
    turning_k = getattr(tk, "turning_k", None)
    if turning_k is not None:
        return turning_k.dt
    return tk.dt


def _turning_index(tk) -> int:
    return tk.turning_k_index if tk.turning_k_index is not None else tk.k_index


@dataclass
class WeeklySegmentCenter:
    """由日线层最终结果形成的周线级别中枢。"""

    segments: List[DailySegment]
    high: float
    low: float
    overlap_type: int
    is_active: bool = True
    status: str = "FINAL"
    points: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)

    @property
    def start_dt(self) -> datetime:
        return _turning_dt(self.segments[0].start_seg.start_k)

    @property
    def end_dt(self) -> datetime:
        return _turning_dt(self.segments[-1].end_seg.end_k)

    @property
    def start_index(self) -> int:
        return _turning_index(self.segments[0].start_seg.start_k)

    @property
    def end_index(self) -> int:
        return _turning_index(self.segments[-1].end_seg.end_k)


@dataclass
class WeeklySegment:
    """由多个日线级别线段合成的周线级别线段。"""

    symbol: str
    direction: Direction
    start_seg: DailySegment
    end_seg: DailySegment
    segments: List[DailySegment] = field(default_factory=list)
    centers: List[WeeklySegmentCenter] = field(default_factory=list)
    cache: dict = field(default_factory=dict)

    @property
    def sdt(self) -> datetime:
        return self.confirm_start_dt

    @property
    def edt(self) -> datetime:
        return self.confirm_end_dt

    @property
    def confirm_start_dt(self) -> datetime:
        return _turning_dt(self.start_seg.start_seg.start_k)

    @property
    def confirm_end_dt(self) -> datetime:
        return _turning_dt(self.end_seg.end_seg.end_k)

    @property
    def confirm_start_index(self) -> int:
        return _turning_index(self.start_seg.start_seg.start_k)

    @property
    def confirm_end_index(self) -> int:
        return _turning_index(self.end_seg.end_seg.end_k)

    @property
    def start_price(self) -> float:
        return self.start_seg.start_price

    @property
    def end_price(self) -> float:
        return self.end_seg.end_price

    @property
    def power(self) -> float:
        return abs(self.end_price - self.start_price)
