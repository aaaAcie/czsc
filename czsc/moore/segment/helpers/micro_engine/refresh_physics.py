# -*- coding: utf-8 -*-
"""同向刷新物理比较与刷新候选构造 helper。"""

from czsc.py.enum import Mark

from ...scope_utils import build_scope_windows, evaluate_scope_refresh, get_trigger_index


class RefreshPhysicsHelper:
    def __init__(self, state, extreme_locator):
        self.s = state
        self.extreme_locator = extreme_locator

    def is_physically_better(self, mark: Mark, trigger_bar, trigger_index: int, old_tk) -> bool:
        s = self.s
        old_bar = old_tk.raw_bar
        seg_start = old_tk.k_index
        if len(s.turning_ks) >= 2 and s.turning_ks[-1].mark == old_tk.mark:
            seg_start = s.turning_ks[-2].k_index + 1
        old_trigger_idx = get_trigger_index(old_tk)
        scopes = build_scope_windows(s.bars_raw, seg_start, old_trigger_idx, trigger_index)
        if scopes is None:
            return False
        refresh = evaluate_scope_refresh(mark, scopes.old_scope, scopes.new_scope)
        if mark == Mark.G:
            body_top = max(trigger_bar.open, trigger_bar.close)
            old_bottom = min(old_bar.open, old_bar.close)
            rule1 = refresh.price_refreshed and (body_top > old_bottom)
        else:
            body_bottom = min(trigger_bar.open, trigger_bar.close)
            old_top = max(old_bar.open, old_bar.close)
            rule1 = refresh.price_refreshed and (body_bottom < old_top)
        rule2a = refresh.ma5_refreshed
        if not refresh.ma5_ready:
            trigger_ma5 = trigger_bar.cache.get("ma5")
            old_trig_ma5 = old_tk.turning_k.cache.get("ma5") if old_tk.turning_k else None
            if old_trig_ma5 is None:
                old_trig_ma5 = old_bar.cache.get("ma5")
            if trigger_ma5 is not None and old_trig_ma5 is not None:
                if mark == Mark.G:
                    rule2a = trigger_ma5 > old_trig_ma5
                else:
                    rule2a = trigger_ma5 < old_trig_ma5
        return rule1 or rule2a

    def prepare_refresh_attempt(self, mark: Mark, trigger_index: int):
        s = self.s
        if not s.turning_ks:
            return None
        old_tk = s.turning_ks[-1]
        old_trigger_idx = get_trigger_index(old_tk)
        seg_start = s.turning_ks[-2].k_index + 1 if len(s.turning_ks) >= 2 else 0
        bars = s.bars_raw
        scopes = build_scope_windows(bars, seg_start, old_trigger_idx, trigger_index)
        if scopes is None:
            return None
        refresh = evaluate_scope_refresh(mark, scopes.old_scope, scopes.new_scope)
        right_scope = bars[old_trigger_idx + 1: trigger_index + 1]
        if not right_scope:
            return None
        cond_a = refresh.ma5_refreshed
        if cond_a:
            left_end = max(old_trigger_idx + 1, trigger_index - 1)
            _, ext_idx = self.extreme_locator.locate_extreme_with_mode(mark, old_trigger_idx - 1, left_end)
            return {"ext_idx": ext_idx, "allow_special_shift": False, "invalidate_last_on_fail": False}
        if mark == Mark.G:
            ext_bar = max(right_scope, key=lambda x: x.high)
            ext_price = ext_bar.high
            old_body_edge = min(old_tk.raw_bar.open, old_tk.raw_bar.close)
            body_ok = max(ext_bar.open, ext_bar.close) > old_body_edge
            price_refreshed = ext_price > refresh.old_price_ext
        else:
            ext_bar = min(right_scope, key=lambda x: x.low)
            ext_price = ext_bar.low
            old_body_edge = max(old_tk.raw_bar.open, old_tk.raw_bar.close)
            body_ok = min(ext_bar.open, ext_bar.close) < old_body_edge
            price_refreshed = ext_price < refresh.old_price_ext
        ext_idx = (old_trigger_idx + 1) + right_scope.index(ext_bar)
        cond_b = price_refreshed and body_ok
        if not cond_b:
            return None
        return {
            "ext_idx": ext_idx,
            "allow_special_shift": cond_b and ext_idx == trigger_index,
            "invalidate_last_on_fail": False,
        }
