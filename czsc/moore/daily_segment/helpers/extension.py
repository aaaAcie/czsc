# -*- coding: utf-8 -*-
"""日线级别线段延展与最佳终点规则。"""
from __future__ import annotations

from typing import Sequence

from czsc.py.enum import Direction

from ..utils import seg_end_price


def is_extension_same_trend(window: Sequence, next_two: Sequence) -> bool:
    if not window or len(next_two) < 2:
        return False
    direction = window[0].direction
    current_end = seg_end_price(window[-1])
    extended_end = seg_end_price(next_two[-1])
    if direction == Direction.Up:
        return extended_end >= current_end
    if direction == Direction.Down:
        return extended_end <= current_end
    return False


def is_opposite_direction(a: Direction, b: Direction) -> bool:
    return (a == Direction.Up and b == Direction.Down) or (a == Direction.Down and b == Direction.Up)

