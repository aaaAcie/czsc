# -*- coding: utf-8 -*-
"""Pure geometry predicates for center detection."""

from czsc.py.enum import Direction


def is_up_progress(curr_k, base_k) -> bool:
    """Strict upward K-line progression; equal prices do not count."""
    return curr_k.high > base_k.high and curr_k.low > base_k.low


def is_down_progress(curr_k, base_k) -> bool:
    """Strict downward K-line progression; equal prices do not count."""
    return curr_k.high < base_k.high and curr_k.low < base_k.low


def is_direction_progress(direction: Direction, curr_k, base_k) -> bool:
    if direction == Direction.Up:
        return is_up_progress(curr_k, base_k)
    return is_down_progress(curr_k, base_k)


def is_reverse_progress(direction: Direction, curr_k, base_k) -> bool:
    if direction == Direction.Up:
        return is_down_progress(curr_k, base_k)
    return is_up_progress(curr_k, base_k)


def is_price_overlap_with_center(bar, center) -> bool:
    """Whether a bar price range overlaps a center price range."""
    return not (bar.low > center.upper_rail or bar.high < center.lower_rail)


def is_center_price_overlap(c1, c2) -> bool:
    """Whether two center price ranges overlap."""
    return not (c1.lower_rail > c2.upper_rail or c1.upper_rail < c2.lower_rail)
