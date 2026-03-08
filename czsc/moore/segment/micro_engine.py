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
from .scope_utils import build_scope_windows, evaluate_scope_refresh, get_trigger_index


class MicroStructureEngine:
    """微观结构引擎"""

    def __init__(self, state, trend_engine, center_engine):
        self.s = state
        self.trend = trend_engine
        self.center = center_engine

    # =========================================================================
    # 公开接口
    # =========================================================================

    def update(self, bar: RawBar, k_index: int, ma5: float):
        """顶底探测主循环"""
        s = self.s
        if s.last_ma5 is None: return

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

        def _is_turning_triggered(target_mark: Mark) -> bool:
            if target_mark == Mark.G:  # 找顶
                return (ma5 <= s.last_ma5 or is_solid_gap_down) and (min(bar.open, bar.close) < ma5)
            # 找底
            return (ma5 >= s.last_ma5 or is_solid_gap_up) and (max(bar.open, bar.close) > ma5)

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
            return
        if reversal_ready:
            _run_attempt(reversal_mark, None)

    # =========================================================================
    # 物理法则：法则 A & 法则 B
    # =========================================================================

    def _is_physically_better(
        self, mark: Mark, new_price: float, trigger_bar: RawBar, trigger_index: int, old_tk: TurningK
    ) -> bool:
        """核心物理实力判定：按 old/new 区间极值比较（Rule 1 OR Rule 2A）。"""
        s = self.s
        old_bar = old_tk.raw_bar

        # 统一使用 old_scope / new_scope 判定“是否对原趋势做出新区间极值”。
        seg_start = old_tk.k_index
        if len(s.turning_ks) >= 2 and s.turning_ks[-1].mark == old_tk.mark:
            seg_start = s.turning_ks[-2].k_index + 1

        old_trigger_idx = get_trigger_index(old_tk)
        scopes = build_scope_windows(s.bars_raw, seg_start, old_trigger_idx, trigger_index)
        if scopes is None:
            return False
        refresh = evaluate_scope_refresh(mark, scopes.old_scope, scopes.new_scope)

        # --- 法则一 (Growth)：区间价格极值刷新 + 实体穿透 ---
        if mark == Mark.G:
            body_top = max(trigger_bar.open, trigger_bar.close)
            old_bottom = min(old_bar.open, old_bar.close)
            rule1 = refresh.price_refreshed and (body_top > old_bottom)
        else:
            body_bottom = min(trigger_bar.open, trigger_bar.close)
            old_top = max(old_bar.open, old_bar.close)
            rule1 = refresh.price_refreshed and (body_bottom < old_top)

        # --- 法则二A (Energy)：区间 MA5 极值刷新（上攻看 max，下行看 min）---
        rule2a = refresh.ma5_refreshed
        if not refresh.ma5_ready:
            # 容错回退：极端冷启动阶段若区间 MA5 缺失，则退回单点比较。
            trigger_ma5 = trigger_bar.cache.get('ma5')
            old_trig_ma5 = old_tk.turning_k.cache.get('ma5') if old_tk.turning_k else None
            if old_trig_ma5 is None:
                old_trig_ma5 = old_bar.cache.get('ma5')
            if trigger_ma5 is not None and old_trig_ma5 is not None:
                if mark == Mark.G:
                    rule2a = trigger_ma5 > old_trig_ma5
                else:
                    rule2a = trigger_ma5 < old_trig_ma5

        return rule1 or rule2a

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

    def _check_and_update_reversal_ma5_gate(self, new_mark: Mark, trigger_index: int) -> bool:
        """异向候选的运行门槛：MA5 或价格任一刷新即可，失败候选同样推进。"""
        s = self.s
        if not s.turning_ks:
            return True

        leg_start = s.turning_ks[-1].k_index
        current_ma5_extreme = self._compute_ma5_extreme(new_mark, leg_start, trigger_index)
        current_price_extreme = self._compute_price_extreme(new_mark, leg_start, trigger_index)
        if current_price_extreme is None:
            return True

        ma5_context_changed = (
            s.reversal_ma5_gate_mark != new_mark
            or s.reversal_ma5_gate_start_k_index != leg_start
        )
        price_context_changed = (
            s.reversal_price_gate_mark != new_mark
            or s.reversal_price_gate_start_k_index != leg_start
        )
        if ma5_context_changed:
            prev_ma5_extreme = None
        else:
            prev_ma5_extreme = s.reversal_ma5_gate_extreme
        if price_context_changed:
            prev_price_extreme = None
        else:
            prev_price_extreme = s.reversal_price_gate_extreme

        if current_ma5_extreme is None or prev_ma5_extreme is None:
            ma5_refreshed = True
        elif new_mark == Mark.G:
            ma5_refreshed = current_ma5_extreme > prev_ma5_extreme
        else:
            ma5_refreshed = current_ma5_extreme < prev_ma5_extreme

        if prev_price_extreme is None:
            price_refreshed = True
        elif new_mark == Mark.G:
            price_refreshed = current_price_extreme > prev_price_extreme
        else:
            price_refreshed = current_price_extreme < prev_price_extreme

        s.reversal_ma5_gate_mark = new_mark
        s.reversal_ma5_gate_start_k_index = leg_start
        if current_ma5_extreme is not None:
            if prev_ma5_extreme is None:
                s.reversal_ma5_gate_extreme = current_ma5_extreme
            elif new_mark == Mark.G:
                s.reversal_ma5_gate_extreme = max(prev_ma5_extreme, current_ma5_extreme)
            else:
                s.reversal_ma5_gate_extreme = min(prev_ma5_extreme, current_ma5_extreme)

        s.reversal_price_gate_mark = new_mark
        s.reversal_price_gate_start_k_index = leg_start
        if prev_price_extreme is None:
            s.reversal_price_gate_extreme = current_price_extreme
        elif new_mark == Mark.G:
            s.reversal_price_gate_extreme = max(prev_price_extreme, current_price_extreme)
        else:
            s.reversal_price_gate_extreme = min(prev_price_extreme, current_price_extreme)

        return ma5_refreshed or price_refreshed

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
        s = self.s
        if s.use_left_3k_locator:
            return self._find_left_3k_extreme(mark, start_idx, end_idx_inclusive)
        return self._find_extreme_in_range(mark, start_idx, end_idx_inclusive + 1)

    def _prepare_refresh_attempt(self, mark: Mark, trigger_index: int) -> dict:
        """为同向刷新构造候选：
        - 条件 A：MA5 极值刷新（由开关控制：左侧3K or 区间绝对极值）
        - 条件 B：仅在 A 不成立时检查。价格极值刷新 + 实体严格越界（同价不算）
        - 刷新失败时仅废弃新候选，不回退旧端点；成功确立后再执行同向替换。
        """
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
        old_scope = scopes.old_scope
        new_scope = scopes.new_scope
        refresh = evaluate_scope_refresh(mark, old_scope, new_scope)

        right_scope = bars[old_trigger_idx + 1 : trigger_index + 1]  # (old_trigger, new_trigger]
        if not right_scope:
            return None

        cond_a = refresh.ma5_refreshed

        # 条件 A：MA5 刷新时，由开关控制寻址方式。
        if cond_a:
            left_end = max(old_trigger_idx + 1, trigger_index - 1)
            ext_price, ext_idx = self._locate_extreme_with_mode(mark, old_trigger_idx - 1, left_end)
            return {
                "ext_idx": ext_idx,
                "allow_special_shift": False,
                "invalidate_last_on_fail": False,
            }

        # 条件 B：仅在 MA5 未刷新时才检查。
        if mark == Mark.G:
            ext_bar = max(right_scope, key=lambda x: x.high)
            ext_price = ext_bar.high
            old_body_edge = min(old_tk.raw_bar.open, old_tk.raw_bar.close)  # 前顶实体下沿
            body_ok = max(ext_bar.open, ext_bar.close) > old_body_edge
            price_refreshed = ext_price > refresh.old_price_ext
        else:
            ext_bar = min(right_scope, key=lambda x: x.low)
            ext_price = ext_bar.low
            old_body_edge = max(old_tk.raw_bar.open, old_tk.raw_bar.close)  # 前底实体上沿
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

    def _find_extreme_in_range(self, mark: Mark, start_idx: int, end_idx: int) -> tuple:
        """在 K 线区间 [start_idx, end_idx) 内定位绝对极值（不含转折K）。

        参数约定：
        - start_idx: 起始索引（含）
        - end_idx:   结束索引（不含）
        """
        s = self.s
        search_bars = s.bars_raw[start_idx : end_idx] # 不含触发K
        if not search_bars:
            trigger = s.bars_raw[end_idx]
            return (trigger.high if mark==Mark.G else trigger.low, end_idx) # 极简容错
        
        if mark == Mark.G:
            ext_bar = max(search_bars, key=lambda b: b.high)
            return ext_bar.high, start_idx + search_bars.index(ext_bar)
        else:
            ext_bar = min(search_bars, key=lambda b: b.low)
            return ext_bar.low, start_idx + search_bars.index(ext_bar)

    def _find_left_3k_extreme(self, mark: Mark, start_idx: int, trigger_index: int) -> tuple:
        """从转折K向左逐个扫描3K，命中第一个即作为候选顶底。"""
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
        return self._find_extreme_in_range(mark, start_idx, trigger_index)

    def _find_extreme_by_trigger(self, mark: Mark, start_idx: int, trigger_index: int) -> tuple:
        """通过转折K找顶底：
        1) 先看 [start, trigger) 的绝对极值是否等于当前段绝对极值；
        2) 若不等，则从转折K向左做3K扫描，取第一个命中。
        """
        s = self.s
        abs_price, abs_idx = self._find_extreme_in_range(mark, start_idx, trigger_index)

        seg_bars = s.bars_raw[start_idx : trigger_index + 1]
        if not seg_bars:
            return abs_price, abs_idx

        seg_ext = max(b.high for b in seg_bars) if mark == Mark.G else min(b.low for b in seg_bars)
        if abs_price == seg_ext:
            return abs_price, abs_idx
        return self._find_left_3k_extreme(mark, start_idx, trigger_index)

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
        if preset_ext_idx is not None:
            ext_idx = preset_ext_idx
            ext_bar = s.bars_raw[ext_idx]
            new_price = ext_bar.high if new_mark == Mark.G else ext_bar.low
        else:
            search_start = 0
            if s.turning_ks:
                # 无论是 Refresh 还是 Reversal，寻址起点都在上一个异向点之后
                last = s.turning_ks[-1]
                if last.mark == new_mark:
                    search_start = s.turning_ks[-2].k_index + 1 if len(s.turning_ks) >= 2 else 0
                else:
                    # 异向寻址左边界约束到“中间K索引”：
                    # first_idx >= max(上一个极值中间K+2, 上一个转折K+1)
                    # => ext_idx >= max(...) + 1
                    search_start = max(last.k_index + 2, get_trigger_index(last) + 1) + 1

            # 反向转折：由开关控制使用“左侧3K”或“区间绝对极值”
            # 同向刷新：沿用“绝对极值优先，必要时回退左侧3K”的寻址策略
            if s.turning_ks and s.turning_ks[-1].mark != new_mark:
                new_price, ext_idx = self._locate_extreme_with_mode(new_mark, search_start, trigger_index)
            else:
                new_price, ext_idx = self._find_extreme_by_trigger(new_mark, search_start, trigger_index)

        # 二次保险：即使容错回退，也不允许异向3K与前一转折K重叠
        if min_non_overlap_idx is not None:
            if ext_idx < min_non_overlap_idx:
                return
            # 明确校验“第一根K”不越过异向左边界
            if min_first_non_overlap_idx is not None and (ext_idx - 1) < min_first_non_overlap_idx:
                return

        if s.turning_ks and s.turning_ks[-1].mark != new_mark:
            if not self._check_and_update_reversal_ma5_gate(new_mark, trigger_index):
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
            if valid:
                # 特殊法则：当转折K本身就是本段极值K，且四法则通过时，
                # 当前转折K无效，后移一根作为新生转折K（不改极值K）。
                if (not from_special_rule) and allow_special_shift and ext_idx == trigger_index:
                    s.waiting_special_rule = True
                    s.special_waiting_mark = new_mark
                    s.special_ext_idx_cache = ext_idx
                    s.candidate_tk = None
                    return
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
        final_tk.is_valid = True
        final_tk.is_perfect = perfect_struct
        final_tk.maybe_is_fake = not perfect_struct
        final_tk.has_visible_center = has_visible
        if final_tk.cache.get("micro_id") is None:
            s.micro_id_seed += 1
            final_tk.cache["micro_id"] = s.micro_id_seed

        if s.turning_ks and s.turning_ks[-1].mark == final_tk.mark:
            ref_tk = s.turning_ks[-2] if len(s.turning_ks) >= 2 else None
        else:
            ref_tk = s.turning_ks[-1] if s.turning_ks else None
        start_idx = ref_tk.k_index if ref_tk else 0
        end_idx = get_trigger_index(final_tk)
        final_tk.cache['leg_ma5_extreme'] = self._compute_ma5_extreme(final_tk.mark, start_idx, end_idx)

        # 同向替换：实现微观延伸（生长）
        if s.turning_ks and s.turning_ks[-1].mark == final_tk.mark:
            s.turning_ks.pop()

        s.turning_ks.append(final_tk)

        # 锁定点策略
        self._reset_locks()

        s.segment_start_extreme = s.turning_ks[-1].price
        s.candidate_tk = None
        
        self._update_segments()
        self.trend.update_trend_state(final_tk)

    def _reset_locks(self):
        """重建锁定点状态：最新点不锁，倒数二/三锁定。"""
        s = self.s
        for tk in s.turning_ks:
            tk.is_locked = False
        if len(s.turning_ks) >= 2:
            s.turning_ks[-2].is_locked = True
        if len(s.turning_ks) >= 3:
            s.turning_ks[-3].is_locked = True

    def _validate_four_rules(self, tk: TurningK) -> tuple:
        """确立顶底的核心四法则 (严格同步核心定义文档)
        
        返回值：(is_valid, is_perfect, has_visible)
          - is_valid: 顶底成立门槛（由 ma34_cross_as_valid_gate 控制是否要求法则2）。
          - is_perfect: 结构完整性（法则 3，或在配置下叠加法则2）。决定线段虚实。
          - has_visible: 是否包含肉眼中枢。
        """
        s = self.s
        bars = s.bars_raw
        ma5_val = bars[-1].cache.get('ma5', 0.0)
        ma34_val = bars[-1].cache.get('ma34', 0.0)

        # ---------------------------------------------------------------------
        # 0. 确定参考点 ref_tk (用于范围扫描)
        # ---------------------------------------------------------------------
        # 若同向刷新场景（当前 tk 与最新 confirmed 同向）→ 跳过一个找异向点作为参考
        if s.turning_ks and s.turning_ks[-1].mark == tk.mark:
            ref_tk = s.turning_ks[-2] if len(s.turning_ks) >= 2 else None
        else:
            ref_tk = s.turning_ks[-1] if s.turning_ks else None

        # 扫描区间定义：
        # - start_idx: 参考锚点 ref_tk 的“中间K”（3K 结构中心）索引，用于定义本段左边界
        # - end_idx:   当前候选 tk 的“中间K”（3K 结构中心）索引，用于定义本段右边界
        # 如果没有参考点（冷启动），左边界回退到 0。
        start_idx = ref_tk.k_index if ref_tk else 0
        end_idx = tk.k_index

        # ---------------------------------------------------------------------
        # 法则 1：局部 3K 极值判定 (刚性)
        # 顶：curr >= prev AND curr >= next
        # ---------------------------------------------------------------------
        rule1 = False
        p_idx = tk.k_index - 1
        n_idx = tk.k_index + 1
        # 注意：next 可能还没出来（在当前 K 线确认时，转折 K 可能就是最新一根）
        # 此时取当前 K 线作为 next 的代理，或者检查已有的 bars 序列
        prev_b = bars[p_idx] if p_idx >= 0 else None
        curr_b = bars[tk.k_index]
        next_b = bars[n_idx] if n_idx < len(bars) else bars[-1]

        if tk.mark == Mark.G:
            rule1 = (curr_b.high >= (prev_b.high if prev_b else -1)) and \
                    (curr_b.high >= next_b.high)
        else:
            rule1 = (curr_b.low <= (prev_b.low if prev_b else 999999)) and \
                    (curr_b.low <= next_b.low)

        # 新增实体-MA5约束：
        # 顶：中间K实体上沿必须上穿该K的MA5；底：中间K实体下沿必须下穿该K的MA5。
        curr_ma5 = curr_b.cache.get('ma5')
        body_top = max(curr_b.open, curr_b.close)
        body_bottom = min(curr_b.open, curr_b.close)
        if tk.mark == Mark.G:
            body_ma5_ok = curr_ma5 is not None and body_top > curr_ma5
        else:
            body_ma5_ok = curr_ma5 is not None and body_bottom < curr_ma5
        if rule1 and not body_ma5_ok:
            s.debug_body_filter += 1
        rule1 = rule1 and body_ma5_ok

        # ---------------------------------------------------------------------
        # 法则 2：两侧大铡刀 (MA5/MA34 金死叉扫描) (刚性)
        # 放松 1 根 K：扫描范围由“中间K到中间K”扩展到
        # “起点3K的第一根K(start_idx-1) 到 终点3K的最后一根K(end_idx+1)”。
        # 穿越检测在相邻两根之间进行，因此循环 i 表示右侧那根 K 的索引（左侧是 i-1）。
        # ---------------------------------------------------------------------
        rule2 = False
        if not ref_tk:
            # 冷启动，由于没有 reference，只要当前处于背离状态即可放行
            if tk.mark == Mark.G: rule2 = bars[-1].close < ma5_val
            else: rule2 = bars[-1].close > ma5_val
        else:
            scan_left_idx = max(0, start_idx - 1)
            scan_right_idx = min(len(bars) - 1, end_idx + 1)
            # 扫描扩展区间内的交叉（比较 i-1 与 i）
            for i in range(scan_left_idx + 1, scan_right_idx + 1):
                b_prev = bars[i-1]
                b_curr = bars[i]
                m5_p, m34_p = b_prev.cache.get('ma5'), b_prev.cache.get('ma34')
                m5_c, m34_c = b_curr.cache.get('ma5'), b_curr.cache.get('ma34')
                if None in (m5_p, m34_p, m5_c, m34_c): continue
                
                # 穿越检测 (Cross Over/Under)
                if (m5_p <= m34_p and m5_c > m34_c) or (m5_p >= m34_p and m5_c < m34_c):
                    rule2 = True
                    break

        # ---------------------------------------------------------------------
        # 法则 3：结构完整性 (级别保障，决定虚实)
        # ---------------------------------------------------------------------
        rule3 = False
        has_v = False
        # A. 检查已固化的中枢（含暂存区）
        all_c = s.all_centers + s.potential_centers
        seg_centers = [
            c for c in all_c
            if start_idx <= c.start_k_index <= end_idx and not getattr(c, 'is_ghost', False)
        ]
        if seg_centers:
            rule3 = True
            if any(c.is_visible for c in seg_centers):
                has_v = True

        # B. 【实时确权】：检查正在孵化的活动中枢 (State 2)
        # 规则：活跃中枢只要"名分（起手三式）+ 黑K质检"均已通过，线段即为实线。
        # 时间截止锚：中枢的确认K（center_line_k_index）必须 <= end_idx，
        #   即必须是"在被评估候选点之前就已形成"的中枢，才具备保护该段的资格。
        #   这防止了候选推进阶段读取"未来"中枢状态而产生的跨时序污染。
        # is_visible 不在此处定性（需正向一笔完整后判断）。
        live_center_qualifies = False
        if not rule3 and s.center_state >= 2 and s.center_line_k:
            is_confirmed = (s.center_method_found is not None and s.center_black_k_pass)
            if is_confirmed and s.center_line_k_index <= end_idx:
                c_start = s.center_start_k_index
                if start_idx <= c_start <= end_idx:
                    rule3 = True
                    live_center_qualifies = True
                    # has_v 故意不升级：is_visible 定性需等中枢固化后由 MooreCenter 决定

        # ---------------------------------------------------------------------
        # 独立两K法则（门槛级，影响 is_valid）
        # 若线段内存在中枢，则顶或底（至少其一）的 3K 组合中，
        # 必须有任意连续两K完全处于中枢区域之外
        # （high/low 与中枢轨道边缘同价也算作在外）。
        # 若线段内无中枢（seg_centers 为空且活跃中枢也不计入），此法则自动通过。
        # ---------------------------------------------------------------------
        rule_double_k = True  # 默认通过（无中枢时不限制）
        # 收集用于独立两K校验的中枢列表（固化中枢 + 活跃中枢边界）
        effective_centers = list(seg_centers)
        if live_center_qualifies:
            # 活跃中枢边界（临时构造轨道值用于独立两K校验）
            from ..objects import MooreCenter
            live_c = MooreCenter(
                type_name="INVISIBLE",
                direction=s.center_direction,
                upper_rail=s.center_upper_rail,
                lower_rail=s.center_lower_rail,
            )
            live_c.start_k_index = s.center_start_k_index
            effective_centers.append(live_c)

        if effective_centers:
            rule_double_k = self._check_double_k_escape(
                ref_tk, tk, effective_centers, bars
            )

        # 交叉规则开关：
        # - True: MA5/MA34 交叉是顶底成立门槛（历史默认）
        # - False: 交叉仅影响虚实，不阻断顶底确立
        if s.ma34_cross_as_valid_gate:
            is_valid = rule1 and rule2 and rule_double_k
            is_perfect = rule3
        else:
            is_valid = rule1 and rule_double_k
            is_perfect = rule3 and rule2
            
        return is_valid, is_perfect, has_v

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
        if not centers:
            return True

        # 线段内所有中枢的边界（并集，取最宽结界）
        seg_upper = max(c.upper_rail for c in centers)
        seg_lower = min(c.lower_rail for c in centers)

        def get_3k(tk) -> list:
            """获取极值K的3K组合（前一、当前、后一），边界安全"""
            idx = tk.k_index
            result = []
            if idx - 1 >= 0:
                result.append(bars[idx - 1])
            result.append(bars[idx])
            if idx + 1 < len(bars):
                result.append(bars[idx + 1])
            return result

        def two_k_above_lower(three_ks: list) -> bool:
            """顶的独立两K：任意连续两K的 low 均 >= seg_lower"""
            for j in range(len(three_ks) - 1):
                if three_ks[j].low >= seg_lower and three_ks[j + 1].low >= seg_lower:
                    return True
            return False

        def two_k_below_upper(three_ks: list) -> bool:
            """底的独立两K：任意连续两K的 high 均 <= seg_upper"""
            for j in range(len(three_ks) - 1):
                if three_ks[j].high <= seg_upper and three_ks[j + 1].high <= seg_upper:
                    return True
            return False

        # 检查终点（end_tk）
        end_3k = get_3k(end_tk)
        if end_tk.mark == Mark.G:
            if two_k_above_lower(end_3k):
                return True
        else:
            if two_k_below_upper(end_3k):
                return True

        # 检查起点（ref_tk）
        if ref_tk is not None:
            start_3k = get_3k(ref_tk)
            if ref_tk.mark == Mark.G:
                if two_k_above_lower(start_3k):
                    return True
            else:
                if two_k_below_upper(start_3k):
                    return True

        return False

    def _update_segments(self):
        """同步 state.segments，并将对应时间区间内的中枢挂载到线段上"""
        s = self.s
        s.segments = []
        if len(s.turning_ks) < 2: return
        
        # 获取所有可用的中枢仓库（包含历史和潜在）
        all_avail_centers = s.all_centers + s.potential_centers

        for i in range(len(s.turning_ks) - 1):
            tk1 = s.turning_ks[i]
            tk2 = s.turning_ks[i+1]
            direction = Direction.Up if tk2.mark == Mark.G else Direction.Down
            
            seg = MooreSegment(
                symbol=tk1.symbol,
                start_k=tk1,
                end_k=tk2,
                direction=direction
            )
            
            # --- 挂载中枢逻辑（用于图表显示 K0 和 确认K） ---
            seg.centers = []
            # 仅挂载事实仓（micro_centers）中的非幽灵中枢
            for c in s.micro_centers:
                if getattr(c, 'is_ghost', False):
                    continue
                c_confirm_dt = c.confirm_k.dt if c.confirm_k else c.start_dt
                if not c_confirm_dt: continue
                # 判定中枢落在本线段序列内
                if tk1.dt <= c_confirm_dt <= tk2.dt:
                    seg.centers.append(c)

            # --- 【核心修复】：物理结构校正 ---
            # 如果重播或生长找回了中枢，则该线段应该是"实"的（Perfect）
            if seg.centers:
                tk2.is_perfect = True
                # 与 is_perfect 同步：一旦被结构校正为实线端点，撤销宏观疑假标记
                tk2.maybe_is_fake = False
            
            s.segments.append(seg)
