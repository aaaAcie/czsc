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
from loguru import logger
from typing import Optional
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

    def update(self, bar: RawBar, k_index: int, force_direction: Optional[Direction] = None, 
               force_anchor_idx: Optional[int] = None, force_trigger_idx: Optional[int] = None):
        """逐 bar 推进中枢状态机"""
        s = self.s

        # 获取宏观的绝对锚点和方向 (仅用于寻找起始点)
        if force_anchor_idx is not None:
            ext_idx = force_anchor_idx
            trig_idx = force_trigger_idx if force_trigger_idx is not None else force_anchor_idx
            macro_dir = force_direction
        else:
            ext_idx, trig_idx, macro_dir = self._get_macro_anchor()

        ma5 = bar.cache.get('ma5', 0)

        # =====================================================================
        # =====================================================================
        # 1. 物理拦截与对合保护
        # =====================================================================
        # 【物理范围拦截】：法定寻 K0 范围从转折确认点（trig_idx）开始
        if k_index < trig_idx:
            return

        # 【同步确认保护】：如果正在孵化的中枢其锚点被更高质量的极值跨越
        if s.center_state > 0 and s.center_anchor_idx < ext_idx:
            # 【核心策略】：在清场前，强制进行最后一次名分与质检探测
            # 许多中枢在极值出现的最后一根 K 线才满足 5K 重叠或黑K通过
            self._check_center_formation()

            # 【提前结业法则】：若已有名分且过质检，抢在清场前正式固化入库
            if s.center_state >= 2 and s.center_method_found is not None and s.center_black_k_pass:
                logger.debug(f"  [Early Graduation] Force finalize center {s.center_start_dt} due to anchor shift {s.center_anchor_idx} < {ext_idx}")
                self._finalize_and_mount_center()
                
            self.rollback()

        # 2. 确定当前参考方向（物理隔离法则）
        # 如果已经有了中枢（State 1 或 2），必须对该中枢的方向绝对忠诚！
        # 只有在 State 0（空仓寻猎）时，才去向宏观引擎请示大方向。
        # =====================================================================
        if s.center_state > 0:
            current_dir = s.center_direction
        else:
            current_dir = force_direction if force_direction is not None else macro_dir
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
            # 【核心修复】：物理隔离扫描仅对“同一个宏观结构内”的老中枢有效
            # 绝对不允许跨越物理极值点（ext_idx）去产生历史重叠感应
            prev_c = next((c for c in reversed(s.potential_centers) 
                           if c.direction == current_dir 
                           and c.end_k_index >= ext_idx  # 必须在本段起点之后
                          ), None)
            if prev_c and not (bar.low > prev_c.upper_rail or bar.high < prev_c.lower_rail):
                has_overlap = True

        # 维护备胎 K0（必须是纯洁且不重叠的）
        if is_pure and not has_overlap:
            s.latest_k0 = bar

        # =====================================================================
        # State 0：找 K0（必须在法定视界内！）
        # =====================================================================
        if s.center_state == 0:
            # 【物理特权】：只有确认转折的那根 K 线（trig_idx）具备法定豁免权
            is_anchor = (k_index == trig_idx)
            
            if (is_pure or is_anchor) and not has_overlap:
                s.current_k0 = bar
                s.center_direction = current_dir
                s.center_anchor_idx = ext_idx  # 轨道搜索回溯仍需看齐物理极值
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
            direction = current_dir

            # 1. 基础物理延伸判定：价格是否仍在结界轨道内？
            in_rails = (bar.high >= s.center_lower_rail and bar.low <= s.center_upper_rail)

            if not in_rails:
                # --- 脱轨处理流程 ---
                s.escape_bars.append((bar, k_index))
                
                # 【右边界定格原则】：如果是第一根脱轨 K 线，把它作为潜在的固化右边界
                if len(s.escape_bars) == 1:
                    s.center_end_dt = bar.dt
                    s.center_end_k_index = k_index
                    
                # 满足连续三根脱轨，彻底宣告病房关闭并固化
                if len(s.escape_bars) >= 3:
                    self._finalize_and_mount_center()
                    self.rollback()
                    
                    # 固化后的无缝衔接判定
                    if is_pure and not has_overlap:
                        s.current_k0 = bar
                        s.center_direction = current_dir
                        s.center_anchor_idx = ext_idx
                        s.center_state = 1
                    return
            else:
                # --- 轨道内延伸流程 ---
                if s.escape_bars:
                    s.escape_bars.clear()  # 假突破/假摔，清空缓冲池
                    
                # 【动态右移】：中枢跟随价格在轨道内的每一次探索而延伸
                s.center_end_dt      = bar.dt
                s.center_end_k_index = k_index

            # 2. 逐 K 确权判定
            self._check_center_formation()
            
            # 3. 物理防线（锁定锁）：只有当中枢彻底形成（名分且黑K通过）后，才禁止刷新 K0
            is_formed = (s.center_method_found is not None and s.center_black_k_pass)
            
            # 4. 【内部抢占进化】：仅在名分未立或黑K未过（尚未真正成型）时，备选 K0 才具备篡位资格。
            if not is_formed and not in_rails:
                if s.latest_k0 is not None and s.latest_k0.id > s.current_k0.id:
                    new_k0 = s.latest_k0
                    ma5_confirm = bar.cache.get('ma5', 0)
                    
                    if direction == Direction.Up:
                        old_center_line = s.center_lower_rail
                        price_gap = bar.high < new_k0.low
                        body_gap_ma5 = max(bar.open, bar.close) < ma5_confirm
                        is_dg = price_gap and body_gap_ma5
                        new_cl = max(bar.open, bar.close) if is_dg else min(bar.open, bar.close)
                        is_forward = (new_cl > old_center_line)
                    else:
                        old_center_line = s.center_upper_rail
                        price_gap = bar.low > new_k0.high
                        body_gap_ma5 = min(bar.open, bar.close) > ma5_confirm
                        is_dg = price_gap and body_gap_ma5
                        new_cl = min(bar.open, bar.close) if is_dg else max(bar.open, bar.close)
                        is_forward = (new_cl < old_center_line)

                    if is_forward:
                        s.current_k0 = new_k0
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
        s.center_start_k_index  = -1
        s.center_end_dt         = None
        s.center_end_k_index    = -1
        s.center_is_double_gap  = False
        s.center_method_found   = None
        s.center_black_k_pass   = False
        s.latest_k0             = None
        s.escape_bars.clear()

    def seal_on_boundary(self):
        """物理截断：当线段到达转折极值点时，强制结算当前观测病房。"""
        s = self.s
        if s.center_state == 2:
            # 即使还没脱轨，到了分水岭也要强制审计并固化
            self._finalize_and_mount_center()
        
        # 无论是否固化成功，必须归位，新线段开启新篇章
        self.rollback()

    def get_active_center(self) -> Optional[MooreCenter]:
        """获取当前正在生长的（且已名分+黑K通过）活跃中枢作为虚拟对象。"""
        s = self.s
        if s.center_state < 2:
            return None
        
        # 实时探测一次（确保当前 bar 的名分/黑K状态是最新的）
        self._check_center_formation()
        
        if s.center_method_found is None or not s.center_black_k_pass:
            return None
            
        return self._create_center_object()

    def _create_center_object(self) -> MooreCenter:
        """从当前 state 构建一个 MooreCenter 对象（不涉及挂载与合并）"""
        s = self.s
        
        # 边界校正：固化时需保证右边界不越界。活跃预览时则根据当前 bar 对齐。
        end_idx = s.center_end_k_index
        end_dt = s.center_end_dt
        if end_idx > (len(s.bars_raw) - 1):
            end_idx = len(s.bars_raw) - 1
            end_dt = s.bars_raw[-1].dt

        # 左边界对合（基于 5K 真实起点回溯）
        final_start_dt = s.center_start_dt
        final_start_idx = s.center_start_k_index
        
        if s.center_method_found == "5K重叠":
            ok, f5k_relative_idx = self._check_5k_overlap_with_idx()
            if ok and f5k_relative_idx != -1:
                search_start_global = max(0, s.center_anchor_idx)
                if s.last_center_end_idx != -1:
                    search_start_global = max(search_start_global, s.last_center_end_idx)
                
                absolute_f5k_idx = search_start_global + f5k_relative_idx
                if absolute_f5k_idx < final_start_idx:
                    final_start_idx = absolute_f5k_idx
                    final_start_dt = s.bars_raw[final_start_idx].dt

        # 中枢定性（肉眼 vs 非肉眼）
        window_bars = s.bars_raw[s.center_line_k_index : end_idx + 1]
        type_str = self._classify_center_type(s.center_direction, 0, window_bars)

        if s.center_direction == Direction.Up:
            center_line = s.center_lower_rail
        else:
            center_line = s.center_upper_rail

        return MooreCenter(
            type_name=type_str,
            direction=s.center_direction,
            anchor_k0=s.current_k0,
            confirm_k=s.center_line_k,
            method=s.center_method_found,
            center_line=center_line,
            upper_rail=s.center_upper_rail,
            lower_rail=s.center_lower_rail,
            start_dt=final_start_dt,
            end_dt=end_dt,
            start_k_index=final_start_idx,
            end_k_index=end_idx,
        )

    # =========================================================================
    # 私有方法
    # =========================================================================

    def _get_macro_anchor(self) -> tuple:
        """
        获取当前宏观走势的绝对锚点与方向（降维同步法核心）
        返回: (物理极值索引, 转折确认点索引, 物理延伸方向)
        """
        s = self.s

        # 1. 最高优先级：当前的候选极值（candidate_tk）
        if s.candidate_tk:
            # 候选顶(G)意味着当前已确认转向向下(Down)
            direction = Direction.Down if s.candidate_tk.mark == Mark.G else Direction.Up
            return s.candidate_tk.k_index, s.candidate_tk.trigger_k_index, direction

        # 2. 次优先级：上一个确立的极值（turning_ks[-1]）
        if s.turning_ks:
            tk = s.turning_ks[-1]
            direction = Direction.Up if tk.mark == Mark.D else Direction.Down
            # 优先使用转折确认点索引，若无则回退到极值点索引
            trig_idx = tk.trigger_k_index if tk.trigger_k_index is not None else tk.k_index
            return tk.k_index, trig_idx, direction

        return -1, -1, None

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
        # 【核心修复：物理极值防火墙】
        # 绝对不允许跨越当前线段的物理发源地（山顶/山谷）去偷 K 线！
        search_start = max(0, s.center_anchor_idx)

        # 【叹息之墙】：同时也不能穿透上一个同向中枢的破窗点
        if s.last_center_end_idx != -1:
            search_start = max(search_start, s.last_center_end_idx)

        upper_rail = center_line  # 兜底：退化为单线
        lower_rail = center_line

        # 【新增】：默认兜底发生时间为确认K的时间
        inception_dt = confirm_k.dt
        inception_idx = cf_index

        # 修正：range 应包含到 cf_index，以便检查 (cf_index-1, cf_index) 这组重叠
        for i in range(search_start, cf_index):
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
                    inception_idx = i
                    break
                elif direction == Direction.Down and overlap_low <= center_line:
                    lower_rail = overlap_low   # 下跌：另一轨 = 最左2K重叠下沿
                    upper_rail = center_line
                    # 【新增】：找到了真实阵地，把起始时间往前推到第一根重叠 K 线
                    inception_dt = k1.dt
                    inception_idx = i
                    break

        # --- 初始化观测病房状态 ---
        s.center_line_k         = confirm_k
        s.center_line_k_index   = cf_index
        s.center_direction      = direction
        s.center_upper_rail     = upper_rail
        s.center_lower_rail     = lower_rail

        # 【修改】：使用我们刚刚锚定的物理发源时间
        s.center_start_dt       = inception_dt
        s.center_start_k_index  = inception_idx
        s.center_end_dt         = confirm_k.dt
        s.center_is_double_gap  = is_double_gap  # 保存双跳空标记，供式一使用

        # 【核心确权】：入场即名分！
        # 在中枢确认诞生的这一刻，立刻回溯校验，看看当前是否已满足起手三式任何一种。
        # 先临时设置结束索引为确认K，供校验引擎内部使用。
        s.center_end_k_index = cf_index
        self._check_center_formation()

    def _finalize_and_mount_center(self):
        """固化病房，进行排他性比对后，挂载到 potential_centers 暂存区"""
        s = self.s
        
        # 1. 终极确权审计
        center = self.get_active_center()
        if not center:
            return  # 未能成型，放弃

        # =====================================================================
        # --- 空间审判与三大延伸法则（终极一刀切版） ---
        # =====================================================================
        if s.potential_centers:
            # 只比对当前线段生成的中枢（不得跨越物理极值点合并）
            last_c = next((c for c in reversed(s.potential_centers) 
                           if c.direction == s.center_direction 
                           and not getattr(c, 'is_ghost', False)
                           and c.end_k_index >= s.center_anchor_idx # 核心约束：上一个中枢必须在本段起点的右侧
                          ), None)
            if last_c:
                
                # 1. 终极空间分离判定（完美涵盖不重叠与中枢线新高/新低）
                # 规则：上升线段的新下轨必须高于旧上轨；下降线段的新上轨必须低于旧下轨。
                if s.center_direction == Direction.Up:
                    is_separated = center.lower_rail > last_c.upper_rail
                else:
                    is_separated = center.upper_rail < last_c.lower_rail

                # 2. 审判执行
                if not is_separated:
                    # 【未实现完全分离，进入核心博弈与吞并绞肉机】
                    if center.is_visible and not last_c.is_visible:
                        # 【法则：新皇登基 (肉废灵)】
                        # 仅当新中枢是肉眼，且老中枢是隐位时，立刻废黜老中枢
                        s.potential_centers.remove(last_c)
                        # 后续代码将继续 append(center) 挂载新皇
                        
                    elif center.is_visible and last_c.is_visible:
                        # 【法则：标准延伸 (肉加肉)】
                        # 肉眼不废肉眼。两形重叠时，原中枢向右延伸并吸收时间与轨道并集
                        last_c.end_dt = center.end_dt
                        last_c.upper_rail = max(last_c.upper_rail, center.upper_rail)
                        last_c.lower_rail = min(last_c.lower_rail, center.lower_rail)
                        s.last_center_end_idx = s.center_end_k_index
                        return
                        
                    else:
                        # 【法则：吞并延伸 (其余情况)】
                        # 无论是“肉吞灵”还是“灵吞灵”，老中枢保持主权，仅吸收时间
                        last_c.end_dt = center.end_dt
                        s.last_center_end_idx = s.center_end_k_index
                        return
                else:
                    # 【完全分离】：绝对独立的主权领地，直接挂载
                    pass

        # 挂载当下正式形成的新中枢
        s.potential_centers.append(center)
        # 无论挂载还是吞并，最后的水位线必须同步，作为下一次 K0 搜索的绝对左边界
        s.last_center_end_idx = s.center_end_k_index

    def _check_center_formation(self):
        """中枢合法性最终校验。名分(Style)与黑K质检解耦判定，直接更新 state 变量。"""
        s = self.s
        
        # 【核心修复：物理极值防火墙】
        # 绝对不允许跨越当前线段的物理发源地去探测名分
        search_start = max(0, s.center_anchor_idx)
        
        # 同步防穿透书签
        if s.last_center_end_idx != -1:
            search_start = max(search_start, s.last_center_end_idx)

        # 1. 探测名分（如果还没有）
        if s.center_method_found is None:
            ok_5k, _ = self._check_5k_overlap_with_idx()
            if ok_5k:
                s.center_method_found = "5K重叠"
            else:
                if self._check_fan_zheng_liang_chuan():
                    s.center_method_found = "反正两穿"
                elif self._check_san_bi():
                    s.center_method_found = "三笔"

        # 2. 探测黑K（如果有了名分但黑K还没过）
        if s.center_method_found is not None and not s.center_black_k_pass:
            if s.center_method_found == "5K重叠":
                # 5K 视界范围宽：摸到确认K即可
                window_bars = s.bars_raw[search_start : s.center_end_k_index + 1]
                if self._check_black_k(s.center_direction, 0, window_bars):
                    s.center_black_k_pass = True
            else:
                # 此时黑K质检必须严格：从 confirm_k (s.center_line_k) 之后开始
                strict_window = s.bars_raw[s.center_line_k_index : s.center_end_k_index + 1]
                if self._check_black_k(s.center_direction, 0, strict_window):
                    s.center_black_k_pass = True

        return None

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
                    # print(f"  [DEBUG BK] Found black K for Up at {bar.dt} (low {bar.low} <= ma5 {ma5})")
                    return True
            else:
                # 下跌中的黑K：不再悬空，最高价摸到或突破 MA5
                if bar.high >= ma5:
                    # print(f"  [DEBUG BK] Found black K for Down at {bar.dt} (high {bar.high} >= ma5 {ma5})")
                    return True

        # print(f"  [DEBUG BK] NO black K found in window {window_bars[0].dt} to {window_bars[-1].dt}")
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
        ok, _ = self._check_5k_overlap_with_idx()
        return ok

    def _check_5k_overlap_with_idx(self) -> tuple:
        """中枢确立式二：5K 模式校验（返回是否成立及起始 Ka 索引）"""
        s = self.s
        # 【核心修复：物理极值防火墙】
        # 视界必须受限于当前线段锚点，严禁跨线段拼凑
        search_start = max(0, s.center_anchor_idx)
        
        # 同步防穿透书签
        if s.last_center_end_idx != -1:
            search_start = max(search_start, s.last_center_end_idx)
        
        # 确认K在截取窗口中的索引
        confirm_idx = s.center_line_k_index - search_start
        if confirm_idx < 0:
            return False, -1

        window_bars  = s.bars_raw[search_start : s.center_end_k_index + 1]

        # 确定当前中枢线价格用于覆盖校验
        center_line = s.center_lower_rail if s.center_direction == Direction.Up else s.center_upper_rail

        return self._check_5k_pattern(s.center_direction, window_bars, confirm_idx, center_line)

    def _check_5k_pattern(self, direction: Direction, bars: list, confirm_idx: int, center_line: float) -> tuple:
        """
        物理逻辑：寻找是否有 5 根 K 线在同一价格带重叠。
        核心逻辑升级：不再死板遵循线段方向，而是通过双向搜索寻找“最强物理摩擦锚点”。
        """
        if len(bars) < 4:
            return False, -1

        def _scan_with_anchor(is_high_anchor: bool) -> tuple:
            # 1. 寻找候选锚点 Ka
            target_idx = 0
            if is_high_anchor:
                # 找最高点锚点
                max_high = -float('inf')
                for i in range(confirm_idx + 1):
                    if bars[i].high >= max_high:
                        max_high, target_idx = bars[i].high, i
            else:
                # 找最低点锚点
                min_low = float('inf')
                for i in range(confirm_idx + 1):
                    if bars[i].low <= min_low:
                        min_low, target_idx = bars[i].low, i
            
            k_a = bars[target_idx]

            # 2. 寻找破坏K (Kb)
            k_b, kb_idx = None, -1
            for i in range(target_idx + 1, len(bars)):
                if is_high_anchor:
                    if bars[i].high < k_a.high:  # 跌破最高点
                        k_b, kb_idx = bars[i], i
                        break
                else:
                    if bars[i].low > k_a.low:    # 涨破最低点
                        k_b, kb_idx = bars[i], i
                        break
            if not k_b:
                return False, -1, 0

            # 3. 计算重叠价格带
            ov_high = min(k_a.high, k_b.high)
            ov_low  = max(k_a.low,  k_b.low)
            if ov_low > ov_high:
                return False, -1, 0

            # 4. 统计重叠（回归价格触碰标准）
            ov_indices = [i for i, k in enumerate(bars) if k.high >= ov_low and k.low <= ov_high]
            cnt = len(ov_indices)
            
            # 5. 终极确权：重叠区间必须覆盖当前中枢线
            is_cl_covered = (ov_low <= center_line <= ov_high)

            # 判断逻辑：5K 或 4K+跳空（反向或破位跳空坐实力量），且必须覆盖中枢线
            is_ok = False
            if cnt >= 5:
                is_ok = True
            elif cnt == 4:
                last_ov_idx = ov_indices[-1]
                if len(bars) > last_ov_idx + 1:
                    next_b = bars[last_ov_idx + 1]
                    prev_b = bars[last_ov_idx]
                    # 只要发生物理跳空，即视为中枢力量的爆发确认
                    is_ok = (next_b.low > prev_b.high or next_b.high < prev_b.low)

            is_ok = is_ok and is_cl_covered

            if is_ok:
                return True, ov_indices[0], cnt
            return False, -1, cnt

        # 执行双向竞速
        res_high = _scan_with_anchor(True)
        res_low  = _scan_with_anchor(False)

        # 优先选取成立的，如果都成立，取重叠 K 线更多的那个
        if res_high[0] and res_low[0]:
            return (True, res_high[1]) if res_high[2] >= res_low[2] else (True, res_low[1])
        if res_high[0]:
            return True, res_high[1]
        if res_low[0]:
            return True, res_low[1]

        return False, -1



    def _check_san_bi(self) -> bool:
        """式三：三笔纯势（正-反-正 N字结构）"""
        s = self.s
        if s.center_line_k_index < 0 or s.center_end_k_index < 0:
            return False

        # 【核心修复：物理极值防火墙】
        # 视界必须受限于当前线段锚点，严禁跨线段拼凑
        search_start = max(0, s.center_anchor_idx)
        
        # 同步防穿透书签
        if s.last_center_end_idx != -1:
            search_start = max(search_start, s.last_center_end_idx)

        window_bars = s.bars_raw[search_start : s.center_end_k_index + 1]
        confirm_k_idx = s.center_line_k_index - search_start

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
