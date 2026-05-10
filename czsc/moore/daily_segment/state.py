# -*- coding: utf-8 -*-
"""日线级别线段模块的状态容器。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from ..objects import MooreSegment
from czsc.py.objects import RawBar
from .objects import DailySegment, DailySegmentCenter


@dataclass
class DailySegmentState:
    base_segments: List[MooreSegment] = field(default_factory=list)
    bars_raw: List[RawBar] = field(default_factory=list)

    current_segments: List[MooreSegment] = field(default_factory=list)
    pending_daily_segments: List[MooreSegment] = field(default_factory=list)
    completed_segments: List[DailySegment] = field(default_factory=list)
    active_center: Optional[DailySegmentCenter] = None
    archived_centers: List[DailySegmentCenter] = field(default_factory=list)
    candidates: List[DailySegmentCenter] = field(default_factory=list)

    ma34: List[Optional[float]] = field(default_factory=list)
    ma170: List[Optional[float]] = field(default_factory=list)

    anchor_k_index: Optional[int] = None
    anchor_dt: Optional[datetime] = None
    anchor_completed_segments: List[DailySegment] = field(default_factory=list)

    last_sig: Optional[tuple] = None
    pending_anchor_snapshot: bool = False
    continuity_broken: bool = False
