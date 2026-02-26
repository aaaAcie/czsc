# -*- coding: utf-8 -*-
"""
中枢识别引擎（CenterEngine）

核心设计：观测病房（Pending Center）四步状态机
  State 0: 找 K0（纯正侧锚点）
  State 1: 等待确认K（第一根反穿 MA5 的 K，即中枢线K）
  State 2: CENTER_FORMING — 逐 bar 增量推进：
      步骤一（入场）：立刻向左定初始结界（center_line + 最左2K重叠）
      步骤二（推进）：价格与结界有交集 → end_dt 不断居新
      步骤三（升级）：MA5 出现波谷/波峰 → 升级为肉中枢，更新另一轨
      步骤四（关闭）：价格完全脱轨 → 固化存储 → 回 State 0

设计哲学：
  - 无魔法数字（Δ），窗口纯靠价格接触自然推进
  - 两类中枢（肉眼/隐式）不在入场时判断，在推进中动态升级
  - 一旦固化即不再修改，后续线段回溯不影响已确定中枢
"""
from czsc.py.enum import Direction, Mark
from czsc.py.objects import RawBar
from ..objects import MooreCenter


class CenterEngine:
    """中枢识别引擎（观测病房模式）"""

    def __init__(self, state):
        # state: SegmentState
        self.s = state

    # =========================================================================
    # 公开接口（供 SegmentAnalyzer 调用）
    # =========================================================================

    def update(self, bar: RawBar, k_index: int):
        """逐 bar 推进中枢状态机"""
        s = self.s

        # 获取宏观的绝对锚点和方向 (仅用于寻找起始点)
        anchor_idx, macro_dir = self._get_macro_anchor()

        ma5 = bar.cache.get('ma5', 0)

        # =====================================================================
        # 1. 确定当前参考方向（物理隔离法则）
        # 如果已经有了中枢（State 1 或 2），必须对该中枢的方向绝对忠诚！
        # 只有在 State 0（空仓寻猎）时，才去向宏观引擎请示大方向。
        # =====================================================================
        if s.center_state > 0:
            current_dir = s.center_direction
        else:
            current_dir = macro_dir
            if current_dir is None:
                return

        # =====================================================================
        # 2. 全局物理属性计算（严格基于 current_dir）
        # =====================================================================
        is_pure = False
        is_break = False
        if current_dir == Direction.Up:
            is_pure = min(bar.open, bar.close) > ma5
            is_break = min(bar.open, bar.close) < ma5
        else:
            is_pure = max(bar.open, bar.close) < ma5
            is_break = max(bar.open, bar.close) > ma5

        # =====================================================================
        # 【新增的核心防线】：空间隔离扫描
        # 校验当前 bar 是否掉进了同向老中枢的结界内
        # =====================================================================
        has_overlap = False
        if s.potential_centers:
            prev_c = next((c for c in reversed(s.potential_centers) if c.direction == current_dir), None)
            if prev_c and not (bar.low > prev_c.upper_rail or bar.high < prev_c.lower_rail):
                has_overlap = True

        # 维护备胎 K0（必须是纯洁且不重叠的）
        if is_pure and not has_overlap:
            s.latest_k0 = bar

        # =====================================================================
        # State 0：找 K0（必须在老中枢领地之外！）
        # =====================================================================
        if s.center_state == 0:
            if is_pure and not has_overlap:
                s.current_k0 = bar
                s.center_direction = current_dir
                s.center_anchor_idx = anchor_idx  # 死死锁定这个中枢的物理发源锚点
                s.center_state = 1

        # =====================================================================
        # State 1：找确认K（滚动期间绝不允许退回老中枢防区！）
        # =====================================================================
        elif s.center_state == 1:
            if is_pure:
                if has_overlap:
                    # 【Bug 2 完美修复】：K0 滚动期间行情漂移回了老中枢防区！
                    # 种子受到污染，立刻作废！直接回滚，让行情乖乖去做老中枢的延伸！
                    self.rollback()
                    return
                s.current_k0 = bar
                return

            # 确认K：实体的一边反穿了 MA5
            if is_break:
                s.center_state = 2
                # 步骤一：入场即定初始结界（向左 look）
                self._enter_forming_state(current_dir, s.current_k0, bar, k_index)
                # confirm_k 本身不再走 State 2 逻辑，直接返回
                return

        # =====================================================================
        # State 2：CENTER_FORMING — 观测病房逐 bar 推进
        # =====================================================================
        elif s.center_state == 2:
            direction = current_dir  # 使用进入 State 2 时锁定的方向

            # 步骤四判断：价格完全脱轨？
            bar_intersects = (bar.high >= s.center_lower_rail
                              and bar.low  <= s.center_upper_rail)

            if not bar_intersects:
                # 步骤四：固化 → 存储 → 回 State 0
                self._finalize_and_mount_center()
                self.rollback()
                return

            # 步骤二：仍在结界内，更新右端时间和索引
            s.center_end_dt      = bar.dt
            s.center_end_k_index = k_index



            # =========================================================
            # 【抢占逻辑】：观测病房的成王败寇（带正向刷新棘轮）
            # =========================================================
            if is_break:
                # 灵魂拷问 1：老中枢现在有资格“成型”吗？（包含黑K校验）
                is_formed = self._check_center_formation()
                
                if not is_formed:
                    # 灵魂拷问 2：有备胎 K0 吗？
                    if s.latest_k0 is not None:
                        new_k0 = s.latest_k0
                        ma5_confirm = bar.cache.get('ma5', 0)
                        
                        # --- 计算这根新确认K的假想中枢线 ---
                        if direction == Direction.Up:
                            old_center_line = s.center_lower_rail
                            price_gap = bar.high < new_k0.low
                            body_gap_ma5 = max(bar.open, bar.close) < ma5_confirm
                            is_double_gap = price_gap and body_gap_ma5
                            new_center_line = max(bar.open, bar.close) if is_double_gap else min(bar.open, bar.close)
                            is_forward_refresh = (new_center_line > old_center_line)
                        else:
                            old_center_line = s.center_upper_rail
                            price_gap = bar.low > new_k0.high
                            body_gap_ma5 = min(bar.open, bar.close) > ma5_confirm
                            is_double_gap = price_gap and body_gap_ma5
                            new_center_line = min(bar.open, bar.close) if is_double_gap else max(bar.open, bar.close)
                            is_forward_refresh = (new_center_line < old_center_line)

                        if is_forward_refresh:
                            # 【抢占成功，浴火重生】：老病房废黜，用新K0和新中枢线原地重建！
                            s.current_k0 = new_k0
                            # 注意：s.center_direction = current_dir 在 _enter_forming_state 里会重置
                            self._enter_forming_state(current_dir, s.current_k0, bar, k_index)
                            return

    def rollback(self):
        """回滚所有中枢状态（线段确立后 或 State 2 自然关闭后调用）"""
        s = self.s
        s.center_state          = 0
        s.current_k0            = None
        s.center_line_k         = None
        s.center_line_k_index   = -1
        s.center_direction      = None
        s.center_upper_rail     = 0.0
        s.center_lower_rail     = 0.0
        s.center_start_dt       = None
        s.center_end_dt         = None
        s.center_end_k_index    = -1
        s.center_is_double_gap  = False

    # =========================================================================
    # 私有方法
    # =========================================================================

    def _get_macro_anchor(self) -> tuple:
        """
        获取当前宏观走势的绝对锚点与方向（降维同步法核心）
        返回: (锚点 K 线的绝对索引, 物理延伸方向)
        """
        s = self.s

        # 1. 最高优先级：当前的候选极值（candidate_tk）
        # 这是最贴近当下物理盘面的前沿阵地。
        # 候选顶(G)意味着当前物理走势正在向下；候选底(D)意味着向上。
        if s.candidate_tk:
            # 修复致命反向 Bug：如果正在酝酿顶（Mark.G），说明当前线段仍处于上升状态（Direction.Up）
            direction = Direction.Up if s.candidate_tk.mark == Mark.G else Direction.Down
            return s.candidate_tk.k_index, direction

        # 2. 次优先级：上一个确立的极值（turning_ks[-1]）
        if s.turning_ks:
            direction = Direction.Up if s.turning_ks[-1].mark == Mark.D else Direction.Down
            return s.turning_ks[-1].k_index, direction

        # 3. 混沌期：无极值，保持静默，不盲目建中枢
        return -1, None

    def _enter_forming_state(self, direction: Direction, k0: RawBar,
                              confirm_k: RawBar, cf_index: int):
        """步骤一：确认K出现时，立刻向左定初始结界

        中枢线K（confirm_k）决定一轨（center_line）：
          - 双跳空：取离 MA5 最近的实体端
          - 非双跳空：取反突破均线后的实体端

        另一轨：从 turning_ks[-1] 到 confirm_k 之间，向左找最早的
        连续 2K 有价格重叠、且重叠区位于 center_line 正确侧 的位置。

        上涨线段：center_line = lower_rail，另一轨 = upper_rail
        下跌线段：center_line = upper_rail，另一轨 = lower_rail
        """
        s = self.s
        ma5_confirm = confirm_k.cache.get('ma5', 0)

        # --- 中枢线取值（双跳空 / 非双跳空）---
        if direction == Direction.Up:
            price_gap    = confirm_k.high < k0.low                              # confirm_k 完全在 k0 下方
            body_gap_ma5 = max(confirm_k.open, confirm_k.close) < ma5_confirm   # 实体完全在 MA5 下方
        else:
            price_gap    = confirm_k.low > k0.high                              # confirm_k 完全在 k0 上方
            body_gap_ma5 = min(confirm_k.open, confirm_k.close) > ma5_confirm   # 实体完全在 MA5 上方

        is_double_gap = price_gap and body_gap_ma5

        if direction == Direction.Up:
            center_line = (max(confirm_k.open, confirm_k.close) if is_double_gap
                           else min(confirm_k.open, confirm_k.close))
        else:
            center_line = (min(confirm_k.open, confirm_k.close) if is_double_gap
                           else max(confirm_k.open, confirm_k.close))

        # (唯一性判定移交给了最终 finalize 方法里的终极隔离判断)

        # --- 向左找最左连续 2K 重叠，确定初始另一轨 ---
        # 基础搜索起点：整条线段的起点
        search_start = s.turning_ks[-1].k_index if s.turning_ks else 0

        # 【新增的叹息之墙】：绝对不允许穿透上一个中枢的破窗点！（跨线段依然有效）
        if s.last_center_end_idx != -1:
            search_start = max(search_start, s.last_center_end_idx)

        upper_rail = center_line  # 兜底：退化为单线
        lower_rail = center_line

        # 【新增】：默认兜底发生时间为确认K的时间
        inception_dt = confirm_k.dt

        for i in range(search_start, cf_index - 1):
            k1 = s.bars_raw[i]
            k2 = s.bars_raw[i + 1]
            overlap_high = min(k1.high, k2.high)
            overlap_low  = max(k1.low,  k2.low)

            if overlap_low <= overlap_high:   # 存在真实重叠
                if direction == Direction.Up and overlap_high >= center_line:
                    upper_rail = overlap_high  # 上涨：另一轨 = 最左2K重叠上沿
                    lower_rail = center_line
                    # 【新增】：找到了真实阵地，把起始时间往前推到第一根重叠 K 线
                    inception_dt = k1.dt
                    break
                elif direction == Direction.Down and overlap_low <= center_line:
                    lower_rail = overlap_low   # 下跌：另一轨 = 最左2K重叠下沿
                    upper_rail = center_line
                    # 【新增】：找到了真实阵地，把起始时间往前推到第一根重叠 K 线
                    inception_dt = k1.dt
                    break

        # --- 初始化观测病房状态 ---
        s.center_line_k         = confirm_k
        s.center_line_k_index   = cf_index
        s.center_direction      = direction
        s.center_upper_rail     = upper_rail
        s.center_lower_rail     = lower_rail

        # 【修改】：使用我们刚刚锚定的物理发源时间
        s.center_start_dt       = inception_dt
        s.center_end_dt         = confirm_k.dt
        s.center_is_double_gap  = is_double_gap  # 保存双跳空标记，供式一使用

    def _finalize_and_mount_center(self):
        """步骤四：固化中枢，执行排他挂载逻辑

        验证门（必须满足其一）：
          - 起手三式满足其一，且包含黑K保障
          - 且满足唯一性、排他延伸法则
        """
        s = self.s
        if s.center_direction is None:
            return

        # ====== 验证门（起手三式 + 黑K保障） ======
        if not self._check_center_formation():
            return  # 未达到任何中枢确立条件（或无黑K），放弃

        # ====== 中枢定性（肉眼 vs 非肉眼） ======
        # 根据“正向一笔”形成前的 MA5 反向重叠情况决定类型
        window_bars = s.bars_raw[s.center_line_k_index : s.center_end_k_index + 1]
        type_str = self._classify_center_type(s.center_direction, 0, window_bars)

        # 确定中枢线（主轨）
        if s.center_direction == Direction.Up:
            center_line = s.center_lower_rail
        else:
            center_line = s.center_upper_rail

        # 判定方式（用于图表标注）
        if self._check_fan_zheng_liang_chuan():
            method = "反正两穿"
        elif self._check_5k_overlap():
            method = "5K重叠"
        elif self._check_san_bi():
            method = "三笔"
        else:
            method = "未知"

        center = MooreCenter(
            type_name=type_str,
            direction=s.center_direction,
            anchor_k0=s.current_k0,
            confirm_k=s.center_line_k,
            method=method,
            center_line=center_line,
            upper_rail=s.center_upper_rail,
            lower_rail=s.center_lower_rail,
            start_dt=s.center_start_dt,
            end_dt=s.center_end_dt,
        )

        # =====================================================================
        # --- 空间审判与三大延伸法则（终极一刀切版） ---
        # =====================================================================
        if s.potential_centers:
            # 只比对当前线段生成的中枢（也就是当前线段还未固化前，挂载在 potential_centers 里的中枢）
            last_c = next((c for c in reversed(s.potential_centers) if c.direction == s.center_direction), None)
            if last_c:
                
                # 1. 终极空间分离判定（完美涵盖不重叠与中枢线新高/新低）
                # 规则：上升线段的新下轨必须高于旧上轨；下降线段的新上轨必须低于旧下轨。
                if s.center_direction == Direction.Up:
                    is_separated = center.lower_rail > last_c.upper_rail
                else:
                    is_separated = center.upper_rail < last_c.lower_rail

                # 2. 审判执行
                if not is_separated:
                    # 【未实现完全分离，进入三大延伸法则的残酷绞肉机】
                    if not last_c.is_visible and center.is_visible:
                        # 【法则三：特殊延伸法则】肉眼霸权，非肉让位
                        s.potential_centers.remove(last_c)
                        # (抹除老中枢后，代码会继续往下执行 append 挂载新中枢)
                        
                    elif center.is_visible and last_c.is_visible:
                        # 【法则一：标准延伸法则】双肉眼重叠扩张
                        last_c.end_dt = center.end_dt  # 吸收时间
                        last_c.upper_rail = max(last_c.upper_rail, center.upper_rail)
                        last_c.lower_rail = min(last_c.lower_rail, center.lower_rail)
                        
                        s.last_center_end_idx = s.center_end_k_index
                        return # 新中枢彻底消亡
                        
                    else:
                        # 【法则二：新中枢被吞并】新中枢是非肉，且未完全分离
                        # 【Bug 1 修复】：老中枢吃掉新中枢的生存时间！
                        last_c.end_dt = center.end_dt  
                        
                        s.last_center_end_idx = s.center_end_k_index
                        return # 新中枢彻底消亡
                else:
                    # 【完全分离】：是一片全新的干净领地！直接放行挂载！
                    pass

        # 挂载当下正式形成的新中枢
        s.potential_centers.append(center)
        # 无论挂载还是吞并，最后的水位线必须同步，作为下一次 K0 搜索的绝对左边界
        s.last_center_end_idx = s.center_end_k_index

    def _check_center_formation(self) -> bool:
        """中枢合法性最终校验 = (起手三式) AND (包含黑K)"""
        s = self.s
        # 1. 形状校验
        has_pattern = (
            self._check_fan_zheng_liang_chuan()
            or self._check_5k_overlap()
            or self._check_san_bi()
        )
        if not has_pattern:
            return False
            
        # 2. 灵魂校验：黑K (保障线段级别)
        window_bars = s.bars_raw[s.center_line_k_index : s.center_end_k_index + 1]
        has_black_k = self._check_black_k(s.center_direction, 0, window_bars)
        
        return has_black_k

    def _check_black_k(self, direction: Direction, confirm_k_idx: int, window_bars: list) -> bool:
        """
        黑K质检器：在确认K之后，寻找至少一根非向正跳空 MA5 的 K 线（且不能是顶底极值）
        """
        s = self.s
        if len(window_bars) < 2:
            return False
            
        for i in range(1, len(window_bars)):
            bar = window_bars[i]
            ma5 = bar.cache.get('ma5', 0)
            
            # 排除已确立的顶底极值K（顶底不可作为黑K）
            is_extreme = any(tk.dt == bar.dt for tk in s.turning_ks)
            if is_extreme:
                continue

            if direction == Direction.Up:
                # 上涨中的黑K：不再悬空，最低价踩到或跌破 MA5
                if bar.low <= ma5:
                    return True
            else:
                # 下跌中的黑K：不再悬空，最高价摸到或突破 MA5
                if bar.high >= ma5:
                    return True
                    
        return False

    def _classify_center_type(self, direction: Direction, confirm_k_idx: int, window_bars: list) -> str:
        """
        中枢定性（成立后调用）：寻找正向一笔，并检测其间的 MA5 反向重叠 (VISIBLE vs INVISIBLE)
        """
        if len(window_bars) <= confirm_k_idx + 1:
            return "INVISIBLE"

        # 1. 寻找以确认K为起点的“正向一笔”刚好形成的时刻 (实体递进链)
        fwd_end_idx = confirm_k_idx
        last_k = window_bars[confirm_k_idx]
        
        for i in range(confirm_k_idx + 1, len(window_bars)):
            curr_k = window_bars[i]
            if direction == Direction.Up:
                # 上升实体递进：不创新低且创新高
                if curr_k.high > last_k.high and curr_k.low >= last_k.low:
                    fwd_end_idx = i
                    last_k = curr_k
                else: break # 递进中断
            else:
                # 下降实体递进
                if curr_k.low < last_k.low and curr_k.high <= last_k.high:
                    fwd_end_idx = i
                    last_k = curr_k
                else: break

        # 2. 在 [确认K, 正向一笔形成K] 区间内，检查 MA5 是否反向重叠/走平
        for i in range(confirm_k_idx + 1, fwd_end_idx + 1):
            curr_ma5 = window_bars[i].cache.get('ma5', 0)
            prev_ma5 = window_bars[i-1].cache.get('ma5', 0)
            
            if direction == Direction.Up:
                if curr_ma5 <= prev_ma5: return "VISIBLE"
            else:
                if curr_ma5 >= prev_ma5: return "VISIBLE"
                    
        return "INVISIBLE"

    def _check_hidden_center(self) -> bool:
        """(过时) 旧的隐式中枢校验入口，现已被 _check_center_formation 涵盖"""
        return self._check_fan_zheng_liang_chuan() or self._check_5k_overlap() or self._check_san_bi()

    def _check_fan_zheng_liang_chuan(self) -> bool:
        """式一：反正两穿（2C）

        双跳空旁路：若 confirm_k 与 k0 已属双跳空，无需等 K2，直接成立。
        常规路：在结界窗口内找到 K1（反穿中枢线），再找到 K2（正穿回中枢线且有新进展）。
        """
        s = self.s

        # 双跳空下自动成立
        if s.center_is_double_gap:
            return True

        if s.center_line_k_index < 0 or s.center_end_k_index < s.center_line_k_index:
            return False

        # 窗口内全部 K 线（从 confirm_k 开始）
        window_bars = s.bars_raw[s.center_line_k_index : s.center_end_k_index + 1]

        # 中枢线：对应轨
        if s.center_direction == Direction.Up:
            center_line = s.center_lower_rail   # 上涨线段：中枢线 = 下轨
        else:
            center_line = s.center_upper_rail   # 下跌线段：中枢线 = 上轨

        return self._check_2c_pattern(s.center_direction, center_line, window_bars)

    def _check_2c_pattern(self, direction: Direction, center_line: float,
                           bars: list) -> bool:
        """反正两穿核心判断（与线段方向无关，通过 direction 区分）

        K1（反穿）：实体穿越中枢线 —— Up: min(o,c) < cl； Down: max(o,c) > cl
        K2（正穿）：在 K1 右侧寻找满足不下 + 有上 + 穿透中枢线的 K
        """
        if len(bars) < 2:
            return False

        k1_found_idx = -1

        # 寻找 K1（反穿）
        for i, b in enumerate(bars):
            if direction == Direction.Up:
                if min(b.open, b.close) < center_line:
                    k1_found_idx = i
                    break
            else:
                if max(b.open, b.close) > center_line:
                    k1_found_idx = i
                    break

        if k1_found_idx == -1 or k1_found_idx == len(bars) - 1:
            return False  # 未找到 K1，或 K1 已是最后一根（K2 还没来）

        # 寻找 K2（正穿，在 K1 右侧）
        for i in range(k1_found_idx + 1, len(bars)):
            curr_k = bars[i]
            prev_k = bars[i - 1]

            curr_body_high = max(curr_k.open, curr_k.close)
            curr_body_low  = min(curr_k.open, curr_k.close)
            prev_body_high = max(prev_k.open, prev_k.close)
            prev_body_low  = min(prev_k.open, prev_k.close)

            if direction == Direction.Up:
                # 实体新高（或者属于强势的向上包含突破） + 穿透中枢线
                if (curr_body_high >  prev_body_high and
                    curr_body_high >  center_line):
                    return True
            else:
                # 实体新低（或者属于强势的向下包含突破） + 穿透中枢线
                if (curr_body_low  <  prev_body_low  and
                    curr_body_low  <  center_line):
                    return True

        return False

    def _check_5k_overlap(self) -> bool:
        """式二：5K重叠

        搜索范围：从线段起点 到 确认K（center_line_k_index），绝不能包含后续的右推窗口！
        bars[-1] 在 _check_5k_pattern 中充当"4K跳空特权"的确认K角色，
        若错误地传入脱轨K，语义完全偏离。
        """
        s = self.s
        if s.center_line_k_index < 0:
            return False

        search_start = s.turning_ks[-1].k_index if s.turning_ks else 0
        # 【新增】：同步防穿透书签
        if s.last_center_end_idx != -1:
            search_start = max(search_start, s.last_center_end_idx)

        # 严格截断至确认K（不含后续右推窗口）
        window_bars  = s.bars_raw[search_start : s.center_line_k_index + 1]

        # 【新增】：提取当前中枢线，用于重叠区位置校验
        if s.center_direction == Direction.Up:
            center_line = s.center_lower_rail
        else:
            center_line = s.center_upper_rail

        return self._check_5k_pattern(s.center_direction, window_bars, center_line)


    def _check_5k_pattern(self, direction: Direction, bars: list, center_line: float) -> bool:
        """5K重叠核心判断

        首K（K_a）：bars 中第一个比前邻更高（Up）或更低（Down）的局部极值点
        破坏K（K_b）：K_a 右侧第一根打破该极值趋势的 K
        重叠区：K_a 与 K_b 的价格区间交集
        成立条件：
          - 重叠区内 K 线数 >= 5
          - 或恰好 4 根，但确认K（bars[-1]）跳空脱离重叠区（4K 跳空特权）
        """
        # 至少需要 4 根才有跳空特权；不足 4 根直接放弃
        if len(bars) < 4:
            return False

        for i in range(1, len(bars) - 1):
            k_a = bars[i]

            # 1. 首K 必须是微观极值点（比前邻更极端）
            if direction == Direction.Up and k_a.high <= bars[i - 1].high:
                continue
            if direction == Direction.Down and k_a.low >= bars[i - 1].low:
                continue

            # 2. 往右找第一根"破坏K"（K_b）
            k_b = None
            k_b_idx = -1
            for j in range(i + 1, len(bars)):
                if direction == Direction.Up:
                    if bars[j].high < k_a.high:    # 递增趋势被打破
                        k_b, k_b_idx = bars[j], j
                        break
                else:
                    if bars[j].low > k_a.low:      # 递减趋势被打破
                        k_b, k_b_idx = bars[j], j
                        break

            if not k_b:
                continue  # 趋势未断，换下一个首K候选

            # 3. 计算 K_a 与 K_b 的重叠区
            overlap_high = min(k_a.high, k_b.high)
            overlap_low  = max(k_a.low,  k_b.low)

            if overlap_low > overlap_high:
                continue  # 无真实重叠

            # 【新增：核心校验】重叠区必须与中枢线价格逻辑契合，防止误触非本中枢的价格基底
            if direction == Direction.Up and overlap_high < center_line:
                continue
            if direction == Direction.Down and overlap_low > center_line:
                continue

            # 4. 清点 [首K, 窗口末端] 内摸到重叠区的 K 线数
            count = sum(
                1 for k in bars[i:]
                if k.high >= overlap_low and k.low <= overlap_high
            )

            # 5. 最终判定
            if count >= 5:
                return True
            elif count == 4:
                # 4K 跳空特权：确认K（bars[-1]）完全跳空脱离重叠区
                confirm_k = bars[-1]
                if direction == Direction.Up   and confirm_k.low  > overlap_high:
                    return True
                if direction == Direction.Down and confirm_k.high < overlap_low:
                    return True

        return False


    def _check_san_bi(self) -> bool:
        """式三：三笔纯势（正-反-正 N字结构）

        搜索范围与式二相同：从线段起点到窗口末端。
        confirm_k_idx: 确认K 在 bars 列表中的相对偏移索引。
        阵眼：确认K 必须落在"反向一笔"的时间辐射内。
        """
        s = self.s
        if s.center_line_k_index < 0 or s.center_end_k_index < 0:
            return False

        search_start    = s.turning_ks[-1].k_index if s.turning_ks else 0
        # 【新增】：同步防穿透书签
        if s.last_center_end_idx != -1:
            search_start = max(search_start, s.last_center_end_idx)
        window_bars     = s.bars_raw[search_start : s.center_end_k_index + 1]
        # 确认K 在 window_bars 中的相对索引
        confirm_k_idx   = s.center_line_k_index - search_start

        if confirm_k_idx < 0 or confirm_k_idx >= len(window_bars):
            return False

        return self._check_3_strokes_pattern(s.center_direction, confirm_k_idx, window_bars)

    def _check_3_strokes_pattern(self, direction: Direction,
                                  confirm_k_idx: int, bars: list) -> bool:
        """三笔纯势核心判断（正-反-正 N字结构）

        第一步：定位原趋势的绝对极值 K（从起点到确认K 之间）
        第二步：从极值K 出发，扫描"反向一笔"（3根+递进，且确认K 在其时间辐射内）
        第三步：扫描"正向一笔"（3根+递进），完成 N 字闭环
        每一笔的 K 线不必连续，但至少有 3 根 K 线最值递增/递减（不同价），
        满足"不下才有上，不上才有下"原则。
        """
        if len(bars) < 5:
            return False

        # --- 第一步：定位原趋势绝对极值 ---
        ext_idx = 0
        if direction == Direction.Up:
            ext_val = bars[0].high
            for i in range(1, confirm_k_idx + 1):
                if bars[i].high > ext_val:
                    ext_val, ext_idx = bars[i].high, i
        else:
            ext_val = bars[0].low
            for i in range(1, confirm_k_idx + 1):
                if bars[i].low < ext_val:
                    ext_val, ext_idx = bars[i].low, i

        # --- 第二步：扫描反向一笔 ---
        rev_count   = 1
        last_k      = bars[ext_idx]
        rev_end_idx = ext_idx

        for i in range(ext_idx + 1, len(bars)):
            curr_k = bars[i]
            if direction == Direction.Up:
                # 回调：不上才有下 (Lower High & Lower Low)
                if curr_k.high < last_k.high and curr_k.low < last_k.low:
                    rev_count += 1
                    last_k, rev_end_idx = curr_k, i
                # 反弹终结（Higher High & Higher Low）→ 跳出
                elif curr_k.high > last_k.high and curr_k.low > last_k.low:
                    break
            else:
                # 反弹：不下才有上 (Higher High & Higher Low)
                if curr_k.high > last_k.high and curr_k.low > last_k.low:
                    rev_count += 1
                    last_k, rev_end_idx = curr_k, i
                # 回调终结 → 跳出
                elif curr_k.high < last_k.high and curr_k.low < last_k.low:
                    break

        # 阵眼 1：反向一笔 >= 3 根
        # 阵眼 2：确认K 必须落在反向一笔的时间辐射内
        if rev_count < 3 or confirm_k_idx > rev_end_idx:
            return False

        # --- 第三步：扫描正向一笔 ---
        fwd_count = 1
        last_k    = bars[rev_end_idx]

        for i in range(rev_end_idx + 1, len(bars)):
            curr_k = bars[i]
            if direction == Direction.Up:
                # 趋势重启：不下才有上
                if curr_k.high > last_k.high and curr_k.low > last_k.low:
                    fwd_count += 1
                    last_k = curr_k
                elif curr_k.high < last_k.high and curr_k.low < last_k.low:
                    break
            else:
                # 趋势重启：不上才有下
                if curr_k.high < last_k.high and curr_k.low < last_k.low:
                    fwd_count += 1
                    last_k = curr_k
                elif curr_k.high > last_k.high and curr_k.low > last_k.low:
                    break

        # 正向一笔 >= 3 根 → 正-反-正 N 字结构彻底闭环
        return fwd_count >= 3
