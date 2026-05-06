# -*- coding: utf-8 -*-
"""兼容层：旧的 higher.analyzer 已迁移至 daily_segment.analyzer。"""

from ..daily_segment.analyzer import (
    DailySegmentAnalyzer,
    DailySegmentCenter,
    DailySegment,
    DailySegmentState,
)

HigherAnalyzer = DailySegmentAnalyzer
HigherCenter = DailySegmentCenter
HigherSegment = DailySegment
HigherState = DailySegmentState

__all__ = [
    "HigherAnalyzer",
    "HigherCenter",
    "HigherSegment",
    "HigherState",
]
