# -*- coding: utf-8 -*-
"""极值寻址与 rule1-local 回退 helper。"""

from czsc.py.enum import Mark


class ExtremeLocatorHelper:
    def __init__(self, state):
        self.s = state

    def locate_extreme_with_mode(self, mark: Mark, start_idx: int, end_idx_inclusive: int) -> tuple:
        if self.s.use_left_3k_locator:
            return self.find_left_3k_extreme(mark, start_idx, end_idx_inclusive)
        return self.find_extreme_in_range(mark, start_idx, end_idx_inclusive + 1)

    def locate_reversal_extreme_by_trigger_rule(self, mark: Mark, start_idx: int, trigger_index: int) -> tuple:
        """异向寻址规则：
        1) 先判断 trigger K 是否是 [start, trigger] 全段最值；
        2) 若是，允许返回 trigger K（后续由特殊法则决定转折K后移）；
        3) 若不是，只能在 trigger 之前 [start, trigger-1] 寻值。
        """
        bars = self.s.bars_raw
        if start_idx >= trigger_index:
            return self.locate_extreme_with_mode(mark, start_idx, trigger_index)

        seg_bars = bars[start_idx : trigger_index + 1]
        trig_bar = bars[trigger_index]
        if mark == Mark.G:
            seg_ext = max(b.high for b in seg_bars)
            trigger_is_seg_extreme = trig_bar.high >= seg_ext
        else:
            seg_ext = min(b.low for b in seg_bars)
            trigger_is_seg_extreme = trig_bar.low <= seg_ext

        if trigger_is_seg_extreme:
            return self.locate_extreme_with_mode(mark, start_idx, trigger_index)

        # trigger 非全段最值时，严格排除 trigger 本身
        return self.locate_extreme_with_mode(mark, start_idx, trigger_index - 1)

    def find_extreme_in_range(self, mark: Mark, start_idx: int, end_idx: int) -> tuple:
        search_bars = self.s.bars_raw[start_idx:end_idx]
        if not search_bars:
            trigger = self.s.bars_raw[end_idx]
            return (trigger.high if mark == Mark.G else trigger.low, end_idx)
        if mark == Mark.G:
            ext_bar = max(search_bars, key=lambda b: b.high)
            return ext_bar.high, start_idx + search_bars.index(ext_bar)
        ext_bar = min(search_bars, key=lambda b: b.low)
        return ext_bar.low, start_idx + search_bars.index(ext_bar)

    def find_left_3k_extreme(self, mark: Mark, start_idx: int, trigger_index: int) -> tuple:
        bars = self.s.bars_raw
        for i in range(trigger_index, start_idx - 1, -1):
            prev_b = bars[i - 1] if i - 1 >= 0 else None
            curr_b = bars[i]
            next_b = bars[i + 1] if i + 1 < len(bars) else bars[-1]
            if mark == Mark.G:
                ok = (curr_b.high >= (prev_b.high if prev_b else -1)) and (curr_b.high >= next_b.high)
                if ok:
                    return curr_b.high, i
            else:
                ok = (curr_b.low <= (prev_b.low if prev_b else 999999)) and (curr_b.low <= next_b.low)
                if ok:
                    return curr_b.low, i
        return self.find_extreme_in_range(mark, start_idx, trigger_index)

    def passes_rule1_local(self, mark: Mark, idx: int) -> bool:
        bars = self.s.bars_raw
        if idx < 0 or idx >= len(bars):
            return False
        prev_b = bars[idx - 1] if idx - 1 >= 0 else None
        curr_b = bars[idx]
        next_b = bars[idx + 1] if idx + 1 < len(bars) else bars[-1]
        if mark == Mark.G:
            is_local_extreme = (curr_b.high >= (prev_b.high if prev_b else -1)) and (curr_b.high >= next_b.high)
        else:
            is_local_extreme = (curr_b.low <= (prev_b.low if prev_b else 999999)) and (curr_b.low <= next_b.low)
        if not is_local_extreme:
            return False
        curr_ma5 = curr_b.cache.get("ma5")
        if curr_ma5 is None:
            return False
        body_top = max(curr_b.open, curr_b.close)
        body_bottom = min(curr_b.open, curr_b.close)
        if mark == Mark.G:
            return body_top > curr_ma5
        return body_bottom < curr_ma5

    def find_prev_rule1_3k(self, mark: Mark, start_idx: int, from_idx: int):
        for i in range(from_idx, start_idx - 1, -1):
            if self.passes_rule1_local(mark, i):
                return i
        return None

    def find_extreme_by_trigger(self, mark: Mark, start_idx: int, trigger_index: int) -> tuple:
        abs_price, abs_idx = self.find_extreme_in_range(mark, start_idx, trigger_index)
        seg_bars = self.s.bars_raw[start_idx: trigger_index + 1]
        if not seg_bars:
            return abs_price, abs_idx
        seg_ext = max(b.high for b in seg_bars) if mark == Mark.G else min(b.low for b in seg_bars)
        if abs_price == seg_ext:
            return abs_price, abs_idx
        return self.find_left_3k_extreme(mark, start_idx, trigger_index)
