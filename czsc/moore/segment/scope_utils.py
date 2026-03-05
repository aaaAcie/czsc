# -*- coding: utf-8 -*-
"""old/new 区间构造与趋势刷新判定的公共工具。"""
from dataclasses import dataclass
from typing import List, Optional

from czsc.py.enum import Mark
from czsc.py.objects import RawBar


@dataclass
class ScopeWindows:
    """以旧触发K与新触发K为边界的两段区间。"""

    seg_start: int
    old_trigger_idx: int
    new_trigger_idx: int
    old_scope: List[RawBar]
    new_scope: List[RawBar]


@dataclass
class ScopeRefreshMetrics:
    """区间刷新判定结果。"""

    old_price_ext: float
    new_price_ext: float
    price_refreshed: bool
    old_ma5: List[float]
    new_ma5: List[float]
    ma5_ready: bool
    ma5_refreshed: bool
    start_ma5_ref: Optional[float]


def get_trigger_index(tk) -> int:
    """统一获取转折K对应的触发索引。"""
    turning_idx = getattr(tk, "turning_k_index", None)
    if turning_idx is not None:
        return turning_idx
    trigger_idx = getattr(tk, "trigger_k_index", None)
    return trigger_idx if trigger_idx is not None else tk.k_index


def build_scope_windows(
    bars: List[RawBar],
    seg_start: int,
    old_trigger_idx: int,
    new_trigger_idx: int,
) -> Optional[ScopeWindows]:
    """构造 old/new 两段区间；边界不合法时返回 None。"""
    if new_trigger_idx <= old_trigger_idx:
        return None

    old_scope = bars[seg_start : old_trigger_idx + 1]
    new_scope = bars[seg_start : new_trigger_idx + 1]
    if not old_scope or not new_scope:
        return None

    return ScopeWindows(
        seg_start=seg_start,
        old_trigger_idx=old_trigger_idx,
        new_trigger_idx=new_trigger_idx,
        old_scope=old_scope,
        new_scope=new_scope,
    )


def evaluate_scope_refresh(
    mark: Mark,
    old_scope: List[RawBar],
    new_scope: List[RawBar],
) -> ScopeRefreshMetrics:
    """评估 old/new 区间在价格与 MA5 上是否发生正向刷新。"""
    old_ma5 = [b.cache.get("ma5") for b in old_scope if b.cache.get("ma5") is not None]
    new_ma5 = [b.cache.get("ma5") for b in new_scope if b.cache.get("ma5") is not None]
    ma5_ready = bool(old_ma5 and new_ma5)

    if mark == Mark.G:
        old_price_ext = max(b.high for b in old_scope)
        new_price_ext = max(b.high for b in new_scope)
        price_refreshed = new_price_ext > old_price_ext
        ma5_refreshed = ma5_ready and (max(new_ma5) > max(old_ma5))
        start_ma5_ref = min(old_ma5) if old_ma5 else None
    else:
        old_price_ext = min(b.low for b in old_scope)
        new_price_ext = min(b.low for b in new_scope)
        price_refreshed = new_price_ext < old_price_ext
        ma5_refreshed = ma5_ready and (min(new_ma5) < min(old_ma5))
        start_ma5_ref = max(old_ma5) if old_ma5 else None

    return ScopeRefreshMetrics(
        old_price_ext=old_price_ext,
        new_price_ext=new_price_ext,
        price_refreshed=price_refreshed,
        old_ma5=old_ma5,
        new_ma5=new_ma5,
        ma5_ready=ma5_ready,
        ma5_refreshed=ma5_refreshed,
        start_ma5_ref=start_ma5_ref,
    )
