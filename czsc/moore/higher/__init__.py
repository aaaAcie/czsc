# -*- coding: utf-8 -*-
"""
兼容层：旧的 higher 模块已迁移至 daily_segment 模块。
"""
from ..daily_segment import (
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
