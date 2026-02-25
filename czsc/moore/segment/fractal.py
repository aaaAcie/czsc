# -*- coding: utf-8 -*-
"""
顶底识别引擎（FractalEngine）

职责：
  -转折K触发检测（MA5停滞 + 实体突破 / 跳空触发）
  - 极值寻址（左向3K扫描 + 兜底绝对极值）
  - 唯一性作废法则（转折K自身为极值时右移）
  - 候选顶底管理与刷新条件（_can_refresh_candidate）
  - 四法则验真（_validate_four_rules）
  - 蓝框后移机制
  - 线段同步（_update_segments）

不直接引用 SegmentAnalyzer，全部通过 SegmentState 共享状态容器操作。
TrendEngine 实例通过构造函数注入，供决定穿透/吞噬时调用。
"""
from czsc.py.enum import Mark, Direction
from czsc.py.objects import RawBar
from ..objects import TurningK, MooreSegment


class FractalEngine:
    """顶底识别引擎

    消费 SegmentState，不持有独立数据：通过构造函数拿到状态引用后直接操作。
    trend_engine 注入用于触发穿透判定与吞噬。
    center_engine 注入用于线段确立后执行回滚。
    """

    def __init__(self, state, trend_engine, center_engine):
        self.s = state
        self.trend = trend_engine
        self.center = center_engine

    # =========================================================================
    # 公开接口（供 SegmentAnalyzer 调用）
    # =========================================================================

    def update(self, bar: RawBar, k_index: int, ma5: float):
        """转折K触发、平移与四法则验真引擎"""
        s = self.s
        if s.last_ma5 is None:
            return

        # 判断是否发生特殊跳空触发
        prev_bar = s.bars_raw[k_index - 1]
        is_gap_up   = bar.low  > prev_bar.high
        is_gap_down = bar.high < prev_bar.low

        is_solid_gap_up   = min(bar.open, bar.close) > ma5 and is_gap_up
        is_solid_gap_down = max(bar.open, bar.close) < ma5 and is_gap_down

        # ==================================================================
        # 步骤A：轮询校验。即使没有新触发，已有备选点可能在这一根K线满足了法则二(均线交叉)
        # ==================================================================
        if s.candidate_tk:
            valid_base, perfect_struct = self._validate_four_rules(s.candidate_tk)
            if valid_base:
                self._confirm_candidate(s.candidate_tk, perfect_struct)
                return  # 确立成功不再走候选逻辑

        # ==================================================================
        # 步骤零：处理上一次遗留的"蓝框后移确立"
        # ==================================================================
        if s.waiting_next_as_tk:
            s.waiting_next_as_tk = False
            # 本根 K 线即为确定的转折 K
            self._process_confirmed_trigger(bar, k_index, s.waiting_mark)
            # 确立后不 return，允许这根 K 继续作为普通 K 探测新的刷新/反转

        # --- 探测方向 ---
        # 探测两个方向：
        # 1. 正常寻址 (Reversal)：寻找与当前末端相反的信号
        # 2. 同向刷新 (Refresh)：寻找与当前末端相同的更好信号
        reversal_mark = Mark.D
        refresh_mark  = None
        if s.turning_ks:
            last_mark    = s.turning_ks[-1].mark
            reversal_mark = Mark.G if last_mark == Mark.D else Mark.D
            refresh_mark  = last_mark

        # 构建探测任务列表，优先级：Refresh > Reversal
        tasks = []
        if refresh_mark:
            tasks.append((refresh_mark, True))
        tasks.append((reversal_mark, False))

        for target_mark, is_refresh in tasks:
            triggered = False
            # 基础触发：MA5 停滞 + 价格突破
            if target_mark == Mark.G:  # 找顶
                if ma5 <= s.last_ma5 or is_solid_gap_down:
                    if min(bar.open, bar.close) < ma5:
                        triggered = True
            else:  # 找底
                if ma5 >= s.last_ma5 or is_solid_gap_up:
                    if max(bar.open, bar.close) > ma5:
                        triggered = True

            if triggered:
                # 记录原始触发信号点（解决可视化后移一根的问题）
                s.signal_bar_cache   = bar
                s.signal_index_cache = k_index

                # --- [核心优化] 深层穿透刷新判定 ---
                real_is_refresh = is_refresh
                if not real_is_refresh and len(s.turning_ks) >= 2:
                    # 哲学：如果中间线段极其短促（K线少）或价格被显著跌破，虚实标记不应阻碍趋势延伸
                    prev_same = s.turning_ks[-2]
                    if prev_same.mark == target_mark:
                        # 只要价格更优，且中间点不是一个极其厚实的"完美"结构，就允许穿透
                        is_better = (target_mark == Mark.G and bar.high > prev_same.price) or \
                                    (target_mark == Mark.D and bar.low  < prev_same.price)
                        if is_better:
                            real_is_refresh = True

                s.debug_trigger_count += 1

                # --- 蓝框检测：触发K实体与上一同向极值实体重叠 → 延后一根 ---
                is_blue_box = False
                prev_pk = next((x for x in reversed(s.turning_ks) if x.mark == target_mark), None)
                if prev_pk:
                    if target_mark == Mark.G:
                        if max(bar.open, bar.close) > min(prev_pk.raw_bar.open, prev_pk.raw_bar.close):
                            is_blue_box = True
                    else:
                        if min(bar.open, bar.close) < max(prev_pk.raw_bar.open, prev_pk.raw_bar.close):
                            is_blue_box = True

                if is_blue_box:
                    s.waiting_next_as_tk = True
                    s.waiting_mark = target_mark
                else:
                    self._process_confirmed_trigger(bar, k_index, target_mark)

                break  # 一旦触发，终止探测任务

    # =========================================================================
    # 私有方法
    # =========================================================================

    def _process_confirmed_trigger(self, turning_k_bar: RawBar, turning_k_index: int, new_mark: Mark):
        """处理已定位转折K后的极值寻址与真假校检（内部私有）"""
        s = self.s

        # 使用缓存的信号点作为真实的转折K
        actual_trigger_bar   = s.signal_bar_cache   if s.signal_bar_cache   is not None else turning_k_bar
        actual_trigger_index = s.signal_index_cache if s.signal_index_cache is not None else turning_k_index

        # ==================================================================
        # 步骤二：以确定的"转折K"为右边界，往左寻找最近的相对局部极值（顶/底）
        # ==================================================================
        search_start_idx = 0
        if s.turning_ks:
            # 如果是同向刷新，则搜索范围应追溯到前一个异向点之后
            if s.turning_ks[-1].mark == new_mark:
                search_start_idx = s.turning_ks[-2].k_index + 1 if len(s.turning_ks) >= 2 else 0
            else:
                search_start_idx = s.turning_ks[-1].k_index + 1

        # 搜索区间不包含转折K本身，极值必须在转折信号发生之前
        search_bars = s.bars_raw[search_start_idx : turning_k_index]
        n = len(search_bars)

        extreme_bar    = None
        extreme_k_index = None

        # 从右（转折K侧）往左逐根扫描局部极值
        if n >= 1:
            for i in range(n - 1, -1, -1):
                curr_b = search_bars[i]
                p_idx  = (search_start_idx + i) - 1
                prev_b = s.bars_raw[p_idx] if p_idx >= 0 else None
                next_b = turning_k_bar if i == (n - 1) else search_bars[i + 1]

                if not prev_b or not next_b:
                    continue
                if (new_mark == Mark.G and curr_b.high > prev_b.high and curr_b.high > next_b.high) or \
                   (new_mark == Mark.D and curr_b.low  < prev_b.low  and curr_b.low  < next_b.low):
                    extreme_bar, extreme_k_index = curr_b, search_start_idx + i
                    break

        # 兜底：未找到 3K 局部极值点时，取 search_bars 中的绝对极值
        if extreme_bar is None and n > 0:
            if new_mark == Mark.G:
                extreme_bar = max(search_bars, key=lambda b: b.high)
            else:
                extreme_bar = min(search_bars, key=lambda b: b.low)
            extreme_k_index = search_start_idx + search_bars.index(extreme_bar)

        if not extreme_bar:
            return
        new_price = extreme_bar.high if new_mark == Mark.G else extreme_bar.low

        # --- 唯一性作废法则：转折K 不能与极值K 重合 ---
        # 如果搜索到的极值K == 原始信号K，转折K 顺延为确认K（turning_k_bar）
        if extreme_k_index == actual_trigger_index:
            final_trigger_bar   = turning_k_bar
            final_trigger_index = turning_k_index
        else:
            final_trigger_bar   = actual_trigger_bar
            final_trigger_index = actual_trigger_index

        new_tk = TurningK(
            symbol=extreme_bar.symbol, dt=extreme_bar.dt, raw_bar=extreme_bar,
            k_index=extreme_k_index,
            trigger_k=turning_k_bar, trigger_k_index=turning_k_index,
            mark=new_mark, price=new_price
        )

        # --- 同向刷新价格过滤 ---
        # 这里用的是搜索到的真实极值 new_price，而不是触发K的 bar.high/bar.low
        if s.turning_ks and s.turning_ks[-1].mark == new_mark:
            if new_mark == Mark.G and new_price <= s.turning_ks[-1].price:
                return
            if new_mark == Mark.D and new_price >= s.turning_ks[-1].price:
                return

        # --- 候选刷新：条件A（MA5新生）OR 条件B（价格新极值+实体重叠）---
        if s.candidate_tk:
            if self._can_refresh_candidate(extreme_bar, new_price, new_mark):
                s.candidate_tk = new_tk
        else:
            s.candidate_tk = new_tk

        # --- 四法则验真 ---
        if s.candidate_tk:
            valid_base, perfect_struct = self._validate_four_rules(s.candidate_tk)
            if valid_base:
                s.candidate_tk.trigger_k       = final_trigger_bar
                s.candidate_tk.trigger_k_index = final_trigger_index
                self._confirm_candidate(s.candidate_tk, perfect_struct)

    def _confirm_candidate(self, final_tk: TurningK, perfect_struct: bool):
        """候选顶底确立：执行追加/刷新/穿透逻辑，更新 turning_ks 与线段"""
        s = self.s
        final_tk.is_valid   = True
        final_tk.is_perfect = perfect_struct

        # 同向刷新 / 趋势穿透多层回溯 / 正常追加
        if s.turning_ks:
            if s.turning_ks[-1].mark == final_tk.mark:
                # 正常相邻同向刷新：直接替换末端
                s.turning_ks.pop()
            elif self.trend.check_penetration(final_tk):
                # 趋势级穿透：动态多层吞噬不稳定链
                # segment_start_extreme 在回溯期间保持不变（线段延申而非重置）
                self.trend.consume_imperfect_chain(final_tk)

        # 追加新 TurningK
        s.turning_ks.append(final_tk)

        # 新反向线段确立（第3个点出现），为上一条完整线段的两个端点发放免死金牌
        if len(s.turning_ks) >= 3:
            s.turning_ks[-3].is_locked = True
            s.turning_ks[-2].is_locked = True

        # 更新线段起点极值（仅真正反向时更新，同向刷新时不动）
        if len(s.turning_ks) >= 2 and s.turning_ks[-2].mark != final_tk.mark:
            s.segment_start_extreme = s.turning_ks[-2].price

        # 更新趋势状态
        self.trend.update_trend_state(final_tk)

        self._update_segments()
        s.candidate_tk = None
        self.center.rollback()
        s.potential_centers = []  # 确立后清空暂存区，为下一轮寻找准备

    def _can_refresh_candidate(self, new_extreme_bar: RawBar, new_price: float, new_mark: Mark) -> bool:
        """判断新找到的极值K能否刷新当前候选顶底（candidate_tk）

        刷新条件（A OR B）：
          A（MA5新生）: 新极值K的 MA5 比旧候选的 MA5 更极端
                       找顶: new_ma5 > old_ma5 / 找底: new_ma5 < old_ma5
          B（价格新极值 + 实体重叠）:
                       找顶: new_price > old_price AND 新实体下沿 < 旧候选实体上沿
                       找底: new_price < old_price AND 新实体上沿 > 旧候选实体下沿
        """
        s = self.s
        if not s.candidate_tk:
            return True

        old_bar   = s.candidate_tk.raw_bar
        old_price = s.candidate_tk.price
        new_ma5   = new_extreme_bar.cache.get('ma5', 0.0)
        old_ma5   = old_bar.cache.get('ma5', 0.0)

        if new_mark == Mark.G:
            cond_A = new_ma5 > old_ma5
            body_overlap = min(new_extreme_bar.open, new_extreme_bar.close) < max(old_bar.open, old_bar.close)
            cond_B = (new_price > old_price) and body_overlap
        else:
            cond_A = new_ma5 < old_ma5
            body_overlap = max(new_extreme_bar.open, new_extreme_bar.close) > min(old_bar.open, old_bar.close)
            cond_B = (new_price < old_price) and body_overlap

        return cond_A or cond_B

    def _validate_four_rules(self, tk: TurningK) -> tuple:
        """顶底四法则验真。
        返回: (is_valid, is_perfect)
        """
        s = self.s
        if not s.turning_ks:
            return True, True

        last_tk = s.turning_ks[-1]

        # 确定参考基准点 (ref_tk)
        if last_tk.mark == tk.mark:
            ref_tk = s.turning_ks[-2] if len(s.turning_ks) >= 2 else last_tk
        else:
            ref_tk = last_tk

        bars_between = s.bars_raw[ref_tk.k_index : tk.k_index + 1]

        # 1. 第一法则：3K 极值要求
        # 要求当前确立的极值点 tk.raw_bar 必须是一个局部极值（3K形态：中间高于/低于两侧）
        idx = tk.k_index
        if 0 < idx < len(s.bars_raw) - 1:
            prev_b = s.bars_raw[idx - 1]
            curr_b = s.bars_raw[idx]   # 即 tk.raw_bar
            next_b = s.bars_raw[idx + 1]

            is_3k_ok = False
            if tk.mark == Mark.G:
                if curr_b.high > prev_b.high and curr_b.high > next_b.high:
                    is_3k_ok = True
            else:
                if curr_b.low < prev_b.low and curr_b.low < next_b.low:
                    is_3k_ok = True

            if not is_3k_ok:
                s.debug_rule_fail[1] += 1
                return False, False  # 不满足3K极值，连基础确立都不算

        # 2. 第二法则：两侧大铡刀 (MA34 与 MA5 金死叉)
        # 在 ref_tk 到 tk 这段期间，至少发生一次 MA5/MA34 交叉
        cross_happened = False
        prev_diff = None
        for b in bars_between:
            m5  = b.cache.get('ma5')
            m34 = b.cache.get('ma34')
            if m5 is None or m34 is None:
                continue
            diff = m5 - m34
            if prev_diff is not None:
                if (prev_diff > 0 and diff < 0) or (prev_diff < 0 and diff > 0):
                    cross_happened = True
                    break
            prev_diff = diff

        if not cross_happened:
            s.debug_rule_fail[2] = s.debug_rule_fail.get(2, 0) + 1
            return False, False  # 没交叉，连基础确立都不算

        # 3. 第三法则：结构完整性 (内部必须包含中枢)
        # 不拦截线段生成，但真实写入 is_perfect 标记，供趋势穿透层使用
        is_perfect = True
        if not s.potential_centers:
            s.debug_rule_fail[3] = s.debug_rule_fail.get(3, 0) + 1
            is_perfect = False  # 无中枢 → 微观结构不完美（虚线标记）

        return True, is_perfect

    def _update_segments(self):
        """同步 turning_ks 到 segments 列表（重新构建，确保刷新逻辑一致性）"""
        s = self.s
        s.segments = []
        if len(s.turning_ks) >= 2:
            for i in range(len(s.turning_ks) - 1):
                tk1 = s.turning_ks[i]
                tk2 = s.turning_ks[i + 1]
                direction     = Direction.Up if tk1.mark == Mark.D else Direction.Down
                segment_bars  = s.bars_raw[tk1.k_index : tk2.k_index + 1]

                seg = MooreSegment(
                    symbol=tk1.symbol, start_k=tk1, end_k=tk2,
                    direction=direction, bars=segment_bars
                )
                # 从历史仓库 all_centers 中提取符合该线段时间的中枢进行挂载
                seg.centers = [c for c in s.all_centers if c.start_dt >= tk1.dt and c.end_dt <= tk2.dt]
                s.segments.append(seg)

        # 控制最大数量
        if len(s.segments) > s.max_segments:
            s.segments = s.segments[-s.max_segments:]
