# -*- coding: utf-8 -*-
"""
顶底识别引擎（FractalEngine）

职责：
  - 转折K触发检测（MA5停滞 + 实体突破 / 跳空触发）
  - 极值寻址（左向扫 3K 局部极值 / 兜底绝对极值）
  - 同向刷新（微观生长）：满足条件 A（MA5新生）或 条件 B（价格破位+实体穿透）
  - 蓝框后移、候选管理、四法则验真
"""
from datetime import datetime
from czsc.py.enum import Mark, Direction
from czsc.py.objects import RawBar
from ..objects import TurningK, MooreSegment


class FractalEngine:
    """顶底识别引擎"""

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

        # --- 2. 蓝框残留：处理上一根 K 线挂起的信号 ---
        if s.waiting_next_as_tk:
            s.waiting_next_as_tk = False
            self._process_confirmed_trigger(bar, k_index, s.waiting_mark)

        # --- 3. 新信号探测 ---
        # 探测方向优先级：Refresh（同向刷新/微观生长） > Reversal（正常转折）
        reversal_mark = Mark.D
        refresh_mark  = None
        if s.turning_ks:
            last_mark = s.turning_ks[-1].mark
            reversal_mark = Mark.G if last_mark == Mark.D else Mark.D
            refresh_mark  = last_mark

        tasks = []
        if refresh_mark: tasks.append((refresh_mark, True))
        tasks.append((reversal_mark, False))

        for target_mark, is_refresh in tasks:
            triggered = False
            if target_mark == Mark.G:  # 找顶
                if ma5 <= s.last_ma5 or is_solid_gap_down:
                    if min(bar.open, bar.close) < ma5: triggered = True
            else:  # 找底
                if ma5 >= s.last_ma5 or is_solid_gap_up:
                    if max(bar.open, bar.close) > ma5: triggered = True

            if triggered:
                # 【微观生长法则准入】：同向刷新必须在物理实力上通过判定
                if is_refresh:
                     # 试探性拉取新极值点
                     new_price, _ = self._find_extreme_in_range(target_mark, s.turning_ks[-1].k_index + 1, k_index)
                     if not self._is_physically_better(target_mark, new_price, bar, s.turning_ks[-1]):
                         continue 

                s.debug_trigger_count += 1
                
                # 蓝框检测：触发K实体与上一同向极值实体是否有冲突
                is_blue_box = self._check_blue_box(target_mark, bar)
                if is_blue_box:
                    s.waiting_next_as_tk, s.waiting_mark = True, target_mark
                else:
                    self._process_confirmed_trigger(bar, k_index, target_mark)
                break

    # =========================================================================
    # 物理法则：法则 A & 法则 B
    # =========================================================================

    def _is_physically_better(self, mark: Mark, new_price: float, trigger_bar: RawBar, old_tk: TurningK) -> bool:
        """核心物理实力判定：判断新点是否有资格刷新旧点（微观生长法则：Rule 1 OR Rule 2A）"""
        old_bar = old_tk.raw_bar
        new_ma5 = trigger_bar.cache.get('ma5', 0.0)
        old_ma5 = old_bar.cache.get('ma5', 0.0)
        
        # --- 法则一 (Growth)：价格新极值 + 实体穿透 ---
        rule1 = False
        if mark == Mark.G:
            body_top = max(trigger_bar.open, trigger_bar.close)
            old_bottom = min(old_bar.open, old_bar.close)
            if new_price > old_tk.price and body_top > old_bottom:
                rule1 = True
        else:
            body_bottom = min(trigger_bar.open, trigger_bar.close)
            old_top = max(old_bar.open, old_bar.close)
            if new_price < old_tk.price and body_bottom < old_top:
                rule1 = True

        # --- 法则二A (Energy)：MA5 能量覆盖 ---
        rule2a = False
        if mark == Mark.G:
            if new_ma5 > old_ma5: rule2a = True
        else:
            if new_ma5 < old_ma5: rule2a = True

        return rule1 or rule2a

    # =========================================================================
    # 私有方法
    # =========================================================================

    def _find_extreme_in_range(self, mark: Mark, start_idx: int, end_idx: int) -> tuple:
        """在 K 线区间内精准定位顶/底"""
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

    def _check_blue_box(self, mark: Mark, bar: RawBar) -> bool:
        """检测触发K实体与上一同向极值实体是否有重叠冲突"""
        s = self.s
        prev_pk = next((x for x in reversed(s.turning_ks) if x.mark == mark), None)
        if not prev_pk: return False
        
        if mark == Mark.G:
            return max(bar.open, bar.close) > min(prev_pk.raw_bar.open, prev_pk.raw_bar.close)
        else:
            return min(bar.open, bar.close) < max(prev_pk.raw_bar.open, prev_pk.raw_bar.close)

    def _process_confirmed_trigger(self, trigger_bar: RawBar, trigger_index: int, new_mark: Mark):
        """处理已定位转折K后的极值寻址（内部私有）"""
        s = self.s
        # 1. 寻找极值
        search_start = 0
        if s.turning_ks:
            # 无论是 Refresh 还是 Reversal，寻址起点都在上一个异向点之后
            last = s.turning_ks[-1]
            if last.mark == new_mark:
                search_start = s.turning_ks[-2].k_index + 1 if len(s.turning_ks) >= 2 else 0
            else:
                search_start = last.k_index + 1
        
        # 暂时简化为绝对极值寻址（3K局部寻址在此基础上可后期再切回）
        new_price, ext_idx = self._find_extreme_in_range(new_mark, search_start, trigger_index)
        ext_bar = s.bars_raw[ext_idx]

        new_tk = TurningK(
            symbol=ext_bar.symbol, dt=ext_bar.dt, raw_bar=ext_bar,
            k_index=ext_idx, trigger_k=trigger_bar, trigger_k_index=trigger_index,
            mark=new_mark, price=new_price
        )

        # 2. 候选判定：如果是 Candidate 刷新，同样走 _is_physically_better
        if s.candidate_tk:
            if self._is_physically_better(new_mark, new_price, trigger_bar, s.candidate_tk):
                s.candidate_tk = new_tk
        else:
            s.candidate_tk = new_tk

        # 3. 实时确立检查
        if s.candidate_tk:
            valid, perfect, visible = self._validate_four_rules(s.candidate_tk)
            if valid:
                self._confirm_candidate(s.candidate_tk, perfect, visible)

    def _confirm_candidate(self, final_tk: TurningK, perfect_struct: bool, has_visible: bool):
        """确认转折点，更新系统状态"""
        s = self.s
        final_tk.is_valid = True
        final_tk.is_perfect = perfect_struct
        final_tk.maybe_is_fake = not perfect_struct
        final_tk.has_visible_center = has_visible

        # 同向替换：实现微观延伸（生长）
        if s.turning_ks and s.turning_ks[-1].mark == final_tk.mark:
            s.turning_ks.pop()

        s.turning_ks.append(final_tk)

        # 锁定点策略
        for tk in s.turning_ks: tk.is_locked = False
        if len(s.turning_ks) >= 2:
            s.turning_ks[-2].is_locked = True
        if len(s.turning_ks) >= 3:
            s.turning_ks[-3].is_locked = True

        s.segment_start_extreme = s.turning_ks[-1].price
        s.candidate_tk = None
        
        self._update_segments()
        self.trend.update_trend_state(final_tk)

    def _validate_four_rules(self, tk: TurningK) -> tuple:
        """确立顶底的核心四法则 (严格同步核心定义文档)
        
        返回值：(is_valid, is_perfect, has_visible)
          - is_valid: 物理成立门槛（法则 1+2）。若为 False，则该顶底不建立。
          - is_perfect: 结构完整性（法则 3）。决定线段虚实。
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

        # 如果没有参考点（冷启动），法则 2 默认通过 (无法执行扫描)
        start_idx = ref_tk.k_index if ref_tk else 0
        end_idx   = tk.k_index

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

        # ---------------------------------------------------------------------
        # 法则 2：两侧大铡刀 (MA5/MA34 金死叉扫描) (刚性)
        # 在 [ref_tk.k_pos, tk.k_pos] 之间必须发生过一次穿越
        # ---------------------------------------------------------------------
        rule2 = False
        if not ref_tk:
            # 冷启动，由于没有 reference，只要当前处于背离状态即可放行
            if tk.mark == Mark.G: rule2 = bars[-1].close < ma5_val
            else: rule2 = bars[-1].close > ma5_val
        else:
            # 扫描区间内的交叉
            for i in range(start_idx + 1, end_idx + 1):
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
        for c in all_c:
            # 只要中枢的生命周期与本段有实质交集，且起始于本段，则构成本段的基础结构
            if start_idx <= c.start_k_index <= end_idx and not getattr(c, 'is_ghost', False):
                rule3 = True
                if c.is_visible:
                    has_v = True
                    break 

        # B. 【实时确权】：检查正在孵化的活动中枢 (State 2)
        # 规则：活跃中枢只要"名分（起手三式）+ 黑K质检"均已通过，线段即为实线。
        # 时间截止锚：中枢的确认K（center_line_k_index）必须 <= end_idx，
        #   即必须是"在被评估候选点之前就已形成"的中枢，才具备保护该段的资格。
        #   这防止了候选推进阶段读取"未来"中枢状态而产生的跨时序污染。
        # is_visible 不在此处定性（需正向一笔完整后判断）。
        if not rule3 and s.center_state >= 2 and s.center_line_k:
            is_confirmed = (s.center_method_found is not None and s.center_black_k_pass)
            if is_confirmed and s.center_line_k_index <= end_idx:
                c_start = s.center_start_k_index
                if start_idx <= c_start <= end_idx:
                    rule3 = True
                    # has_v 故意不升级：is_visible 定性需等中枢固化后由 MooreCenter 决定
        is_valid = rule1 and rule2
        is_perfect = rule3
            
        return is_valid, is_perfect, has_v

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
            for c in all_avail_centers:
                c_confirm_dt = c.confirm_k.dt if c.confirm_k else c.start_dt
                if not c_confirm_dt: continue
                # 判定中枢落在本线段序列内
                if tk1.dt <= c_confirm_dt <= tk2.dt:
                    seg.centers.append(c)

            # --- 【核心修复】：物理结构校正 ---
            # 如果重播或生长找回了中枢，或者满足两K脱离，则该线段应该是“实”的（Perfect）
            if seg.centers:
                tk2.is_perfect = True
            
            # TODO: 后续可在此处增加“两K脱离”逻辑
            
            s.segments.append(seg)
