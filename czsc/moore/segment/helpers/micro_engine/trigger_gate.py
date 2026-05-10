# -*- coding: utf-8 -*-
"""触发门与实时包络追踪 helper。"""

from czsc.py.enum import Mark


class TriggerGateHelper:
    def __init__(self, state):
        self.s = state

    def snapshot_leg_gate_baseline(self):
        s = self.s
        s.gate_prev_leg_max_ma5 = s.leg_max_ma5
        s.gate_prev_leg_min_ma5 = s.leg_min_ma5
        s.gate_prev_leg_max_price = s.leg_max_price
        s.gate_prev_leg_min_price = s.leg_min_price

    def update_leg_realtime_extremes(self, bar, k_index: int, ma5: float):
        s = self.s
        last_tk = s.turning_ks[-1] if s.turning_ks else None
        anchor_idx = last_tk.k_index if last_tk else 0
        anchor_mark = last_tk.mark if last_tk else None
        context_changed = (
            anchor_idx != s.leg_extreme_anchor_idx
            or anchor_mark != s.leg_extreme_anchor_mark
            or s.leg_max_ma5 is None
        )
        if context_changed:
            s.leg_max_ma5 = s.leg_min_ma5 = ma5
            s.leg_max_price = bar.high
            s.leg_min_price = bar.low
            s.leg_extreme_anchor_idx = anchor_idx
            s.leg_extreme_anchor_mark = anchor_mark
        else:
            s.leg_max_ma5 = max(s.leg_max_ma5, ma5)
            s.leg_min_ma5 = min(s.leg_min_ma5, ma5)
            s.leg_max_price = max(s.leg_max_price, bar.high)
            s.leg_min_price = min(s.leg_min_price, bar.low)

    def is_turning_triggered(
        self,
        target_mark: Mark,
        ma5: float,
        last_ma5: float,
        prev2_ma5,
        is_solid_gap_up: bool,
        is_solid_gap_down: bool,
        bar,
    ) -> bool:
        if prev2_ma5 is None:
            ma5_turn_down = ma5 < last_ma5
            ma5_turn_up = ma5 > last_ma5
        else:
            ma5_turn_down = (ma5 < last_ma5) and (last_ma5 >= prev2_ma5)
            ma5_turn_up = (ma5 > last_ma5) and (last_ma5 <= prev2_ma5)

        if target_mark == Mark.G:
            return (ma5_turn_down or is_solid_gap_down) and (min(bar.open, bar.close) < ma5)
        return (ma5_turn_up or is_solid_gap_up) and (max(bar.open, bar.close) > ma5)
