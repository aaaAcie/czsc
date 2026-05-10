# -*- coding: utf-8 -*-
"""非同处理：反向虚线起手的特殊候选成立路径。"""
from __future__ import annotations

from typing import Sequence

from .extension import is_opposite_direction
from .ma_cross import ma_reverses_against_window


def try_non_same_candidate(
    segments: Sequence,
    previous_direction,
    ma34,
) -> list:
    if len(segments) < 3:
        return []

    first = segments[0]
    if not is_opposite_direction(first.direction, previous_direction):
        return []
    if first.is_perfect:
        return []

    window = list(segments[:3])
    if not ma_reverses_against_window(window, ma34):
        return []
    return window

