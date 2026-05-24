# -*- coding: utf-8 -*-
"""宏观审计引擎：负责疑点审计、跃迁判定与吞噬塌陷。"""

from ..objects import TurningK
from .helpers.macro import (
    build_leap_collapse_plan,
    check_leap_growth_only,
    check_leap_physics,
)


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

        if s.enable_pre_round:
            pre_start_idx = idx - 2
            pre_end_idx = idx + 1
            if pre_start_idx >= 0 and pre_end_idx <= n:
                tk_start = tks[pre_start_idx]
                mid_same = tks[idx - 1]
                tk_target = tks[idx]
                tk_end = tks[pre_end_idx]

                if pre_end_idx > pre_start_idx + 1 and tk_start.mark != tk_end.mark:
                    # print(
                    #     f"  [Audit] Testing Pre-Round: "
                    #     f"{tk_start.dt}({tk_start.mark.name}) -> "
                    #     f"{tk_end.dt}({tk_end.mark.name}) swallow "
                    #     f"{mid_same.dt}/{tk_target.dt}"
                    # )

                    if self._check_leap_growth_only(tk_start, tk_end, mid_same, tk_target):
                        # print(f"  [Audit] SUCCESS! Pre-Round Leaping from {tk_start.dt} to {tk_end.dt}")
                        tks[fake_idx].maybe_is_fake = False
                        self._execute_leap_collapse(pre_start_idx, pre_end_idx)
                        return True

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
                # print(
                #     f"  [Audit] Testing Leap Round {round_no}: "
                #     f"{tk_start.dt}({tk_start.mark.name}) -> "
                #     f"{tk_end.dt}({tk_end.mark.name}) swallow "
                #     f"{mid_same.dt}/{tk_target.dt}"
                # )

                if self._check_leap_physics(tk_start, tk_end, mid_same, tk_target):
                    # print(f"  [Audit] SUCCESS! Leaping from {tk_start.dt} to {tk_end.dt}")
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
        return check_leap_physics(
            self.state.bars_raw,
            tk_start,
            tk_end,
            tk_mid_same,
            tk_pullback,
        )

    def _check_leap_growth_only(
        self,
        tk_start: TurningK,
        tk_end: TurningK,
        tk_mid_same: TurningK,
        tk_pullback: TurningK,
    ) -> bool:
        """仅执行法则一（生长法则）的物理边际审判。

        注意：Pre-Round 只能在 ma5_is_better == False 时启用法则一兜底。
        若 ma5_is_better == True，必须走完整物理审判（含法则二），
        不能无条件以法则一放行。
        """
        return check_leap_growth_only(
            self.state.bars_raw,
            tk_start,
            tk_end,
            tk_mid_same,
            tk_pullback,
        )

    def _execute_leap_collapse(self, anchor_idx: int, new_end_idx: int):
        """执行塌陷：重连主干、落盘幽灵、触发中枢/趋势同步。"""
        s = self.state
        plan = build_leap_collapse_plan(s.macro_turning_ks, anchor_idx, new_end_idx)
        if plan is None:
            return

        s.macro_excluded_micro_ids.update(plan.excluded_micro_ids)
        if plan.swallow_key is not None:
            s.macro_swallow_map[plan.swallow_key] = plan.swallow_internal_ids
        s.macro_ghost_forks.append(plan.macro_ghost_fork)
        s.macro_turning_ks = plan.new_turning_ks
        plan.tk_new_end.maybe_is_fake = False
        s.cache["macro_replay_marks"] = plan.replay_marks

        if len(s.macro_turning_ks) >= 3:
            s.macro_turning_ks[-3].is_locked = True
            s.macro_turning_ks[-2].is_locked = True

        s.segment_start_extreme = plan.tk_anchor.price
        self._trend_engine.update_trend_state(plan.tk_new_end)
