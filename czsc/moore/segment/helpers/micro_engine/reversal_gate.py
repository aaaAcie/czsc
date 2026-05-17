# -*- coding: utf-8 -*-
"""异向准入门控 helper。"""

from czsc.py.enum import Mark


class ReversalGateHelper:
    def __init__(self, state):
        self.s = state

    def check_and_update_reversal_ma5_gate(self, new_mark: Mark, candidate_idx: int, candidate_price: float) -> bool:
        s = self.s
        if not s.turning_ks:
            return True
        candidate_bar = s.bars_raw[candidate_idx]
        candidate_ma5 = candidate_bar.cache.get("ma5")
        anchor_idx = s.turning_ks[-1].k_index
        if candidate_idx < anchor_idx:
            return True
        seg_bars = s.bars_raw[anchor_idx: candidate_idx + 1]
        if not seg_bars:
            return True
        seg_ma5 = [b.cache.get("ma5") for b in seg_bars if b.cache.get("ma5") is not None]
        if not seg_ma5:
            return True
        seg_max_ma5 = max(seg_ma5)
        seg_min_ma5 = min(seg_ma5)
        seg_max_price = max(b.high for b in seg_bars)
        seg_min_price = min(b.low for b in seg_bars)
        if new_mark == Mark.G:
            ma5_ok = (candidate_ma5 is not None) and (candidate_ma5 >= (seg_max_ma5 - 1e-8))
            price_ok = candidate_price >= (seg_max_price - 1e-8)
        else:
            ma5_ok = (candidate_ma5 is not None) and (candidate_ma5 <= (seg_min_ma5 + 1e-8))
            price_ok = candidate_price <= (seg_min_price + 1e-8)
        return ma5_ok or price_ok
