# -*- coding: utf-8 -*-
"""Pure center formation pattern checks."""

from czsc.py.enum import Direction

from .geometry import is_direction_progress, is_reverse_progress


def check_2c_pattern_with_idx(direction: Direction, center_line: float, bars: list) -> tuple:
    """反正两穿核心判断并返回索引。"""
    if len(bars) < 2:
        return False, -1, -1

    k1_found_idx = -1
    for i, b in enumerate(bars):
        if direction == Direction.Up:
            if min(b.open, b.close) < center_line:
                k1_found_idx = i
                break
        else:
            if max(b.open, b.close) > center_line:
                k1_found_idx = i
                break

    if k1_found_idx == -1 or k1_found_idx == len(bars) - 1:
        return False, -1, -1

    for i in range(k1_found_idx + 1, len(bars)):
        curr_k = bars[i]
        prev_k = bars[i - 1]
        curr_body_high = max(curr_k.open, curr_k.close)
        curr_body_low = min(curr_k.open, curr_k.close)
        prev_body_high = max(prev_k.open, prev_k.close)
        prev_body_low = min(prev_k.open, prev_k.close)

        if direction == Direction.Up:
            if curr_body_high > prev_body_high and curr_body_high > center_line:
                return True, k1_found_idx, i
        else:
            if curr_body_low < prev_body_low and curr_body_low < center_line:
                return True, k1_found_idx, i

    return False, -1, -1


def check_2c_pattern(direction: Direction, center_line: float, bars: list) -> bool:
    ok, _, _ = check_2c_pattern_with_idx(direction, center_line, bars)
    return ok


def check_5k_pattern(direction: Direction, bars: list, confirm_idx: int, _center_line: float) -> tuple:
    """寻找是否有 5 根 K 线在同一价格带重叠。"""
    if len(bars) < 4:
        return False, -1, 0, 0

    def _scan_with_anchor(is_high_anchor: bool) -> tuple:
        target_idx = 0
        if is_high_anchor:
            max_high = -float("inf")
            for i in range(confirm_idx + 1):
                if bars[i].high >= max_high:
                    max_high, target_idx = bars[i].high, i
        else:
            min_low = float("inf")
            for i in range(confirm_idx + 1):
                if bars[i].low <= min_low:
                    min_low, target_idx = bars[i].low, i

        anchor = bars[target_idx]
        break_bar, break_idx = None, -1
        for i in range(target_idx + 1, len(bars)):
            if is_high_anchor:
                if bars[i].high < anchor.high:
                    break_bar, break_idx = bars[i], i
                    break
            else:
                if bars[i].low > anchor.low:
                    break_bar, break_idx = bars[i], i
                    break
        if not break_bar:
            return False, -1, 0, 0, 0

        ov_high = min(anchor.high, break_bar.high)
        ov_low = max(anchor.low, break_bar.low)
        if ov_low > ov_high:
            return False, -1, 0, 0, 0

        ov_indices = [
            i for i in range(target_idx, len(bars))
            if bars[i].high >= ov_low and bars[i].low <= ov_high
        ]
        cnt = len(ov_indices)
        has_confirm_k = confirm_idx in ov_indices

        is_ok = False
        if cnt >= 5:
            is_ok = True
        elif cnt == 4:
            last_ov_idx = ov_indices[-1]
            if len(bars) > last_ov_idx + 1:
                next_b = bars[last_ov_idx + 1]
                prev_b = bars[last_ov_idx]
                is_ok = next_b.low > prev_b.high or next_b.high < prev_b.low

        is_ok = is_ok and has_confirm_k
        if is_ok:
            return True, ov_indices[0], ov_high, ov_low, cnt
        return False, -1, 0, 0, cnt

    res_high = _scan_with_anchor(True)
    res_low = _scan_with_anchor(False)

    if res_high[0] and res_low[0]:
        if res_high[4] >= res_low[4]:
            return True, res_high[1], res_high[2], res_high[3]
        return True, res_low[1], res_low[2], res_low[3]
    if res_high[0]:
        return True, res_high[1], res_high[2], res_high[3]
    if res_low[0]:
        return True, res_low[1], res_low[2], res_low[3]
    return False, -1, 0, 0


def check_3_strokes_pattern_with_price(direction: Direction, confirm_k_idx: int, bars: list) -> tuple:
    """三笔纯势核心判断并返回价格区间。"""
    if len(bars) < 5:
        return False, 0, 0

    ext_idx = 0
    if direction == Direction.Up:
        ext_val = bars[0].high
        for i in range(1, confirm_k_idx + 1):
            if bars[i].high > ext_val:
                ext_val, ext_idx = bars[i].high, i
    else:
        ext_val = bars[0].low
        for i in range(1, confirm_k_idx + 1):
            if bars[i].low < ext_val:
                ext_val, ext_idx = bars[i].low, i

    rev_count = 1
    last_k = bars[ext_idx]
    rev_end_idx = ext_idx
    rev_high = bars[ext_idx].high
    rev_low = bars[ext_idx].low

    for i in range(ext_idx + 1, len(bars)):
        curr_k = bars[i]
        if is_reverse_progress(direction, curr_k, last_k):
            rev_count += 1
            last_k, rev_end_idx = curr_k, i
            rev_high = max(rev_high, curr_k.high)
            rev_low = min(rev_low, curr_k.low)

    if rev_count < 3 or confirm_k_idx > rev_end_idx:
        return False, 0, 0

    fwd_count = 1
    last_k = bars[rev_end_idx]
    fwd_high = bars[rev_end_idx].high
    fwd_low = bars[rev_end_idx].low

    for i in range(rev_end_idx + 1, len(bars)):
        curr_k = bars[i]
        if is_direction_progress(direction, curr_k, last_k):
            fwd_count += 1
            last_k = curr_k
            fwd_high = max(fwd_high, curr_k.high)
            fwd_low = min(fwd_low, curr_k.low)

    if fwd_count < 3:
        return False, 0, 0

    final_ur = min(rev_high, fwd_high)
    final_lr = max(rev_low, fwd_low)
    if final_lr >= final_ur:
        final_ur = max(rev_high, fwd_high)
        final_lr = min(rev_low, fwd_low)

    return True, final_ur, final_lr


def check_3_strokes_pattern(direction: Direction, confirm_k_idx: int, bars: list) -> bool:
    ok, _, _ = check_3_strokes_pattern_with_price(direction, confirm_k_idx, bars)
    return ok
