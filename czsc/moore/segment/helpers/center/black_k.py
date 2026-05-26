# -*- coding: utf-8 -*-
"""Pure black-K quality check."""

from czsc.py.enum import Direction


def check_black_k(
    *,
    direction: Direction,
    confirm_k_idx: int,
    window_bars: list,
    turning_ks: list,
    replay_anchor: int | None = None,
) -> bool:
    """Find at least one non-gap MA5-touching K after the confirmation K."""
    if len(window_bars) < 2:
        return False

    start = max(1, confirm_k_idx + 1)
    for i in range(start, len(window_bars)):
        bar = window_bars[i]
        ma5 = bar.cache.get("ma5", 0)

        is_extreme = False
        for tk in turning_ks:
            if tk.dt == bar.dt:
                if replay_anchor is not None and tk.k_index >= replay_anchor:
                    continue
                is_extreme = True
                break
        if is_extreme:
            continue

        if direction == Direction.Up:
            if bar.low <= ma5:
                return True
        else:
            if bar.high >= ma5:
                return True
    return False
