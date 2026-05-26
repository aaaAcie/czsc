# -*- coding: utf-8 -*-
"""Pure macro leap physics checks."""

from czsc.py.enum import Mark

from ...scope_utils import evaluate_scope_refresh, get_trigger_index


def check_leap_physics(bars_raw: list, tk_start, tk_end, tk_mid_same, tk_pullback) -> bool:
    """执行跃迁判定：法则一 (实力生长) OR 法则二 (重心演化)。"""
    start_idx = tk_start.k_index
    end_idx = tk_end.k_index
    old_trigger_idx = get_trigger_index(tk_start)
    new_trigger_idx = get_trigger_index(tk_end)
    if not (start_idx <= old_trigger_idx < end_idx <= new_trigger_idx):
        return False

    growth_old_scope = bars_raw[start_idx:end_idx]
    growth_new_scope = bars_raw[end_idx : new_trigger_idx + 1]
    if not growth_old_scope or not growth_new_scope:
        return False
    refresh = evaluate_scope_refresh(tk_end.mark, growth_old_scope, growth_new_scope)

    path_bars = bars_raw[tk_mid_same.k_index : new_trigger_idx + 1]
    if not path_bars:
        return False
    path_ma5 = [b.cache.get("ma5") for b in path_bars if b.cache.get("ma5") is not None]
    if not path_ma5:
        return False

    tk_end_top = max(tk_end.raw_bar.open, tk_end.raw_bar.close)
    tk_end_bottom = min(tk_end.raw_bar.open, tk_end.raw_bar.close)
    tk_mid_top = max(tk_mid_same.raw_bar.open, tk_mid_same.raw_bar.close)
    tk_mid_bottom = min(tk_mid_same.raw_bar.open, tk_mid_same.raw_bar.close)

    if tk_end.mark == Mark.G:
        growth_price_ok = refresh.price_refreshed
        growth_body_ok = tk_end_top > tk_mid_bottom
    else:
        growth_price_ok = refresh.price_refreshed
        growth_body_ok = tk_end_bottom < tk_mid_top
    growth_ok = growth_price_ok and growth_body_ok

    ma5_is_better = refresh.ma5_refreshed
    gravity_old_scope = bars_raw[start_idx : old_trigger_idx + 1]
    gravity_old_ma5 = [b.cache.get("ma5") for b in gravity_old_scope if b.cache.get("ma5") is not None]
    start_ma5_ref = (min(gravity_old_ma5) if tk_end.mark == Mark.G else max(gravity_old_ma5)) if gravity_old_ma5 else None
    if start_ma5_ref is None:
        start_ma5_ref = tk_start.raw_bar.cache.get("ma5")
    if start_ma5_ref is None:
        return False

    ma5_gravity_ok = (
        min(path_ma5) >= start_ma5_ref
        if tk_end.mark == Mark.G
        else max(path_ma5) <= start_ma5_ref
    )

    if ma5_is_better:
        return ma5_gravity_ok
    return growth_ok


def check_leap_growth_only(bars_raw: list, tk_start, tk_end, tk_mid_same, tk_pullback) -> bool:
    """仅执行法则一（生长法则）的物理边际审判。"""
    start_idx = tk_start.k_index
    end_idx = tk_end.k_index
    new_trigger_idx = get_trigger_index(tk_end)
    if not (start_idx < end_idx <= new_trigger_idx):
        return False

    growth_old_scope = bars_raw[start_idx:end_idx]
    growth_new_scope = bars_raw[end_idx : new_trigger_idx + 1]
    if not growth_old_scope or not growth_new_scope:
        return False
    refresh = evaluate_scope_refresh(tk_end.mark, growth_old_scope, growth_new_scope)
    if refresh.ma5_refreshed:
        return False

    tk_end_top = max(tk_end.raw_bar.open, tk_end.raw_bar.close)
    tk_end_bottom = min(tk_end.raw_bar.open, tk_end.raw_bar.close)
    tk_mid_top = max(tk_mid_same.raw_bar.open, tk_mid_same.raw_bar.close)
    tk_mid_bottom = min(tk_mid_same.raw_bar.open, tk_mid_same.raw_bar.close)

    if tk_end.mark == Mark.G:
        growth_body_ok = tk_end_top > tk_mid_bottom
    else:
        growth_body_ok = tk_end_bottom < tk_mid_top
    return refresh.price_refreshed and growth_body_ok
