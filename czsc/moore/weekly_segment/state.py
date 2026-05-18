# -*- coding: utf-8 -*-
"""周线级别线段模块的状态容器。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..daily_segment.objects import DailySegment, DailySegmentCenter
from ..objects import MooreSegment
from .objects import WeeklySegment, WeeklySegmentCenter


@dataclass
class WeeklySegmentState:
    daily_segments: List[DailySegment] = field(default_factory=list)
    daily_centers: List[DailySegmentCenter] = field(default_factory=list)
    daily_refined_segments: List[MooreSegment] = field(default_factory=list)
    ma170: List[Optional[float]] = field(default_factory=list)

    completed_segments: List[WeeklySegment] = field(default_factory=list)
    pending_segments: List[WeeklySegment] = field(default_factory=list)
    non_same_segments: List[WeeklySegment] = field(default_factory=list)
    weekly_centers: List[WeeklySegmentCenter] = field(default_factory=list)
    pending_centers: List[WeeklySegmentCenter] = field(default_factory=list)
    weekly_center_source_segments: List[DailySegment] = field(default_factory=list)

    last_sig: Optional[tuple] = None
