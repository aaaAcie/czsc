# -*- coding: utf-8 -*-
"""日线级别线段顺势关系。"""
from __future__ import annotations

from typing import Sequence

from czsc.py.enum import Direction

from ..utils import seg_start_price


def check_strict_start_extreme(segments: Sequence) -> bool:
    if not segments:
        return False
    daily_dir = segments[0].direction
    start_p = seg_start_price(segments[0])
    endpoints = []
    for seg in segments:
        endpoints.extend([seg_start_price(seg), seg.end_k.price])
    global_max = max(endpoints)
    global_min = min(endpoints)
    if daily_dir == Direction.Up:
        return start_p == global_min and endpoints.count(global_min) == 1
    if daily_dir == Direction.Down:
        return start_p == global_max and endpoints.count(global_max) == 1
    return False


def check_global_trend_relationship(segments: Sequence) -> bool:
    """兼容旧命名：日线候选只校验起点严格极值，终点由提交链确认。"""
    return check_strict_start_extreme(segments)
