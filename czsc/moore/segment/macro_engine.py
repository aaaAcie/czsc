# -*- coding: utf-8 -*-
"""宏观审计引擎：负责疑点审计、跃迁判定与吞噬塌陷。"""
from czsc.py.enum import Mark

from ..objects import TurningK
from .scope_utils import build_scope_windows, evaluate_scope_refresh, get_trigger_index


class MacroAuditEngine:
    """宏观吞噬/审计的独立引擎。"""

    def __init__(self, state, fractal_engine, trend_engine):
        self.state = state
        self._fractal_engine = fractal_engine
        self._trend_engine = trend_engine

    def audit_and_replay(self, current_k_idx: int) -> bool:
        """仅审计最老疑点；若命中则执行一次吞噬并返回。"""
        s = self.state
        tks = s.macro_turning_ks
        n = len(tks) - 1
        if n < 4:
            return False

        target_indices = [i for i, tk in enumerate(tks) if tk.maybe_is_fake]
        if not target_indices:
            return False

        # 严格“老疑点优先 + 每轮只处理一个疑点”：
        # maybe_is_fake 打在虚线段终点，审计坐标就用该终点索引本身。
        fake_idx = None
        idx = None
        for candidate_idx in target_indices:
            if candidate_idx < 2:
                continue
            if n - candidate_idx < s.audit_link_rounds:
                break
            fake_idx = candidate_idx
            idx = candidate_idx
            break

        if idx is None or fake_idx is None:
            return False

        for round_no in range(1, s.audit_link_rounds + 1):
            start_idx = idx - round_no
            if start_idx < 0:
                break

            tk_start = tks[start_idx]

            # 同一锚点下仍坚持从近到远找最小可行吞噬
            # 右侧法官列表从疑点本身开始（idx..n），优先尝试就地最小吞噬。
            for end_idx in range(idx, n + 1):
                # 必须存在至少 1 个被吞噬中间点；相邻端点不算有效吞噬。
                if end_idx <= start_idx + 1:
                    continue
                tk_end = tks[end_idx]
                if tk_start.mark == tk_end.mark:
                    continue

                mid_same = tks[idx - 1]
                tk_target = tks[idx]
                print(
                    f"  [Audit] Testing Leap Round {round_no}: "
                    f"{tk_start.dt}({tk_start.mark.name}) -> "
                    f"{tk_end.dt}({tk_end.mark.name}) swallow "
                    f"{mid_same.dt}/{tk_target.dt}"
                )

                if self._check_leap_physics(tk_start, tk_end, mid_same, tk_target):
                    print(f"  [Audit] SUCCESS! Leaping from {tk_start.dt} to {tk_end.dt}")
                    # 被审计的 fake 点已被处理，先撤销标签再塌陷。
                    tks[fake_idx].maybe_is_fake = False
                    self._execute_leap_collapse(start_idx, end_idx)
                    return True

        return False

    def _check_leap_physics(
        self,
        tk_start: TurningK,
        tk_end: TurningK,
        tk_mid_same: TurningK,
        tk_pullback: TurningK,
    ) -> bool:
        """执行跃迁判定：法则一 (实力生长) OR 法则二 (重心演化)。"""
        s = self.state

        seg_start = tk_start.k_index
        old_trigger_idx = get_trigger_index(tk_pullback)
        new_trigger_idx = get_trigger_index(tk_end)
        scopes = build_scope_windows(s.bars_raw, seg_start, old_trigger_idx, new_trigger_idx)
        if scopes is None:
            return False
        refresh = evaluate_scope_refresh(tk_end.mark, scopes.old_scope, scopes.new_scope)

        path_bars = s.bars_raw[tk_mid_same.k_index : new_trigger_idx + 1]
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

        # ma5_is_better 统一按 old_scope/new_scope 的趋势极值比较，避免单点 MA5 失真。
        ma5_is_better = refresh.ma5_refreshed
        start_ma5_ref = refresh.start_ma5_ref
        if start_ma5_ref is None:
            start_ma5_ref = tk_start.raw_bar.cache.get("ma5")
        if start_ma5_ref is None:
            return False

        ma5_gravity_ok = (
            (min(path_ma5) >= start_ma5_ref)
            if tk_end.mark == Mark.G
            else (max(path_ma5) <= start_ma5_ref)
        )

        if ma5_is_better:
            return growth_ok and ma5_gravity_ok
        return growth_ok

    def _execute_leap_collapse(self, anchor_idx: int, new_end_idx: int):
        """执行塌陷：重连主干、落盘幽灵、触发中枢/趋势同步。"""
        s = self.state
        tks = s.macro_turning_ks

        tk_anchor = tks[anchor_idx]
        tk_new_end = tks[new_end_idx]

        ghost_nodes = tks[anchor_idx + 1 : new_end_idx]
        if not ghost_nodes:
            return

        for gtk in ghost_nodes:
            src_id = gtk.cache.get("source_micro_id")
            if src_id is not None:
                s.macro_excluded_micro_ids.add(src_id)

        start_src = tk_anchor.cache.get("source_micro_id")
        end_src = tk_new_end.cache.get("source_micro_id")
        if start_src is not None and end_src is not None:
            internal_ids = [
                gtk.cache.get("source_micro_id")
                for gtk in ghost_nodes
                if gtk.cache.get("source_micro_id") is not None
            ]
            s.macro_swallow_map[(start_src, end_src)] = internal_ids

        s.macro_ghost_forks.append((tk_anchor, sorted(ghost_nodes, key=lambda t: t.k_index)))

        new_turning_ks = tks[: anchor_idx + 1]
        new_turning_ks.append(tk_new_end)
        for i in range(new_end_idx + 1, len(tks)):
            new_turning_ks.append(tks[i])
        s.macro_turning_ks = new_turning_ks

        tk_new_end.maybe_is_fake = False

        if len(s.macro_turning_ks) >= 3:
            s.macro_turning_ks[-3].is_locked = True
            s.macro_turning_ks[-2].is_locked = True

        s.segment_start_extreme = tk_anchor.price
        self._trend_engine.update_trend_state(tk_new_end)
