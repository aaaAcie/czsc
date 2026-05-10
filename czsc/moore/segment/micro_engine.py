# -*- coding: utf-8 -*-
"""
微观结构引擎（MicroStructureEngine）

职责：
  - 转折K触发检测（MA5停滞 + 实体突破 / 跳空触发）
  - 极值寻址（左向扫 3K 局部极值 / 兜底绝对极值）
  - 同向刷新（微观生长）：满足条件 A（MA5新生）或 条件 B（价格破位+实体穿透）
  - 特殊法则（转折K后移一根）、候选管理、四法则验真
"""
from datetime import datetime
from czsc.py.enum import Mark, Direction
from czsc.py.objects import RawBar
from ..objects import TurningK, MooreSegment
from .helpers import (
    DelayedJudgementHelper,
    CandidateCommitHelper,
    TriggerGateHelper,
    ExtremeLocatorHelper,
    ReversalGateHelper,
    RefreshPhysicsHelper,
    RuleValidatorHelper,
    SegmentBuilderHelper,
)
from .scope_utils import build_scope_windows, evaluate_scope_refresh, get_trigger_index


class MicroStructureEngine:
    """微观结构引擎"""

    def __init__(self, state, trend_engine, center_engine):
        self.s = state
        self.trend = trend_engine
        self.center = center_engine
        self._delayed_judgement = DelayedJudgementHelper(
            state,
            has_center_between=lambda a, b: self._segment_builder.has_center_between(a, b),
            reset_locks=lambda: self._segment_builder.reset_locks(),
            update_segments=lambda: self._segment_builder.update_segments(),
        )
        self._candidate_commit = CandidateCommitHelper(
            state,
            compute_ma5_extreme=self._compute_ma5_extreme,
        )
        self._trigger_gate = TriggerGateHelper(state)
        self._extreme_locator = ExtremeLocatorHelper(state)
        self._reversal_gate = ReversalGateHelper(state)
        self._refresh_physics = RefreshPhysicsHelper(state, self._extreme_locator)
        self._rule_validator = RuleValidatorHelper(state)
        self._segment_builder = SegmentBuilderHelper(state)

    # =========================================================================
    # 公开接口
    # =========================================================================

    def update(self, bar: RawBar, k_index: int, ma5: float):
        """顶底探测主循环"""
        s = self.s
        if s.last_ma5 is None: return

        # --- 0. 门控快照 ---
        # 先记录“上一时刻包络”，供本根候选做准入校验（先检查，再刷新）。
        self._snapshot_leg_gate_baseline()

        # 判断是否发生物理跳空
        prev_bar = s.bars_raw[k_index - 1]
        is_gap_up   = bar.low  > prev_bar.high
        is_gap_down = bar.high < prev_bar.low
        is_solid_gap_up   = min(bar.open, bar.close) > ma5 and is_gap_up
        is_solid_gap_down = max(bar.open, bar.close) < ma5 and is_gap_down

        # --- 1. 候选推进：旧候选在当前 K 线可能由于 MA5 交叉满足了四法则 ---
        if s.candidate_tk:
            valid_base, perfect_struct, has_v = self._validate_four_rules(s.candidate_tk)
            if valid_base:
                self._confirm_candidate(s.candidate_tk, perfect_struct, has_v)
                return
            s.candidate_tk = None

        # --- 2. 特殊法则残留：处理上一根 K 线挂起的“转折K后移” ---
        if s.waiting_special_rule:
            waiting_mark = s.special_waiting_mark
            cached_ext_idx = s.special_ext_idx_cache
            s.waiting_special_rule = False
            s.special_waiting_mark = None
            s.special_ext_idx_cache = None
            if waiting_mark is not None:
                self._process_confirmed_trigger(
                    bar, k_index, waiting_mark, preset_ext_idx=cached_ext_idx, from_special_rule=True
                )

        # --- 3. 转折K信号探测 ---
        # 这里只判定“转折K”候选；K0 等非转折用途由中枢引擎单独处理，不在此分支内。
        # 探测方向：同向刷新（微观生长）与反向转折在同一根 K 上并检
        reversal_mark = Mark.D
        refresh_mark  = None
        if s.turning_ks:
            last_mark = s.turning_ks[-1].mark
            reversal_mark = Mark.G if last_mark == Mark.D else Mark.D
            refresh_mark  = last_mark

        prev2_ma5 = None
        if k_index >= 2:
            prev2_ma5 = s.bars_raw[k_index - 2].cache.get("ma5")

        def _is_turning_triggered(target_mark: Mark) -> bool:
            return self._trigger_gate.is_turning_triggered(
                target_mark=target_mark,
                ma5=ma5,
                last_ma5=s.last_ma5,
                prev2_ma5=prev2_ma5,
                is_solid_gap_up=is_solid_gap_up,
                is_solid_gap_down=is_solid_gap_down,
                bar=bar,
            )

        def _run_attempt(
            target_mark: Mark,
            preset_ext_idx: int = None,
            allow_special_shift: bool = True,
            invalidate_last_on_fail: bool = False,
        ) -> bool:
            old_len = len(s.turning_ks)
            old_last = s.turning_ks[-1].dt if s.turning_ks else None
            old_last_trigger = (
                s.turning_ks[-1].turning_k_index if s.turning_ks else None
            )
            s.debug_trigger_count += 1

            self._process_confirmed_trigger(
                bar,
                k_index,
                target_mark,
                preset_ext_idx=preset_ext_idx,
                allow_special_shift=allow_special_shift,
                invalidate_last_on_fail=invalidate_last_on_fail,
            )

            # 若本根 K 已形成待后移特殊法则，或已经确认了一个新顶底，则视为本轮结束。
            if s.waiting_special_rule:
                return True
            if len(s.turning_ks) != old_len or (s.turning_ks and s.turning_ks[-1].dt != old_last):
                return True
            if s.turning_ks and s.turning_ks[-1].turning_k_index != old_last_trigger:
                return True
            return False

        # 同一根 K 线先并检触发，再按“同向刷新 -> 反向转折”顺序裁决，
        # 最终只会落地为：同向、反向、或都不成立。
        refresh_attempt = None
        if refresh_mark and _is_turning_triggered(refresh_mark):
            refresh_attempt = self._prepare_refresh_attempt(refresh_mark, k_index)

        reversal_ready = _is_turning_triggered(reversal_mark)

        if refresh_attempt and _run_attempt(
            refresh_mark,
            preset_ext_idx=refresh_attempt["ext_idx"],
            allow_special_shift=refresh_attempt["allow_special_shift"],
            invalidate_last_on_fail=refresh_attempt["invalidate_last_on_fail"],
        ):
            # 本根处理结束前，再把当前 K 吞入实时包络
            self._update_leg_realtime_extremes(bar, k_index, ma5)
            return
        if reversal_ready:
            _run_attempt(reversal_mark, None)
        # 本根处理结束后统一刷新包络
        self._update_leg_realtime_extremes(bar, k_index, ma5)

    # =========================================================================
    # 物理法则：法则 A & 法则 B
    # =========================================================================

    def _is_physically_better(
        self, mark: Mark, new_price: float, trigger_bar: RawBar, trigger_index: int, old_tk: TurningK
    ) -> bool:
        return self._refresh_physics.is_physically_better(mark, trigger_bar, trigger_index, old_tk)

    def _compute_ma5_extreme(self, mark: Mark, start_idx: int, end_idx: int):
        """计算 [start_idx, end_idx] 区间内的 MA5 极值。"""
        s = self.s
        if start_idx > end_idx:
            return None
        scope = s.bars_raw[start_idx : end_idx + 1]
        ma5_vals = [b.cache.get('ma5') for b in scope if b.cache.get('ma5') is not None]
        if not ma5_vals:
            return None
        return max(ma5_vals) if mark == Mark.G else min(ma5_vals)

    def _compute_price_extreme(self, mark: Mark, start_idx: int, end_idx: int):
        """计算 [start_idx, end_idx] 区间内的价格极值。"""
        s = self.s
        if start_idx > end_idx:
            return None
        scope = s.bars_raw[start_idx : end_idx + 1]
        if not scope:
            return None
        return max(b.high for b in scope) if mark == Mark.G else min(b.low for b in scope)

    def _snapshot_leg_gate_baseline(self):
        """快照上一时刻包络，供本根异向门控使用。"""
        self._trigger_gate.snapshot_leg_gate_baseline()

    def _update_leg_realtime_extremes(self, bar: RawBar, k_index: int, ma5: float):
        """全时域双向最值包络追踪：实时吞噬 K 线，建立客观门槛。"""
        self._trigger_gate.update_leg_realtime_extremes(bar, k_index, ma5)

    def _check_and_update_reversal_ma5_gate(
        self, new_mark: Mark, candidate_idx: int, candidate_price: float
    ) -> bool:
        """异向准入校验：候选点对标“截至候选K”的同段包络基准。"""
        return self._reversal_gate.check_and_update_reversal_ma5_gate(new_mark, candidate_idx, candidate_price)

    # =========================================================================
    # 私有方法
    # =========================================================================

    def _locate_extreme_with_mode(
        self,
        mark: Mark,
        start_idx: int,
        end_idx_inclusive: int,
    ) -> tuple:
        """按配置选择顶底寻址策略：左侧3K优先 or 区间绝对极值。"""
        return self._extreme_locator.locate_extreme_with_mode(mark, start_idx, end_idx_inclusive)

    def _prepare_refresh_attempt(self, mark: Mark, trigger_index: int) -> dict:
        """为同向刷新构造候选：
        - 条件 A：MA5 极值刷新（由开关控制：左侧3K or 区间绝对极值）
        - 条件 B：仅在 A 不成立时检查。价格极值刷新 + 实体严格越界（同价不算）
        - 刷新失败时仅废弃新候选，不回退旧端点；成功确立后再执行同向替换。
        """
        return self._refresh_physics.prepare_refresh_attempt(mark, trigger_index)

    def _find_extreme_in_range(self, mark: Mark, start_idx: int, end_idx: int) -> tuple:
        """在 K 线区间 [start_idx, end_idx) 内定位绝对极值（不含转折K）。

        参数约定：
        - start_idx: 起始索引（含）
        - end_idx:   结束索引（不含）
        """
        return self._extreme_locator.find_extreme_in_range(mark, start_idx, end_idx)

    def _find_left_3k_extreme(self, mark: Mark, start_idx: int, trigger_index: int) -> tuple:
        """从转折K向左逐个扫描3K，命中第一个即作为候选顶底。"""
        return self._extreme_locator.find_left_3k_extreme(mark, start_idx, trigger_index)

    def _passes_rule1_local(self, mark: Mark, idx: int) -> bool:
        """检查候选 idx 是否满足法则1（含实体-MA5约束）。"""
        return self._extreme_locator.passes_rule1_local(mark, idx)

    def _find_prev_rule1_3k(self, mark: Mark, start_idx: int, from_idx: int):
        """从 from_idx 向左扫描，返回第一个满足法则1（含实体-MA5）的 3K 候选索引。"""
        return self._extreme_locator.find_prev_rule1_3k(mark, start_idx, from_idx)

    def _find_extreme_by_trigger(self, mark: Mark, start_idx: int, trigger_index: int) -> tuple:
        """通过转折K找顶底：
        1) 先看 [start, trigger) 的绝对极值是否等于当前段绝对极值；
        2) 若不等，则从转折K向左做3K扫描，取第一个命中。
        """
        return self._extreme_locator.find_extreme_by_trigger(mark, start_idx, trigger_index)

    def _process_confirmed_trigger(self, trigger_bar: RawBar, trigger_index: int, new_mark: Mark,
                                   preset_ext_idx: int = None,
                                   from_special_rule: bool = False,
                                   allow_special_shift: bool = True,
                                   invalidate_last_on_fail: bool = False):
        """处理已定位转折K后的极值寻址（内部私有）"""
        s = self.s
        min_first_non_overlap_idx = None
        min_non_overlap_idx = None
        if s.turning_ks and s.turning_ks[-1].mark != new_mark:
            last = s.turning_ks[-1]
            turning_idx = get_trigger_index(last)
            # 异向寻址约束：新分型3K不能与前一个转折K重叠
            # => 新候选3K的第一根K索引 first_idx 必须落在：
            #    max(上一个极值中间K+2, 上一个转折K+1)
            #    （对应中间K ext_idx >= first_idx + 1）
            min_first_non_overlap_idx = max(last.k_index + 2, turning_idx + 1)
            min_non_overlap_idx = min_first_non_overlap_idx + 1

        # 1. 寻找极值
        if s.turning_ks:
            last = s.turning_ks[-1]
            if last.mark == new_mark:
                search_start = s.turning_ks[-2].k_index + 1 if len(s.turning_ks) >= 2 else 0
            else:
                # 异向寻址左边界约束到“中间K索引”：
                # first_idx >= max(上一个极值中间K+2, 上一个转折K+1)
                # => ext_idx >= max(...) + 1
                search_start = max(last.k_index + 2, get_trigger_index(last) + 1) + 1
        else:
            search_start = 0

        if preset_ext_idx is not None:
            ext_idx = preset_ext_idx
            ext_bar = s.bars_raw[ext_idx]
            new_price = ext_bar.high if new_mark == Mark.G else ext_bar.low
        else:
            # 反向转折：由开关控制使用“左侧3K”或“区间绝对极值”
            # 同向刷新：沿用“绝对极值优先，必要时回退左侧3K”的寻址策略
            if s.turning_ks and s.turning_ks[-1].mark != new_mark:
                new_price, ext_idx = self._extreme_locator.locate_reversal_extreme_by_trigger_rule(
                    new_mark, search_start, trigger_index
                )
            else:
                new_price, ext_idx = self._find_extreme_by_trigger(new_mark, search_start, trigger_index)

        # 二次保险：即使容错回退，也不允许异向3K与前一转折K重叠
        if min_non_overlap_idx is not None:
            if ext_idx < min_non_overlap_idx:
                return
            # 明确校验“第一根K”不越过异向左边界
            if min_first_non_overlap_idx is not None and (ext_idx - 1) < min_first_non_overlap_idx:
                return

        # 若当前极值点不满足法则1（含实体-MA5），继续向左找下一个满足法则1的 3K 点
        if not self._passes_rule1_local(new_mark, ext_idx):
            alt_idx = self._find_prev_rule1_3k(new_mark, search_start, ext_idx - 1)
            if alt_idx is None:
                return
            ext_idx = alt_idx
            ext_bar = s.bars_raw[ext_idx]
            new_price = ext_bar.high if new_mark == Mark.G else ext_bar.low

        is_reversal = bool(s.turning_ks and s.turning_ks[-1].mark != new_mark)
        # 异向门控采用“先检查后提交”：
        # 1) 常规路径先按旧基准校验，不立即提交；
        # 2) 若触发“特殊法则后移一根”，本次不提交；
        # 3) 后移回调路径（from_special_rule=True）不再重复卡门控。
        if is_reversal and not from_special_rule:
            # 进门检查：对标实时极值包络。
            if not self._check_and_update_reversal_ma5_gate(new_mark, ext_idx, new_price):
                return

        # 同向刷新若命中与上一个完全相同的极值K，只更新时间触发锚点，不重建新候选
        if s.turning_ks and s.turning_ks[-1].mark == new_mark and ext_idx == s.turning_ks[-1].k_index:
            s.turning_ks[-1].turning_k = trigger_bar
            s.turning_ks[-1].turning_k_index = trigger_index
            return
        ext_bar = s.bars_raw[ext_idx]

        new_tk = TurningK(
            symbol=ext_bar.symbol, dt=ext_bar.dt, raw_bar=ext_bar,
            k_index=ext_idx, trigger_k=trigger_bar, trigger_k_index=trigger_index,
            mark=new_mark, price=new_price
        )

        # 2. 候选判定：如果是 Candidate 刷新，同样走 _is_physically_better
        if s.candidate_tk:
            if self._is_physically_better(new_mark, new_price, trigger_bar, trigger_index, s.candidate_tk):
                s.candidate_tk = new_tk
        else:
            s.candidate_tk = new_tk

        # 3. 实时确立检查
        if s.candidate_tk:
            valid, perfect, visible = self._validate_four_rules(s.candidate_tk)
            # 异向初判失败时，尝试 A->D 复判（避免被 C->D 局部语境锁死）。
            if (not valid) and is_reversal:
                c_id = s.turning_ks[-1].cache.get("micro_id") if s.turning_ks else None
                ref_a_id = self._delayed_judgement.get_reversal_fallback_ref_id(c_id) if c_id is not None else None
                if ref_a_id is not None:
                    ref_a_tk = self._delayed_judgement.get_tk_by_id(ref_a_id)
                    if ref_a_tk is not None:
                        valid2, perfect2, visible2 = self._validate_four_rules(s.candidate_tk, override_ref_tk=ref_a_tk)
                        if valid2:
                            valid, perfect, visible = valid2, perfect2, visible2
            if valid:
                # 特殊法则：当转折K本身就是本段极值K，且四法则通过时，
                if (not from_special_rule) and allow_special_shift and ext_idx == trigger_index:
                    s.waiting_special_rule = True
                    s.special_waiting_mark = new_mark
                    s.special_ext_idx_cache = ext_idx
                    s.candidate_tk = None
                    return
                # 候选最终落地前不再需要重复提交，因为前置校验时已执行 commit=True
                self._confirm_candidate(s.candidate_tk, perfect, visible)
            else:
                # 顶底确立是静态裁决：当前不通过即废弃，不跨K线等待
                s.candidate_tk = None
                if invalidate_last_on_fail and s.turning_ks and s.turning_ks[-1].mark == new_mark:
                    s.turning_ks.pop()
                    self._reset_locks()
                    s.segment_start_extreme = s.turning_ks[-1].price if s.turning_ks else None
                    self._update_segments()

    def _confirm_candidate(self, final_tk: TurningK, perfect_struct: bool, has_visible: bool):
        """确认转折点，更新系统状态"""
        s = self.s

        refreshed_old_tk = self._candidate_commit.commit_candidate(final_tk, perfect_struct, has_visible)

        # 延迟结算链：同向刷新先入队，异向确立只推进状态，真实锚点出现后再结算。
        if refreshed_old_tk is not None:
            self._delayed_judgement.enqueue_or_advance(refreshed_old_tk, final_tk)
        else:
            self._delayed_judgement.enqueue_or_advance(None, final_tk)
        self._delayed_judgement.resolve_ready()

        # 锁定点策略
        self._reset_locks()

        s.segment_start_extreme = s.turning_ks[-1].price
        s.candidate_tk = None
        
        self._update_segments()
        self.trend.update_trend_state(final_tk)

    def _reset_locks(self):
        """重建锁定点状态：最新点不锁，倒数二/三锁定。"""
        self._segment_builder.reset_locks()

    def _has_center_between(self, start_k_index: int, end_k_index: int) -> bool:
        """检查 [start_k_index, end_k_index] 区间内是否存在有效（非幽灵）中枢。

        用于滞后审判：判断以备份端点 B 为起点、当前异向转折点 C 为终点的线段 BC
        是否为实线（内部存在中枢）。口径与 _validate_four_rules Rule3 / analyzer 的
        _check_actual_perfection 保持一致。

        覆盖范围：
          A. 已固化中枢（all_centers + potential_centers，排除幽灵）
          B. 正在孵化的活跃中枢（State 2，名分 + 黑K 均已通过才算）
        """
        return self._segment_builder.has_center_between(start_k_index, end_k_index)

    def _validate_four_rules(self, tk: TurningK, override_ref_tk: TurningK = None) -> tuple:
        """确立顶底的核心四法则 (严格同步核心定义文档)
        
        返回值：(is_valid, is_perfect, has_visible)
          - is_valid: 顶底成立门槛（由 ma34_cross_as_valid_gate 控制是否要求法则2）。
          - is_perfect: 结构完整性（法则 3，或在配置下叠加法则2）。决定线段虚实。
          - has_visible: 是否包含肉眼中枢。
        """
        return self._rule_validator.validate_four_rules(tk, override_ref_tk=override_ref_tk)

    def _check_double_k_escape(
        self,
        ref_tk,      # 线段起点 TurningK（可能是 None，冷启动）
        end_tk,      # 线段终点候选 TurningK（当前被验证的候选）
        centers: list,  # 线段内有效中枢列表（已排除幽灵）
        bars: list,
    ) -> bool:
        """独立两K法则：判断线段的顶或底（至少其一）是否满足"独立脱离"条件。

        规则：
          - 取顶（Mark.G）的 3K 组合（极值K前一根、极值K、后一根）；
          - 取底（Mark.D）的 3K 组合（同上）；
          - 对于顶：若 3K 组合中存在任意连续两K，其 low 均 >= 线段内所有中枢的最低 lower_rail，
                    则该顶满足独立两K（两根K完全高于中枢整体下轨）。
          - 对于底：若 3K 组合中存在任意连续两K，其 high 均 <= 线段内所有中枢的最高 upper_rail，
                    则该底满足独立两K（两根K完全低于中枢整体上轨）。
          - 与中枢轨道边缘同价也算作在外（使用 >= / <=）。
          - 顶或底满足其一即返回 True。
          - 若 ref_tk 为 None（冷启动无起点），只检查终点。
        """
        return self._rule_validator.check_double_k_escape(ref_tk, end_tk, centers, bars)

    def _update_segments(self):
        """同步 state.segments，并将对应时间区间内的中枢挂载到线段上"""
        self._segment_builder.update_segments()
