# -*- coding: utf-8 -*-
"""
中枢识别引擎（CenterEngine）

职责：
  - 追踪 K0 锚点与确认K，识别双轨线段中枢（_update_center_engine）
  - 判定中枢类别（肉眼 / 非肉眼）并完成定轨挂载（_dispatch_and_mount_center）
  - 在线段确立后回滚游标（rollback）

不直接引用 SegmentAnalyzer，全部通过 SegmentState 共享状态容器操作。
"""
from czsc.py.enum import Direction
from czsc.py.objects import RawBar
from ..objects import MooreCenter


class CenterEngine:
    """中枢识别引擎

    消费 SegmentState，不持有独立数据：通过构造函数拿到状态引用后直接操作。
    """

    def __init__(self, state):
        # state: SegmentState（避免循环 import，类型注解用字符串）
        self.s = state

    # =========================================================================
    # 公开接口（供 SegmentAnalyzer 调用）
    # =========================================================================

    def update(self, bar: RawBar, k_index: int):
        """在最新的线段上，追踪并构建双极轨道中枢"""
        s = self.s
        # 寻找方向参考 last_confirmed_tk
        # 中枢方向与当前正在形成的线段方向一致
        direction = Direction.Up
        if s.turning_ks:
            # 如果有已确立的转折K，则中枢方向与当前正在形成的线段方向一致
            direction = Direction.Up if s.turning_ks[-1].mark.value == 'D' else Direction.Down
        else:
            # 如果还没有确立的转折K，则根据MA5方向判断
            if s.last_ma5 is not None:
                if bar.cache.get('ma5', 0) > s.last_ma5:
                    direction = Direction.Up
                elif bar.cache.get('ma5', 0) < s.last_ma5:
                    direction = Direction.Down
                else:
                    return  # MA5持平，方向不明，不处理中枢

        ma5 = bar.cache.get('ma5', 0)

        # =========================================================
        # 统一核心游标：无论肉眼或非肉眼，建立中枢必须首先完成起手式：
        # 步骤一：抓锚点 K0 (绝密发源地)
        # 步骤二：等破坏，获取确认K (中枢线的奠基人)
        # =========================================================

        # --- 步骤一：抓锚点 K0 ---
        if s.center_state == 0:
            # K0 的实体必须绝对站在 MA5 正侧 (比如上涨线段，K实体完全在MA5之上)
            is_pure = False
            if direction == Direction.Up:
                is_pure = bar.close > ma5
            else:
                is_pure = bar.close < ma5

            if is_pure:
                # 检查是否与前一个历史中枢重叠
                has_overlap = False
                if s.potential_centers:
                    prev_center = s.potential_centers[-1]
                    # 如果当前K0与上一个中枢有重叠，则不作为新的K0
                    if not (bar.low > prev_center.upper_rail or bar.high < prev_center.lower_rail):
                        has_overlap = True

                if not has_overlap:
                    s.current_k0 = bar
                    s.center_state = 1

        # --- 步骤二：等破坏，获取确认K ---
        elif s.center_state == 1:
            # 2a. 如果当前 K 依然满足纯正侧条件，则滚动更新 K0（保持最新的纯正侧 K）
            is_still_pure = False
            if direction == Direction.Up:
                is_still_pure = bar.close > ma5
            else:
                is_still_pure = bar.close < ma5

            if is_still_pure:
                # K0 滚动刷新为最新的纯正侧 K
                s.current_k0 = bar
                return

            # 2b. 检查是否触发确认K（实体反穿 MA5）
            is_break = False
            if direction == Direction.Up:
                is_break = bar.close < ma5  # 常规反穿：跌破MA5
            else:
                is_break = bar.close > ma5

            if is_break:
                s.center_state = 2
                # 此时，我们拿到了完整的起手素材 [K0, ..., confirm_k]
                # 开始交由底层路线判断是肉眼中枢还是非肉眼中枢，并进行定轨挂载
                self._dispatch_and_mount_center(direction, s.current_k0, bar, k_index)

    def rollback(self):
        """回滚中枢巡航游标（在线段确立后调用）"""
        s = self.s
        s.center_state = 0
        s.current_k0 = None

    # =========================================================================
    # 私有方法
    # =========================================================================

    def _dispatch_and_mount_center(self, direction: Direction, k0: RawBar, confirm_k: RawBar, cf_index: int):
        """定性中枢类别、定轨并存入 potential_centers"""
        s = self.s
        # =========================================================
        # 第一阶段判定：是否满足"肉眼可见中枢"的震荡阈值
        # 判定条件：MA5 发生了明显的折返（至少1次斜率正负交替）
        # 同时记录"第一次折返时的 MA5 极值"，作为肉眼中枢的另一条轨道基准
        # =========================================================
        k0_idx = s.bars_raw.index(k0)
        past_bars = s.bars_raw[k0_idx : cf_index + 1]

        slope_flips = 0
        prev_slope = 0
        first_flip_ma5 = None  # 第一次"有效方向"折返时的 MA5 极值（最早的峰/谷）
        for i in range(1, len(past_bars)):
            m1 = past_bars[i-1].cache.get('ma5', 0)
            m2 = past_bars[i].cache.get('ma5', 0)
            slope = m2 - m1
            if prev_slope != 0 and slope != 0:
                is_flip = (prev_slope > 0 and slope < 0) or (prev_slope < 0 and slope > 0)
                if is_flip:
                    slope_flips += 1
                    if first_flip_ma5 is None:
                        # 区分方向：上涨段找第一个波峰（pos→neg），下跌段找第一个波谷（neg→pos）
                        is_peak_flip   = (prev_slope > 0 and slope < 0)  # MA5 从上升变下降 = 峰顶
                        is_trough_flip = (prev_slope < 0 and slope > 0)  # MA5 从下降变上升 = 谷底
                        if direction == Direction.Up and is_peak_flip:
                            first_flip_ma5 = m1  # 上涨段需要波峰作为上轨
                        elif direction == Direction.Down and is_trough_flip:
                            first_flip_ma5 = m1  # 下跌段需要波谷作为下轨
            prev_slope = slope

        # 0. 初始化
        start_dt = k0.dt
        is_visible = False
        first_flip_ma5 = None  # 注：重置，以下实际判断复用 slope_flips

        if slope_flips >= 1:
            is_visible = True

        ma5_confirm = confirm_k.cache.get('ma5', 0)
        ma5_k0 = k0.cache.get('ma5', 0)

        # 1. 中枢线取值（confirm_k 实体价格，非 MA5 本身）
        # 双跳空判断：
        #   价格跳空：confirm_k 与 k0 价格区间无重叠（两根K直接比较）
        #   实体跳空：confirm_k 实体不接触 MA5（实体完全越过 MA5）
        if direction == Direction.Up:
            price_gap    = confirm_k.high < k0.low                             # confirm_k 完全在 k0 下方
            body_gap_ma5 = max(confirm_k.open, confirm_k.close) < ma5_confirm  # 实体完全在 MA5 下方
        else:
            price_gap    = confirm_k.low > k0.high                             # confirm_k 完全在 k0 上方
            body_gap_ma5 = min(confirm_k.open, confirm_k.close) > ma5_confirm  # 实体完全在 MA5 上方

        is_double_gap = price_gap and body_gap_ma5

        if direction == Direction.Up:
            if is_double_gap:
                center_line = max(confirm_k.open, confirm_k.close)  # 实体上沿：离 MA5 最近
            else:
                center_line = min(confirm_k.open, confirm_k.close)  # 实体下沿：反突破均线后的价
        else:
            if is_double_gap:
                center_line = min(confirm_k.open, confirm_k.close)  # 实体下沿：离 MA5 最近
            else:
                center_line = max(confirm_k.open, confirm_k.close)  # 实体上沿：反突破均线后的价

        # 2. 定轨法则分支
        # 中枢线即对应轨：上升线段 → 下轨；下跌线段 → 上轨
        # 另一轨（上升→上轨 / 下跌→下轨）由 is_visible 逻辑决定
        if is_visible:
            # 肉眼中枢：另一轨取 MA5 第一次折返的极值（无折返则兜底用 K0 的 MA5）
            ref_ma5 = first_flip_ma5 if first_flip_ma5 is not None else ma5_k0
            if direction == Direction.Up:
                lower_rail = center_line  # 中枢线 = 下轨
                upper_rail = ref_ma5      # 另一轨 = MA5 折返峰
            else:
                upper_rail = center_line  # 中枢线 = 上轨
                lower_rail = ref_ma5      # 另一轨 = MA5 折返谷
        else:
            # 非肉眼中枢：寻找 K0 到 ConfirmK 之间通过 CenterLine 的 3K 缠绕区间
            cross_bars = []
            start_search_idx = s.turning_ks[-1].k_index if s.turning_ks else 0
            for i in reversed(range(max(start_search_idx, k0_idx), cf_index)):
                cb = s.bars_raw[i]
                if cb.low <= center_line <= cb.high:
                    cross_bars.insert(0, cb)
                else:
                    if len(cross_bars) < 3:
                        cross_bars = []
                    else:
                        break

            # 非肉眼中枢：center_line 为对应轨，另一轨由 3K 重叠区决定
            if direction == Direction.Up:
                lower_rail = center_line  # 中枢线 = 下轨
                upper_rail = center_line  # 另一轨默认同值，待 3K 确认后更新
            else:
                upper_rail = center_line  # 中枢线 = 上轨
                lower_rail = center_line  # 另一轨默认同值，待 3K 确认后更新

            if len(cross_bars) >= 3:
                leftmost_3k = cross_bars[0:3]
                start_dt = leftmost_3k[0].dt
                overlap_high = min(b.high for b in leftmost_3k)
                overlap_low = max(b.low for b in leftmost_3k)
                if direction == Direction.Up:
                    upper_rail = overlap_high  # 另一轨：3K 重叠上沿
                else:
                    lower_rail = overlap_low   # 另一轨：3K 重叠下沿

            # --- 隐性中枢验真 ---
            extreme_ref = s.turning_ks[-1].raw_bar if s.turning_ks else k0
            # 式2: 5K重叠
            intersect_high = min(extreme_ref.high, confirm_k.high)
            intersect_low  = max(extreme_ref.low, confirm_k.low)
            if intersect_high >= intersect_low:
                overlap_count = sum(
                    1 for i in range(k0_idx, cf_index + 1)
                    if s.bars_raw[i].low <= intersect_high and s.bars_raw[i].high >= intersect_low
                )
                # valid_center = overlap_count >= 5  # 当前已注释物理形态拦截

            # 式3: 三笔纯势
            # （保留代码结构，当前暂不拦截，只要完成反穿即视为中枢形成）

        # =========================================================
        # 3. 中枢向右扩张
        # =========================================================
        end_dt = confirm_k.dt
        for i in range(cf_index + 1, len(s.bars_raw)):
            expand_bar = s.bars_raw[i]
            if expand_bar.high >= lower_rail and expand_bar.low <= upper_rail:
                end_dt = expand_bar.dt
            else:
                break

        # 4. 生成与排他挂载
        type_str = "VISIBLE" if is_visible else "INVISIBLE"
        center = MooreCenter(
            type_name=type_str, direction=direction,
            anchor_k0=k0, confirm_k=confirm_k,
            center_line=center_line, upper_rail=upper_rail, lower_rail=lower_rail,
            start_dt=start_dt, end_dt=end_dt
        )

        if s.potential_centers:
            last_c = s.potential_centers[-1]
            if center.lower_rail <= last_c.upper_rail and center.upper_rail >= last_c.lower_rail:
                if not last_c.is_visible and is_visible:
                    # 肉眼霸权：剔除前面重合的隐性
                    s.potential_centers.pop()
                elif is_visible and last_c.is_visible:
                    # 双肉眼重合扩张
                    last_c.end_dt = end_dt
                    last_c.upper_rail = max(last_c.upper_rail, upper_rail)
                    last_c.lower_rail = min(last_c.lower_rail, lower_rail)
                    self.rollback()
                    return
                else:
                    self.rollback()
                    return

        s.potential_centers.append(center)
        self.rollback()
