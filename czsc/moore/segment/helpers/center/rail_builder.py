# -*- coding: utf-8 -*-
"""Initial rail construction for CenterEngine."""

from dataclasses import dataclass

from czsc.py.enum import Direction


@dataclass(frozen=True)
class InitialRails:
    center_line: float
    upper_rail: float
    lower_rail: float
    inception_dt: object
    inception_idx: int
    is_double_gap: bool


def build_initial_rails(
    *,
    direction: Direction,
    k0,
    confirm_k,
    confirm_idx: int,
    bars: list,
    center_anchor_idx: int,
    last_center_end_idx: int,
    sandbox_active: bool,
) -> InitialRails:
    """Compute center line, initial rails, and inception point when confirm K appears."""
    ma5_confirm = confirm_k.cache.get("ma5", 0)

    if direction == Direction.Up:
        price_gap = confirm_k.high < k0.low
        body_gap_ma5 = max(confirm_k.open, confirm_k.close) < ma5_confirm
    else:
        price_gap = confirm_k.low > k0.high
        body_gap_ma5 = min(confirm_k.open, confirm_k.close) > ma5_confirm

    is_double_gap = price_gap and body_gap_ma5

    if direction == Direction.Up:
        center_line = max(confirm_k.open, confirm_k.close) if is_double_gap else min(confirm_k.open, confirm_k.close)
    else:
        center_line = min(confirm_k.open, confirm_k.close) if is_double_gap else max(confirm_k.open, confirm_k.close)

    search_start = max(0, center_anchor_idx)
    if last_center_end_idx != -1 and not sandbox_active:
        search_start = max(search_start, last_center_end_idx)

    upper_rail = center_line
    lower_rail = center_line
    inception_dt = confirm_k.dt
    inception_idx = confirm_idx

    for i in range(search_start, confirm_idx):
        k1 = bars[i]
        k2 = bars[i + 1]
        overlap_high = min(k1.high, k2.high)
        overlap_low = max(k1.low, k2.low)

        if overlap_low > overlap_high:
            continue
        if direction == Direction.Up and overlap_high >= center_line:
            upper_rail = overlap_high
            lower_rail = center_line
            inception_dt = k1.dt
            inception_idx = i
            break
        if direction == Direction.Down and overlap_low <= center_line:
            lower_rail = overlap_low
            upper_rail = center_line
            inception_dt = k1.dt
            inception_idx = i
            break

    return InitialRails(
        center_line=center_line,
        upper_rail=upper_rail,
        lower_rail=lower_rail,
        inception_dt=inception_dt,
        inception_idx=inception_idx,
        is_double_gap=is_double_gap,
    )
