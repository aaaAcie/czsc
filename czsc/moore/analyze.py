# -*- coding: utf-8 -*-
"""
author: moore_czsc
describe: 摩尔缠论核心分析引擎 (状态机)
"""
import collections
from typing import List, Optional
from dataclasses import dataclass
from czsc.py.objects import RawBar
from czsc.py.enum import Mark, Direction
from .objects import TurningK, MooreCenter, MooreSegment


class MooreCZSC:
    """摩尔缠论核心状态机分析引擎"""

    def __init__(self, bars: List[RawBar], max_segments: int = 500):
        self.max_segments = max_segments
        
        # 基础数据容器
        self.bars_raw: List[RawBar] = []             # 传入的所有合法原始K线
        self.ma5_q = collections.deque(maxlen=5)     # 实时计算 MA5
        self.ma34_q = collections.deque(maxlen=34)   # 实时计算 MA34
        
        # 结果输出容器
        self.turning_ks: List[TurningK] = []         # 所有产生的转折极值K线
        self.segments: List[MooreSegment] = []       # 确立的本质线段
        
        # --- 引擎游标状态锁 ---
        
        # 1. 顶底引擎游标
        self.last_ma5 = None                         # 记录上一根 K 线的 MA5 值用于比对
        self.candidate_tk: Optional[TurningK] = None # 当前备选/未确立的转折 K 线
        
        # 调试计数器
        self._debug_rule_fail = {1: 0, 1.1: 0, 2: 0, 3: 0}
        self._debug_trigger_count = 0  # 触发次数统计
        self._debug_body_filter = 0    # 实体推升拦截次数
        
        # 内部状态机：处理蓝框逻辑的后移确立
        self._waiting_next_as_tk: bool = False
        # 记录最初触发信号的那根 K，避免标记滞后
        self._signal_bar_cache: Optional[RawBar] = None
        self._signal_index_cache: Optional[int] = None
        self._waiting_mark: Optional[Mark] = None

        self.refreshed_segments = []
        self.potential_centers = []   
        self.all_centers = []         
        
        # 中枢引擎内部状态
        self.center_state = 0
        self.current_k0 = None

        # --- 趋势穿透层状态 ---
        self.trend_state: Optional[Direction] = None        # 当前趋势方向
        self.trend_high: Optional[float] = None             # 趋势内全局最高价
        self.trend_low: Optional[float] = None              # 趋势内全局最低价
        self.trend_extreme_k: Optional[TurningK] = None     # 代表全局极值的 TurningK
        self.segment_start_extreme: Optional[float] = None  # 当前线段起点极值（上一确认线段终点价格）

        # 穿透灵敏度配置
        # 1=STRUCT_ONLY(仅结构) / 2=BREAK_SEG_START(或突破线段起点) / 3=BREAK_TREND_GLOBAL(或突破趋势全局极值)
        self.penetration_level: int = 2
        
        for bar in bars:
            self.update(bar)

    def update(self, bar: RawBar):
        """流式处理引擎入口：每接收一根 K 线，推动一次状态机引擎"""
        # --- 1. 数据预处理与冷启动拦截 ---
        self.bars_raw.append(bar)
        k_index = len(self.bars_raw) - 1
        
        # 喂入均线管道
        self.ma5_q.append(bar.close)
        self.ma34_q.append(bar.close)
        
        # 冷启动判断：MA34 未充盈时，仅累积数据，所有状态机静默 (Engineering Defenses #1)
        if len(self.ma34_q) < 34:
            return
            
        current_ma5 = sum(self.ma5_q) / 5
        current_ma34 = sum(self.ma34_q) / 34
        
        # 将指标赋给原始 bar 的缓存中，方便后续读取
        bar.cache['ma5'] = current_ma5
        bar.cache['ma34'] = current_ma34
        
        # 先更新中枢引擎，探测当前正在进行的这一笔里面是否产生了中枢
        # 注意：中枢引擎的运行不依赖于 turning_ks 的存在，它独立寻找潜在中枢
        self._update_center_engine(bar, k_index)

        # --- 2. 顶底确立游标引擎 ---
        self._update_turning_k_engine(bar, k_index, current_ma5)
            
        # 游标步进
        self.last_ma5 = current_ma5

    # =========================================================================
    # 第一模块：顶底引擎 (The Fractals Engine)
    # =========================================================================
    def _update_turning_k_engine(self, bar: RawBar, k_index: int, ma5: float):
        """转折 K 触发、平移与四法则验真引擎"""
        if self.last_ma5 is None: return

        # 判断是否发生特殊跳空触发
        prev_bar = self.bars_raw[k_index - 1]
        is_gap_up = bar.low > prev_bar.high
        is_gap_down = bar.high < prev_bar.low
        
        is_solid_gap_up = min(bar.open, bar.close) > ma5 and is_gap_up
        is_solid_gap_down = max(bar.open, bar.close) < ma5 and is_gap_down

        # --- 2. 顶底确立方向判定 ---
        # 寻找方向始终以上一个“确立（已验证）”的顶底为基准
        # 如果上一个是底(Mark.D)，则现在找顶(Mark.G)；反之亦然。
        seeking_mark = Mark.D  # 默认找底
        if self.turning_ks:
            seeking_mark = Mark.G if self.turning_ks[-1].mark == Mark.D else Mark.D

        triggered = False
        new_mark = None
        new_price = 0.0

        # --- 第一重过滤：触发机关 (MA5停滞 + 实体有一端突破 MA5) ---
        if seeking_mark == Mark.G:  # 正在找顶（上涨线段中，等待反转信号）
            if ma5 <= self.last_ma5 or is_solid_gap_down:
                # 实体至少有一端突破到 MA5 下方（触发顶转折）
                if min(bar.open, bar.close) < ma5:
                    triggered = True
                    new_mark = Mark.G
                    self._debug_trigger_count += 1
                    
        elif seeking_mark == Mark.D: # 正在找底（下跌线段中，等待反转信号）
            if ma5 >= self.last_ma5 or is_solid_gap_up:
                # 实体至少有一端突破到 MA5 上方（触发底转折）
                if max(bar.open, bar.close) > ma5:
                    triggered = True
                    new_mark = Mark.D
                    self._debug_trigger_count += 1

        # ==================================================================
        # 步骤零：处理上一次遗留的“蓝框后移确立”
        # ==================================================================
        # ==================================================================
        # 步骤A：轮询校验。即使没有新触发，已有备选点可能在这一根K线满足了法则二(均线交叉)
        # ==================================================================
        if self.candidate_tk:
            if self._validate_four_rules(self.candidate_tk):
                final_tk = self.candidate_tk
                final_tk.is_valid = True
                
                # 同向替换（新生刷新）
                if self.turning_ks and self.turning_ks[-1].mark == final_tk.mark:
                    self.turning_ks.pop()
                    
                self.turning_ks.append(final_tk)
                self._update_segments()
                self.candidate_tk = None
                self._rollback_center_engine()
                self.potential_centers = [] # 确立后清空，为下一轮寻找准备
                return # 确立成功不再走候选逻辑

        # ==================================================================
        # 步骤零：处理上一次遗留的“蓝框后移确立”
        # ==================================================================
        if hasattr(self, '_waiting_next_as_tk') and self._waiting_next_as_tk:
            self._waiting_next_as_tk = False
            # 本根 K 线即为确定的转折 K
            self._process_confirmed_trigger(bar, k_index, self._waiting_mark)
            # 确立后不 return，允许这根 K 继续作为普通 K 探测新的刷新/反转

        # --- 2. 顶底确立方向判定 ---
        # 探测两个方向：
        # 1. 正常寻址 (Reversal)：寻找与当前末端相反的信号
        # 2. 同向刷新 (Refresh)：寻找与当前末端相同的更好信号
        reversal_mark = Mark.D
        refresh_mark = None
        if self.turning_ks:
            last_mark = self.turning_ks[-1].mark
            reversal_mark = Mark.G if last_mark == Mark.D else Mark.D
            refresh_mark = last_mark

        # 构建探测任务列表，优先级：Refresh > Reversal
        tasks = []
        if refresh_mark:
            tasks.append((refresh_mark, True))
        tasks.append((reversal_mark, False))

        for target_mark, is_refresh in tasks:
            triggered = False
            # 基础触发：MA5 停滞 + 价格突破
            if target_mark == Mark.G:  # 找顶
                if ma5 <= self.last_ma5 or is_solid_gap_down:
                    if min(bar.open, bar.close) < ma5:
                        triggered = True
            else:  # 找底
                if ma5 >= self.last_ma5 or is_solid_gap_up:
                    if max(bar.open, bar.close) > ma5:
                        triggered = True

            if triggered:
                # 记录原始触发信号点（解决可视化后移一根的问题）
                self._signal_bar_cache = bar
                self._signal_index_cache = k_index
                
                # --- [核心优化] 深层穿透刷新判定 ---
                real_is_refresh = is_refresh
                if not real_is_refresh and len(self.turning_ks) >= 2:
                    # 哲学：如果中间线段极其短促（K线少）或价格被显著跌破，虚实标记不应阻碍趋势延伸
                    prev_same = self.turning_ks[-2]
                    if prev_same.mark == target_mark:
                        # 只要价格更优，且中间点不是一个极其厚实的“完美”结构，就允许穿透
                        # 这里暂时完全移除 is_perfect 拦截，转由价格优势驱动
                        is_better = (target_mark == Mark.G and bar.high > prev_same.price) or \
                                    (target_mark == Mark.D and bar.low < prev_same.price)
                        if is_better: real_is_refresh = True

                if real_is_refresh and self.turning_ks:
                    # 确定参考基准点进行价格占优判定
                    ref_tk = next((x for x in reversed(self.turning_ks) if x.mark == target_mark), None)
                    if ref_tk:
                        if target_mark == Mark.G and bar.high <= ref_tk.price: continue
                        if target_mark == Mark.D and bar.low >= ref_tk.price: continue

                self._debug_trigger_count += 1
                is_blue_box = False
                prev_pk = next((x for x in reversed(self.turning_ks) if x.mark == target_mark), None)
                if prev_pk:
                    if target_mark == Mark.G:
                        if max(bar.open, bar.close) > min(prev_pk.raw_bar.open, prev_pk.raw_bar.close):
                            is_blue_box = True
                    else:
                        if min(bar.open, bar.close) < max(prev_pk.raw_bar.open, prev_pk.raw_bar.close):
                            is_blue_box = True

                if is_blue_box:
                    self._waiting_next_as_tk = True
                    self._waiting_mark = target_mark
                else:
                    self._process_confirmed_trigger(bar, k_index, target_mark)
                
                break # 一旦触发，终止探测任务

    def _process_confirmed_trigger(self, turning_k_bar: RawBar, turning_k_index: int, new_mark: Mark):
        """内部私有：处理已定位转折K后的极值寻址与真假校检"""
        
        # 使用缓存的信号点作为真实的转折K，如果没有缓存（通常不应该），则使用当前确认K
        actual_trigger_bar = self._signal_bar_cache if self._signal_bar_cache else turning_k_bar
        actual_trigger_index = self._signal_index_cache if self._signal_index_cache is not None else turning_k_index

        # ==================================================================
        # 步骤二：以确定的“转折K”为右边界，往左寻找最近的相对局部极值（顶/底）
        # ==================================================================
        search_start_idx = 0
        if self.turning_ks:
            # 如果是同向刷新，则搜索范围应追溯到前一个异向点之后
            if self.turning_ks[-1].mark == new_mark:
                search_start_idx = self.turning_ks[-2].k_index + 1 if len(self.turning_ks) >= 2 else 0
            else:
                search_start_idx = self.turning_ks[-1].k_index + 1

        # 搜索区间不包含转折K本身，确立顶底必须在转折信号发生之前
        search_bars = self.bars_raw[search_start_idx : turning_k_index]
        n = len(search_bars)

        extreme_bar = None
        extreme_k_index = None

        # 从右（转折K侧）往左逐根扫描局部极值
        if n >= 1:
            for i in range(n - 1, -1, -1):
                curr_b = search_bars[i]
                p_idx = (search_start_idx + i) - 1
                prev_b = self.bars_raw[p_idx] if p_idx >= 0 else None
                next_b = turning_k_bar if i == (n - 1) else search_bars[i + 1]

                if not prev_b or not next_b: continue
                if (new_mark == Mark.G and curr_b.high > prev_b.high and curr_b.high > next_b.high) or \
                   (new_mark == Mark.D and curr_b.low < prev_b.low and curr_b.low < next_b.low):
                    extreme_bar, extreme_k_index = curr_b, search_start_idx + i
                    break

        # 兜底：未找到 3K 局部极值点时，取 search_bars 中的绝对极值
        if extreme_bar is None and n > 0:
            if new_mark == Mark.G:
                extreme_bar = max(search_bars, key=lambda b: b.high)
            else:
                extreme_bar = min(search_bars, key=lambda b: b.low)
            extreme_k_index = search_start_idx + search_bars.index(extreme_bar)

        if not extreme_bar: return
        new_price = extreme_bar.high if new_mark == Mark.G else extreme_bar.low

        new_tk = TurningK(
            symbol=extreme_bar.symbol, dt=extreme_bar.dt, raw_bar=extreme_bar, k_index=extreme_k_index,
            trigger_k=turning_k_bar, trigger_k_index=turning_k_index, mark=new_mark, price=new_price
        )

        # --- 同向替换/新生刷新逻辑 ---
        if self.candidate_tk:
            # 只要新触发点在时间轴上更晚，无论价格是否更极端，都应刷新（MA5 动态生新）
            # 或者新信号价格更极端
            if (new_mark == Mark.G and new_price >= self.candidate_tk.price) or \
               (new_mark == Mark.D and new_price <= self.candidate_tk.price) or \
               (turning_k_index > self.candidate_tk.trigger_k_index):
                self.candidate_tk = new_tk
        else:
            self.candidate_tk = new_tk

        # --- 四法则验真 ---
        if self.candidate_tk:
            valid_base, perfect_struct = self._validate_four_rules(self.candidate_tk)
            if valid_base:
                final_tk = self.candidate_tk
                final_tk.trigger_k = actual_trigger_bar # 确保最终确立的tk也使用原始触发点
                final_tk.trigger_k_index = actual_trigger_index # 确保最终确立的tk也使用原始触发点
                final_tk.is_valid = True
                final_tk.is_perfect = perfect_struct
                
                # 同向刷新 / 趋势穿透多层回溯 / 正常追加
                if self.turning_ks:
                    if self.turning_ks[-1].mark == final_tk.mark:
                        # 正常相邻同向刷新：直接替换末端
                        self.turning_ks.pop()
                    elif self._check_penetration(final_tk, final_tk.is_perfect):
                        # 趋势级穿透：动态多层吞噬不稳定链
                        # segment_start_extreme 在回溯期间保持不变（线段延申而非重置）
                        self._consume_imperfect_chain(final_tk)

                # 追加新 TurningK
                self.turning_ks.append(final_tk)

                # 新反向线段确立后，锁定上上个同向点（它成为历史端点）
                if len(self.turning_ks) >= 3:
                    candidate_lock = self.turning_ks[-3]
                    if candidate_lock.mark != final_tk.mark:
                        candidate_lock.is_locked = True

                # 更新线段起点极值（仅真正反向时更新，同向刷新时不动）
                if len(self.turning_ks) >= 2 and self.turning_ks[-2].mark != final_tk.mark:
                    self.segment_start_extreme = self.turning_ks[-2].price

                # 更新趋势状态
                self._update_trend_state(final_tk)

                self._update_segments()
                self.candidate_tk = None
                self._rollback_center_engine()
                self.potential_centers = [] # 确立后清空暂存区，为下一轮寻找准备

    def _validate_four_rules(self, tk: TurningK) -> tuple[bool, bool]:
        """顶底四法则验真。
        返回: (is_valid, is_perfect)
        """
        if not self.turning_ks:
            return True, True 

        last_tk = self.turning_ks[-1]
        
        # 确定参考基准点 (ref_tk)
        if last_tk.mark == tk.mark:
            ref_tk = self.turning_ks[-2] if len(self.turning_ks) >= 2 else last_tk
        else:
            ref_tk = last_tk
        
        bars_between = self.bars_raw[ref_tk.k_index : tk.k_index + 1]

        # 1. 第一法则：3K 极值要求
        # 要求当前确立的极值点 tk.raw_bar 必须是一个局部极值（3K形态：中间高于/低于两侧）
        idx = tk.k_index
        if 0 < idx < len(self.bars_raw) - 1:
            prev_b = self.bars_raw[idx - 1]
            curr_b = self.bars_raw[idx]   # 即 tk.raw_bar
            next_b = self.bars_raw[idx + 1]
            
            is_3k_ok = False
            if tk.mark == Mark.G:
                # 顶：中间 K 的 high > 两侧 K 的 high
                if curr_b.high > prev_b.high and curr_b.high > next_b.high:
                    is_3k_ok = True
            else:
                # 底：中间 K 的 low < 两侧 K 的 low
                if curr_b.low < prev_b.low and curr_b.low < next_b.low:
                    is_3k_ok = True
            
            if not is_3k_ok:
                self._debug_rule_fail[1] += 1
                return False, False # 不满足3K极值，连基础确立都不算
        else:
            # 如果在边界，无法构成 3K，放行（或按需处理）
            pass

        # 2. 第二法则：两侧大铡刀 (MA34 与 MA5 金死叉)
        # 在 ref_tk 到 tk 这段期间，至少发生一次 MA5/MA34 交叉
        cross_happened = False
        prev_diff = None
        for b in bars_between:
            m5 = b.cache.get('ma5')
            m34 = b.cache.get('ma34')
            if m5 is None or m34 is None: continue
            diff = m5 - m34
            if prev_diff is not None:
                if (prev_diff > 0 and diff < 0) or (prev_diff < 0 and diff > 0):
                    cross_happened = True
                    break
            prev_diff = diff
        
        if not cross_happened:
            self._debug_rule_fail[2] = self._debug_rule_fail.get(2, 0) + 1
            return False, False  # 没交叉，连基础确立都不算

        # 3. 第三法则：结构完整性 (内部必须包含中枢)
        # 不拦截线段生成，但真实写入 is_perfect 标记，供趋势穿透层使用
        is_perfect = True
        if not self.potential_centers:
            self._debug_rule_fail[3] = self._debug_rule_fail.get(3, 0) + 1
            is_perfect = False  # 无中枢 → 微观结构不完美（虚线标记）

        return True, is_perfect

    def _update_segments(self):
        """同步 turning_ks 到 segments 列表（重新构建，确保刷新逻辑一致性）"""
        self.segments = []
        if len(self.turning_ks) >= 2:
            for i in range(len(self.turning_ks) - 1):
                tk1 = self.turning_ks[i]
                tk2 = self.turning_ks[i+1]
                direction = Direction.Up if tk1.mark == Mark.D else Direction.Down
                segment_bars = self.bars_raw[tk1.k_index : tk2.k_index + 1]
                
                seg = MooreSegment(
                    symbol=tk1.symbol, start_k=tk1, end_k=tk2,
                    direction=direction, bars=segment_bars
                )
                # 从历史仓库 all_centers 中提取符合该线段时间的中枢进行挂载
                seg.centers = [c for c in self.all_centers if c.start_dt >= tk1.dt and c.end_dt <= tk2.dt]
                self.segments.append(seg)
        
        # 控制最大数量
        if len(self.segments) > self.max_segments:
            self.segments = self.segments[-self.max_segments:]

    # =========================================================================
    # 第一模块附属：趋势穿透层 (The Trend Penetration Layer)
    # =========================================================================

    def _check_penetration(self, new_tk: TurningK, is_perfect: bool) -> bool:
        """根据 penetration_level 判断是否触发趋势穿透（允许跨越回溯）
        
        OR 递进关系：高级别天然包含低级别条件。
          Level 1: 仅结构不完美 → 允许吞噬
          Level 2: Level1 OR 突破线段起点极值
          Level 3: Level1 OR 突破趋势全局极值（最宽松）
        """
        # 条件 A：结构不完美（所有级别均包含）
        if not is_perfect:
            return True

        # 条件 B：突破线段起点极值（Level 2 / 3）
        if self.penetration_level >= 2 and self.segment_start_extreme is not None:
            if new_tk.mark == Mark.G and new_tk.price > self.segment_start_extreme:
                return True
            if new_tk.mark == Mark.D and new_tk.price < self.segment_start_extreme:
                return True

        # 条件 C：突破趋势全局极值（Level 3）
        if self.penetration_level >= 3:
            if self.trend_high is not None and new_tk.mark == Mark.G and new_tk.price > self.trend_high:
                return True
            if self.trend_low is not None and new_tk.mark == Mark.D and new_tk.price < self.trend_low:
                return True

        return False

    def _consume_imperfect_chain(self, new_pivot: TurningK):
        """双重门多层回溯引擎：动态吞噬不稳定结构链
        
        门1（方向门）：只吞噬正确方向的异向中继
        门2（防御门）：宏观锁定（is_locked）为绝对铁门；结构完美（is_perfect）为弹性门
        门3（价格门）：新 pivot 必须在价格上碾压旧同向点
        """
        MAX_BACKTRACK = 50
        backtrack_count = 0
        while len(self.turning_ks) >= 2:
            if backtrack_count > MAX_BACKTRACK:
                break
            last_opposite = self.turning_ks[-1]   # 最近的异向点（待吞噬的中继）
            last_same     = self.turning_ks[-2]   # 最近的同向点（待替换的旧极值）

            # 门1：方向必须正确
            if last_opposite.mark == new_pivot.mark:
                break

            # 门2：双重防御（宏观铁门 > 弹性微观门）
            # 绝对防御：宏观锁定，任何情况不可吞噬
            if last_opposite.is_locked:
                break
            # 弹性防御：结构完美 + 保守模式（Level 1），停止
            if last_opposite.is_perfect:
                if self.penetration_level == 1:   # STRUCT_ONLY
                    break
                # Level 2/3：完美但未锁定，继续看价格门

            # 门3：价格替代 — 新 pivot 必须在价格上碾压旧同向点
            if last_same.mark != new_pivot.mark:
                break
            if new_pivot.mark == Mark.G and new_pivot.price < last_same.price:
                break
            if new_pivot.mark == Mark.D and new_pivot.price > last_same.price:
                break

            # 通过三重门 → 吞噬一层
            self.turning_ks.pop()   # 吞噬异向中继
            self.turning_ks.pop()   # 吞噬旧同向点
            backtrack_count += 1

    def _update_trend_state(self, new_tk: TurningK):
        """在新 TurningK 确立后，更新趋势状态、全局极值与翻转判断"""
        # 趋势初始化：第一根有效线段生成时赋值
        if self.trend_state is None:
            if len(self.turning_ks) >= 2:
                self.trend_state = Direction.Up if self.turning_ks[0].mark == Mark.D else Direction.Down
                g_tks = [tk for tk in self.turning_ks if tk.mark == Mark.G]
                d_tks = [tk for tk in self.turning_ks if tk.mark == Mark.D]
                self.trend_high = max(tk.price for tk in g_tks) if g_tks else None
                self.trend_low  = min(tk.price for tk in d_tks) if d_tks else None
                if self.trend_state == Direction.Up:
                    self.trend_extreme_k = max(g_tks, key=lambda x: x.price) if g_tks else None
                else:
                    self.trend_extreme_k = min(d_tks, key=lambda x: x.price) if d_tks else None
            return

        # 更新全局极值
        if new_tk.mark == Mark.G:
            if self.trend_high is None or new_tk.price > self.trend_high:
                self.trend_high = new_tk.price
                if self.trend_state == Direction.Up:
                    self.trend_extreme_k = new_tk
        if new_tk.mark == Mark.D:
            if self.trend_low is None or new_tk.price < self.trend_low:
                self.trend_low = new_tk.price
                if self.trend_state == Direction.Down:
                    self.trend_extreme_k = new_tk

        # 趋势翻转双重锁（满足其一即翻转）
        if self.trend_state == Direction.Up:
            # V 型反转：新底直接打穿全局最低
            if new_tk.mark == Mark.D and self.trend_low is not None and new_tk.price < self.trend_low:
                self._flip_trend(Direction.Down, new_tk)
            # 结构翻转：完美反向线段突破最近关键节点
            elif (new_tk.mark == Mark.D and new_tk.is_perfect
                  and len(self.segments) >= 2):
                key_node = self.segments[-2].start_k.price   # 最近上涨段的起点
                if new_tk.price < key_node:
                    self._flip_trend(Direction.Down, new_tk)
        else:  # Direction.Down
            if new_tk.mark == Mark.G and self.trend_high is not None and new_tk.price > self.trend_high:
                self._flip_trend(Direction.Up, new_tk)
            elif (new_tk.mark == Mark.G and new_tk.is_perfect
                  and len(self.segments) >= 2):
                key_node = self.segments[-2].start_k.price
                if new_tk.price > key_node:
                    self._flip_trend(Direction.Up, new_tk)

    def _flip_trend(self, new_direction: Direction, trigger_tk: TurningK):
        """执行趋势翻转：重置方向与全局极值"""
        self.trend_state = new_direction
        # 翻转后以触发点为新趋势的起始极值
        if new_direction == Direction.Up:
            self.trend_low  = trigger_tk.price
            self.trend_high = None
        else:
            self.trend_high = trigger_tk.price
            self.trend_low  = None
        self.trend_extreme_k = trigger_tk


    def _rollback_center_engine(self):
        """回滚中枢巡航游标 (Engineering Defenses #3)"""
        self.center_state = 0
        self.current_k0 = None

    def _update_center_engine(self, bar: RawBar, k_index: int):
        """在最新的线段上，追踪并构建双极轨道中枢"""
        # 寻找方向参考 last_confirmed_tk
        # 中枢方向与当前正在形成的线段方向一致
        direction = Direction.Up
        if self.turning_ks:
            # 如果有已确立的转折K，则中枢方向与当前正在形成的线段方向一致
            direction = Direction.Up if self.turning_ks[-1].mark == Mark.D else Direction.Down
        else:
            # 如果还没有确立的转折K，则根据MA5方向判断
            if self.last_ma5 is not None:
                if bar.cache.get('ma5', 0) > self.last_ma5:
                    direction = Direction.Up
                elif bar.cache.get('ma5', 0) < self.last_ma5:
                    direction = Direction.Down
                else:
                    return # MA5持平，方向不明，不处理中枢

        ma5 = bar.cache.get('ma5', 0)
        
        # =========================================================
        # 统一核心游标：无论肉眼或非肉眼，建立中枢必须首先完成起手式：
        # 步骤一：抓锚点 K0 (绝密发源地)
        # 步骤二：等破坏，获取确认K (中枢线的奠基人)
        # =========================================================
        
        # --- 步骤一：抓锚点 K0 ---
        if self.center_state == 0:
            # K0 的实体必须绝对站在 MA5 正侧 (比如上涨线段，K实体完全在MA5之上)
            is_pure = False
            if direction == Direction.Up:
                is_pure = bar.close > ma5
            else:
                is_pure = bar.close < ma5
                
            if is_pure:
                # 检查是否与前一个历史中枢重叠 (这里应该检查 potential_centers 的最后一个)
                has_overlap = False
                if self.potential_centers:
                    prev_center = self.potential_centers[-1]
                    # 如果当前K0与上一个中枢有重叠，则不作为新的K0
                    if not (bar.low > prev_center.upper_rail or bar.high < prev_center.lower_rail):
                        has_overlap = True
                
                if not has_overlap:
                    self.current_k0 = bar
                    self.center_state = 1
                    
        # --- 步骤二：等破坏，获取确认K ---
        elif self.center_state == 1:
            # 2a. 如果当前 K 依然满足纯正侧条件，则滚动更新 K0（保持最新的纯正侧 K）
            is_still_pure = False
            if direction == Direction.Up:
                is_still_pure = bar.close > ma5
            else:
                is_still_pure = bar.close < ma5
            
            if is_still_pure:
                # K0 滚动刷新为最新的纯正侧 K
                self.current_k0 = bar
                return
            
            # 2b. 检查是否触发确认K（实体反穿 MA5）
            is_break = False
            if direction == Direction.Up:
                is_break = bar.close < ma5 # 常规反穿：跌破MA5
            else:
                is_break = bar.close > ma5
                
            if is_break:
                confirm_k = bar
                self.center_state = 2
                
                # 此时，我们拿到了完整的起手素材 [K0, ..., confirm_k]
                # 开始交由底层路线判断是肉眼中枢还是非肉眼中枢，并进行定轨挂载
                self._dispatch_and_mount_center(direction, self.current_k0, confirm_k, k_index)
                
    def _dispatch_and_mount_center(self, direction: Direction, k0: RawBar, confirm_k: RawBar, cf_index: int):
        """定性中枢类别、定轨并存入 potential_centers"""
        # =========================================================
        # 第一阶段判定：是否满足"肉眼可见中枢"的震荡阈值
        # 判定条件：MA5 发生了明显的折返（至少1次斜率正负交替）
        # 同时记录"第一次折返时的 MA5 极值"，作为肉眼中枢的另一条轨道基准
        # =========================================================
        k0_idx = self.bars_raw.index(k0)
        past_bars = self.bars_raw[k0_idx : cf_index + 1]
        
        is_visible = False
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
                        is_peak_flip  = (prev_slope > 0 and slope < 0)   # MA5 从上升变下降 = 峰顶
                        is_trough_flip = (prev_slope < 0 and slope > 0)  # MA5 从下降变上升 = 谷底
                        if direction == Direction.Up and is_peak_flip:
                            first_flip_ma5 = m1  # 上涨段需要波峰作为上轨
                        elif direction == Direction.Down and is_trough_flip:
                            first_flip_ma5 = m1  # 下跌段需要波谷作为下轨
            prev_slope = slope
        # 0. 初始化
        start_dt = k0.dt
        is_visible = False
        first_flip_ma5 = None
        
        if slope_flips >= 1:
            is_visible = True
                
        # 1. 提取中枢线：基于 MA5 在确认K时的位置
        ma5_confirm = confirm_k.cache.get('ma5', 0)
        center_line = ma5_confirm
        
        # 2. 定轨法则分支
        if is_visible:
            # 如果没有采集到有效折返，取 K0 的 MA5
            ref_ma5 = first_flip_ma5 if first_flip_ma5 is not None else ma5_k0
            upper_rail = max(ma5_confirm, ref_ma5)
            lower_rail = min(ma5_confirm, ref_ma5)
        else:
            # 非肉眼中枢：寻找 K0 到 ConfirmK 之间通过 CenterLine 的 3K 缠绕区间
            cross_bars = []
            start_search_idx = self.turning_ks[-1].k_index if self.turning_ks else 0
            for i in reversed(range(max(start_search_idx, k0_idx), cf_index)):
                cb = self.bars_raw[i]
                if cb.low <= center_line <= cb.high:
                    cross_bars.insert(0, cb)
                else:
                    if len(cross_bars) < 3: cross_bars = []
                    else: break
            
            upper_rail = center_line
            lower_rail = center_line
            if len(cross_bars) >= 3:
                leftmost_3k = cross_bars[0:3]
                start_dt = leftmost_3k[0].dt
                overlap_high = min(b.high for b in leftmost_3k)
                overlap_low = max(b.low for b in leftmost_3k)
                if direction == Direction.Up: upper_rail = overlap_high
                else: lower_rail = overlap_low
            
            # --- 隐性中枢验真 ---
            valid_center = False
            extreme_ref = self.turning_ks[-1].raw_bar if self.turning_ks else k0
            # 式2: 5K重叠
            intersect_high = min(extreme_ref.high, confirm_k.high)
            intersect_low = max(extreme_ref.low, confirm_k.low)
            if intersect_high >= intersect_low:
                overlap_count = sum(1 for i in range(k0_idx, cf_index + 1) 
                                    if self.bars_raw[i].low <= intersect_high and self.bars_raw[i].high >= intersect_low)
                if overlap_count >= 5: valid_center = True
            
            # 式3: 三笔纯势
            if not valid_center:
                for i in range(k0_idx + 1, cf_index):
                    mid_bar = self.bars_raw[i]
                    if direction == Direction.Up:
                        if mid_bar.high > extreme_ref.high and confirm_k.high > mid_bar.high:
                            valid_center = True; break
                    else:
                        if mid_bar.low < extreme_ref.low and confirm_k.low < mid_bar.low:
                            valid_center = True; break
            
            # 这里暂时移除 valid_center 物理形态拦截，只要完成反穿动作即视为中枢形成
            # if not valid_center:
            #     self._rollback_center_engine()
            #     return

        # =========================================================
        # 3. 中枢向右扩张
        # =========================================================
        end_dt = confirm_k.dt
        for i in range(cf_index + 1, len(self.bars_raw)):
            expand_bar = self.bars_raw[i]
            if expand_bar.high >= lower_rail and expand_bar.low <= upper_rail:
                end_dt = expand_bar.dt
            else: break
                
        # 4. 生成与排他挂载
        type_str = "VISIBLE" if is_visible else "INVISIBLE"
        center = MooreCenter(
            type_name=type_str, direction=direction,
            anchor_k0=k0, confirm_k=confirm_k,
            center_line=center_line, upper_rail=upper_rail, lower_rail=lower_rail,
            start_dt=start_dt, end_dt=end_dt
        )
        
        if self.potential_centers:
            last_c = self.potential_centers[-1]
            if center.lower_rail <= last_c.upper_rail and center.upper_rail >= last_c.lower_rail:
                if not last_c.is_visible and is_visible:
                    # 肉眼霸权：剔除前面重合的隐性
                    self.potential_centers.pop()
                elif is_visible and last_c.is_visible:
                    # 双肉眼重合扩张
                    last_c.end_dt = end_dt
                    last_c.upper_rail = max(last_c.upper_rail, upper_rail)
                    last_c.lower_rail = min(last_c.lower_rail, lower_rail)
                    self._rollback_center_engine()
                    return
                else:
                    self._rollback_center_engine()
                    return
                    
        self.potential_centers.append(center)
        self._rollback_center_engine()

    def deprecated_compute_and_mount_center(self):
        """废弃的底层老引擎"""

