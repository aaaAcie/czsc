# -*- coding: utf-8 -*-
"""
30分钟线段级分析器（SegmentAnalyzer）

包含：
  - SegmentState：三个子引擎共享的状态容器（数据总线）
  - SegmentAnalyzer：协调 FractalEngine / CenterEngine / TrendEngine 的主入口

外部通过 SegmentAnalyzer 的属性直接访问结果：
    analyzer.turning_ks   → List[TurningK]
    analyzer.segments     → List[MooreSegment]
    analyzer.all_centers  → List[MooreCenter]
    analyzer.ghost_forks  → List[tuple]
    analyzer.trend_state  → Optional[Direction]
    ...

【架构说明：宏观滞后审判与同向跃迁】
  原有的微观价格穿透机制（check_penetration / consume_imperfect_chain）已被彻底废弃。
  新架构采用"放弃微观实时生长，改由宏观滞后审判驱动回溯"的策略：

  - FractalEngine：纯粹顶底记录仪，只负责确立原始顶底，不参与任何线段合并。
  - TrendEngine：仅维护趋势状态（极值 / 方向 / 翻转），不处理任何穿透逻辑。
  - SegmentAnalyzer（宏观审判层）：在每根 K 线的 fractal_engine.update() 之后，
    执行 _macro_audit_and_replay() —— 检查被审判线段 N（n2→n3）是否"不完美"，
    若通过冷静期检验，则尝试三级同向跃迁回溯，将中间的噪音幻影塌陷为幽灵，
    重建正确的线段结构。

【四点模型索引定义】（以上涨趋势为例：1=底, 2=顶, 3=底, 4=顶）
  n4 = turning_ks[-1]：最新确立的顶/底（N+1 段的终点）
  n3 = turning_ks[-2]：N+1 段起点 / N 段终点
                       n3.is_perfect 代表 N 段（n2→n3）的结构完整性
  n2 = turning_ks[-3]：N 段起点（疑假端点打标位置）
  n1 = turning_ks[-4]：P1 跃迁锚点
  n0 = turning_ks[-5]：P2 跃迁锚点
  nm1= turning_ks[-6]：P3 跃迁锚点
"""
import collections
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from czsc.py.objects import RawBar
from czsc.py.enum import Mark, Direction

from ..objects import TurningK, MooreCenter, MooreSegment
from .fractal import FractalEngine
from .center import CenterEngine
from .trend import TrendEngine


@dataclass
class SegmentState:
    """三个子引擎共享的状态容器（数据总线）

    引擎之间通过持有同一个 SegmentState 实例来共享和修改状态，
    而不是直接互相调用对方的方法（降低耦合）。
    """

    # 可配置参数
    max_segments: int = 500

    # -------------------------------------------------------------------------
    # 基础数据容器
    # -------------------------------------------------------------------------
    bars_raw: List[RawBar] = field(default_factory=list)

    # -------------------------------------------------------------------------
    # 结果输出容器
    # -------------------------------------------------------------------------
    turning_ks: List[TurningK]        = field(default_factory=list)
    segments:   List[MooreSegment]    = field(default_factory=list)
    all_centers: List[MooreCenter]    = field(default_factory=list)
    refreshed_segments: list          = field(default_factory=list)
    ghost_forks: List[tuple]          = field(default_factory=list)

    # -------------------------------------------------------------------------
    # 顶底引擎游标
    # -------------------------------------------------------------------------
    last_ma5: Optional[float]         = None
    candidate_tk: Optional[TurningK]  = None
    potential_centers: List[MooreCenter] = field(default_factory=list)

    # 蓝框后移状态机
    waiting_next_as_tk: bool          = False
    signal_bar_cache: Optional[RawBar]  = None
    signal_index_cache: Optional[int]   = None
    waiting_mark: Optional[Mark]        = None

    # -------------------------------------------------------------------------
    # 中枢引擎游标
    # -------------------------------------------------------------------------
    center_anchor_idx: int            = -1     # 记录当前中枢发源的宏观锚点索引
    center_state: int                 = 0
    current_k0: Optional[RawBar]      = None
    latest_k0: Optional[RawBar]       = None   # 追踪最近的合规 K0，用于在旧中枢夭折时原地重建

    # 观测病房（Pending Center）状态（State 2 使用）
    center_line_k: Optional[RawBar]         = None   # 确认K（中枢线K）
    center_line_k_index: int                = -1     # 确认K 的绝对索引
    center_direction: Optional[Direction]   = None   # 当前中枢方向
    center_upper_rail: float                = 0.0    # 结界上轨（实时可更新）
    center_lower_rail: float                = 0.0    # 结界下轨（实时可更新）
    center_start_dt: Optional[datetime]     = None   # 结界起始时间
    center_start_k_index: int               = -1     # 结界起始时间对应的绝对索引
    center_end_dt: Optional[datetime]       = None   # 结界当前右端（实时居新）
    center_end_k_index: int                 = -1     # 当前窗口最后一根在结界内K的绝对索引
    center_is_double_gap: bool              = False  # confirm_k 与 k0 是否双跳空（式一自动成立）
    center_method_found: Optional[str]      = None   # 记录该中枢第一个被触发的确立名分（起手三式）
    center_black_k_pass: bool               = False  # 记录黑K质检是否通过
    # is_visible 定性严格规定需在正向一笔完整形成后才可判断，不在 State 2 期间提前定性
    last_center_end_idx: int                = -1     # 记录上一个固化中枢的破窗 K 线索引
    escape_bars: list                       = field(default_factory=list)  # 脱轨缓冲区

    # -------------------------------------------------------------------------
    # 趋势穿透层状态
    # -------------------------------------------------------------------------
    trend_state: Optional[Direction]        = None
    trend_high: Optional[float]             = None
    trend_low: Optional[float]              = None
    trend_extreme_k: Optional[TurningK]    = None
    segment_start_extreme: Optional[float]  = None

    # -------------------------------------------------------------------------
    # 调试计数器
    # -------------------------------------------------------------------------
    debug_rule_fail: dict  = field(default_factory=lambda: {1: 0, 1.1: 0, 2: 0, 3: 0})
    debug_trigger_count: int = 0
    debug_body_filter: int   = 0


class SegmentAnalyzer:
    """30分钟线段级分析器

    协调 FractalEngine / CenterEngine / TrendEngine，
    维护 MA5/MA34 滑窗队列，驱动每根 K 线的状态机推进。
    """

    def __init__(self, bars: List[RawBar], max_segments: int = 500):
        # 1. 准备共享状态容器（初始物理空间为空）
        s = SegmentState(bars_raw=[], max_segments=max_segments)
        self.state = s

        # 2. 准备滑窗缓存
        self._ma5_q  = collections.deque(maxlen=5)
        self._ma34_q = collections.deque(maxlen=34)

        # 3. 实例化子引擎（此时它们持有了空的 state 引用）
        self._trend_engine   = TrendEngine(self.state)
        self._center_engine  = CenterEngine(self.state)
        self._fractal_engine = FractalEngine(self.state, self._trend_engine, self._center_engine)

        # 4. 历史状态推演：执行全量 update，物理化重建所有转折点与中枢
        for bar in bars:
            self.update(bar)

    # =========================================================================
    # 公开接口
    # =========================================================================

    def update(self, bar: RawBar):
        """流式处理引擎入口：每接收一根 K 线，推动一次状态机"""
        s = self.state

        # --- 1. 数据预处理与冷启动拦截 ---
        s.bars_raw.append(bar)
        k_index = len(s.bars_raw) - 1

        self._ma5_q.append(bar.close)
        self._ma34_q.append(bar.close)

        if len(self._ma34_q) < 34:
            return

        current_ma5  = sum(self._ma5_q)  / 5
        current_ma34 = sum(self._ma34_q) / 34

        bar.cache['ma5']  = current_ma5
        bar.cache['ma34'] = current_ma34

        # --- 2. 中枢引擎（先于顶底引擎）---
        self._center_engine.update(bar, k_index)

        # --- 3. 顶底确立游标引擎（纯记录，不做线段合并）---
        old_tk_len     = len(s.turning_ks)
        old_last_tk_dt = s.turning_ks[-1].dt if s.turning_ks else None

        self._fractal_engine.update(bar, k_index, current_ma5)

        new_tk_len     = len(s.turning_ks)
        new_last_tk_dt = s.turning_ks[-1].dt if s.turning_ks else None

        # --- 4. 宏观审判层：三级同向跃迁回溯 ---
        # 变化分类：
        #   is_new_tk    = 新的反向 TurningK 被确立（turning_ks 数量真正增加）
        #                  → N+1 段的终点（点4）已固化，这是审判的"黄金时机"
        #   is_refreshed = 仅发生了同向刷新（数量不变但末端 dt 变了）
        #                  → N+1 段终点未固化，审判依据不足，按兵不动
        is_new_tk    = new_tk_len > old_tk_len
        is_refreshed = (not is_new_tk) and (old_tk_len > 0 and old_last_tk_dt != new_last_tk_dt)
        is_changed   = is_new_tk or is_refreshed

        # 宏观审判：仅在"新 TurningK 固化"时触发，四点模型至少需要 4 个转折点
        # 同向刷新时不审判：此时 N+1 段的终点尚未锁定，拿不确定性否定物理事实是错误的
        leap_happened = False
        if is_new_tk and len(s.turning_ks) >= 4:
            leap_happened = self._macro_audit_and_replay(k_index)
        

        # --- 5. 中枢重播（线段结构发生变化时触发，包含同向刷新）---
        if (leap_happened or is_changed) and len(s.turning_ks) >= 2:
            # turning_ks[-2] 是当前最新完整线段的起点锚。
            # 若发生跃迁，_execute_leap_collapse 已重建 turning_ks，
            # 此时 turning_ks[-2] 正是跃迁锚点（n1/n0/nm1），k_index 即其物理 bar 位置。
            tk_replay_start = s.turning_ks[-2]
            real_start_idx  = tk_replay_start.k_index
            real_trig_idx   = (
                tk_replay_start.trigger_k_index
                if tk_replay_start.trigger_k_index is not None
                else tk_replay_start.k_index
            )
            correct_direction = Direction.Up if s.turning_ks[-1].mark == Mark.G else Direction.Down
            self._replay_center_engine_for_segment(real_start_idx, real_trig_idx, k_index, correct_direction)
            
            # 【核心同步】：重播找回中枢后，必须立即同步到线段对象中，否则绘图层看到的 centers 为空
            self._fractal_engine._update_segments()

        # 游标步进
        s.last_ma5 = current_ma5

    # =========================================================================
    # 宏观审判层：三级同向跃迁回溯
    # =========================================================================

    # 宏观审计无需“K 线数”冷静期，只要 N+1 段的终点 n4 确立即可触发

    def _macro_audit_and_replay(self, current_k_idx: int) -> bool:
        """宏观滞后审判 —— 三级同向跃迁回溯引擎

        四点模型（以上涨方向为例：1=底, 2=顶, 3=底, 4=顶）：
          - N 段（被审判的不完美段）= n2 → n3  （n3.is_perfect 反映 N 段完整性）
          - N+1 段（冷静期观测窗口）= n3 → n4

        三级优先级（一旦通过即停止）：
          P1: n1 → n4，吞噬 n2/n3（同向大跃迁）
          P2: n0 → n3，吞噬 n1/n2（历史前溯）
          P3: nm1 → n2，吞噬 n0/n1（深层回补）

        返回：True=发生了跃迁并重建结构，False=未触发
        """
    def _macro_audit_and_replay(self, k_index: int) -> bool:
        """【宏观主动审判】
        
        策略：扫描 turning_ks 列表中的疑似虚假点 (maybe_is_fake)，
        从脆弱点出发，搜索可用的异向终点执行物理坍塌。
        """
        s = self.state
        n = len(s.turning_ks)
        if n < 4: return False

        # 扫描范围：最近确立的三个段落末端，按时间正序扫描（从左到右），优先解决最深远的历史遗留问题
        scan_indices = [n-4, n-3, n-2] 
        
        for idx in scan_indices:
            if idx < 2: continue
            tk_target = s.turning_ks[idx]
            
            # --- 命中标签，开始审判 ---
            if not tk_target.maybe_is_fake:
                continue

            # -----------------------------------------------------------------
            # 搜索异向终点进行吞噬：
            # -----------------------------------------------------------------
            tk_start = s.turning_ks[idx-2]
            mid_same = s.turning_ks[idx-1]
            
            # 候选终点：从当前虚假点往后，按时间顺序 (idx+1 开始) 发射探知网
            potential_end_indices = range(idx + 1, n)
            
            for end_idx in potential_end_indices:
                if end_idx <= idx: continue 
                
                tk_end = s.turning_ks[end_idx]
                
                # 物理前提：连线必须是顶底连接（异向）
                if tk_start.mark != tk_end.mark:
                    print(f"  [Audit] Testing Leap: {tk_start.dt}({tk_start.mark.name}) -> {tk_end.dt}({tk_end.mark.name}) swallow {mid_same.dt}/{tk_target.dt}")
                    if self._check_leap_physics(tk_start, tk_end, mid_same, tk_target):
                        print(f"  [Audit] SUCCESS! Leaping from {tk_start.dt} to {tk_end.dt}")
                        self._execute_leap_collapse(idx-2, end_idx, (idx-1, idx))
                        return True
                    else:
                        print(f"  [Audit] FAILED Physics check.")
        return False

        return False

    def _check_leap_physics(self, tk_start: TurningK, tk_end: TurningK, 
                           tk_mid_same: TurningK, tk_pullback: TurningK) -> bool:
        """执行跃迁判定：法则一 (实力生长) OR 法则二 (重心演化)
        
        法则一：物理生长 (Price Growth)
        法则二：能量覆盖 (Energy 2A)   AND 引力锁定 (MA5 Gravity 2B)
        """
        s = self.state

        # 1. 准备物理参数
        bar_start = tk_start.k_index
        bar_end   = tk_end.k_index
        path_bars = s.bars_raw[bar_start : bar_end + 1]
        path_ma5  = [b.cache.get('ma5') for b in path_bars if b.cache.get('ma5') is not None]
        start_ma5 = tk_start.raw_bar.cache.get('ma5', None)
        mid_ma5   = tk_mid_same.raw_bar.cache.get('ma5', None)
        end_ma5   = tk_end.raw_bar.cache.get('ma5', None)

        if start_ma5 is None or not path_ma5: return False

        # --- 基础判定因子 ---
        tk_end_top = max(tk_end.raw_bar.open, tk_end.raw_bar.close)
        tk_end_bottom = min(tk_end.raw_bar.open, tk_end.raw_bar.close)
        tk_mid_top = max(tk_mid_same.raw_bar.open, tk_mid_same.raw_bar.close)
        tk_mid_bottom = min(tk_mid_same.raw_bar.open, tk_mid_same.raw_bar.close)

        # 增长判定
        if tk_end.mark == Mark.G:
            growth_ok = tk_end.price > tk_mid_same.price and tk_end_top > tk_mid_bottom
        else:
            growth_ok = tk_end.price < tk_mid_same.price and tk_end_bottom < tk_mid_top

        # 势能优胜判定 (Discriminator)
        ma5_is_better = False
        if mid_ma5 is not None and end_ma5 is not None:
            ma5_is_better = (end_ma5 > mid_ma5) if tk_end.mark == Mark.G else (end_ma5 < mid_ma5)

        # 引力锁定判定
        ma5_gravity_ok = (min(path_ma5) >= start_ma5) if tk_end.mark == Mark.G else (max(path_ma5) <= start_ma5)

        # ---------------------------------------------------------------------
        # 分支逻辑：更优就法则二（重心优先），否则法则一（边界优先）
        # ---------------------------------------------------------------------
        if ma5_is_better:
            return growth_ok and ma5_gravity_ok
        else:
            return growth_ok

    def _execute_leap_collapse(self, anchor_idx: int, new_end_idx: int,
                                ghost_range: tuple):
        """执行时空塌陷：将幽灵节点写入 ghost_forks，重建 turning_ks

        重建规则（以保留 new_end 之后节点为核心）：
          P1 (n1→n4): [..., n0, n1, n2, n3, n4] → [..., n0, n1, n4]
          P2 (n0→n3): [..., nm1, n0, n1, n2, n3, n4] → [..., nm1, n0, n3, n4]
          P3 (nm1→n2):turning_ks [..., nm1, n0, n1, n2, n3, n4] → [..., nm1, n2, n3, n4]

        Args:
            anchor_idx:  跃迁起点锚点在 turning_ks 中的列表索引
            new_end_idx: 新终点在 turning_ks 中的列表索引
            ghost_range: 被塌陷节点的列表索引范围 (start, end)，含两端
        """
        s = self.state

        tk_anchor  = s.turning_ks[anchor_idx]
        tk_new_end = s.turning_ks[new_end_idx]

        # 收集幽灵节点
        g_start, g_end = ghost_range
        ghost_nodes = [
            s.turning_ks[i] for i in range(g_start, g_end + 1)
            if 0 <= i < len(s.turning_ks)
        ]
        if not ghost_nodes:
            return

        # 写入 ghost_forks
        s.ghost_forks.append((
            tk_anchor,
            sorted(ghost_nodes, key=lambda t: t.k_index)
        ))

        # 重建 turning_ks：保留锚点之前（含锚点），追加新终点，再保留新终点之后的节点
        new_turning_ks = s.turning_ks[:anchor_idx + 1]
        new_turning_ks.append(tk_new_end)
        # 保留 new_end_idx 之后的节点（P2/P3 时 n4 等后续节点仍然有效）
        for i in range(new_end_idx + 1, len(s.turning_ks)):
            new_turning_ks.append(s.turning_ks[i])
        s.turning_ks = new_turning_ks

        # 清除新终点的疑假标记
        tk_new_end.maybe_is_fake = False

        # 重新打 is_locked
        if len(s.turning_ks) >= 3:
            s.turning_ks[-3].is_locked = True
            s.turning_ks[-2].is_locked = True  # 即 tk_anchor

        # 更新线段起点极值
        s.segment_start_extreme = tk_anchor.price

        # 同步线段与趋势状态
        self._fractal_engine._update_segments()
        self._trend_engine.update_trend_state(tk_new_end)

    # =========================================================================
    # 中枢重播引擎
    # =========================================================================

    def _replay_center_engine_for_segment(self, start_ext_idx: int, start_trig_idx: int,
                                           current_end_idx: int, correct_direction: Direction):
        """发生线段结构变化时，回滚并重播正确的方向，找回被遗漏的中枢，冻结逆势幽灵中枢"""
        s = self.state

        # Step 1: 精准清理（拔根与留种）
        new_potential = []
        for center in s.potential_centers:
            if center.start_k_index >= start_ext_idx:
                if center.direction != correct_direction:
                    center.is_ghost = True
                    new_potential.append(center)
            else:
                new_potential.append(center)
        s.potential_centers = new_potential

        new_all = []
        for center in s.all_centers:
            if center.start_k_index >= start_ext_idx:
                if center.direction != correct_direction:
                    center.is_ghost = True
                    new_all.append(center)
            else:
                new_all.append(center)
        s.all_centers = new_all

        # Step 2: 状态机洗盘重置
        self._center_engine.rollback()

        # 重置叹息之墙
        last_valid_end_idx = -1
        combined_centers = s.all_centers + s.potential_centers
        for center in reversed(combined_centers):
            if center.start_k_index < start_ext_idx and not getattr(center, 'is_ghost', False):
                last_valid_end_idx = center.end_k_index
                break
        s.last_center_end_idx = last_valid_end_idx

        # Step 3: 时空重播（找回被忽略的真中枢）
        segment_boundary_idx = s.turning_ks[-1].k_index

        for i in range(start_ext_idx, current_end_idx + 1):
            bar = s.bars_raw[i]
            if i <= segment_boundary_idx:
                self._center_engine.update(bar, i, force_direction=correct_direction,
                                           force_anchor_idx=start_ext_idx,
                                           force_trigger_idx=start_trig_idx)
                if i == segment_boundary_idx:
                    self._center_engine.seal_on_boundary()
            else:
                self._center_engine.update(bar, i)
            

    # =========================================================================
    # 属性代理
    # =========================================================================

    @property
    def turning_ks(self) -> List[TurningK]:
        return self.state.turning_ks

    @property
    def segments(self) -> List[MooreSegment]:
        return self.state.segments

    @property
    def all_centers(self) -> List[MooreCenter]:
        return self.state.all_centers + self.state.potential_centers

    @property
    def potential_centers(self) -> List[MooreCenter]:
        return self.state.potential_centers

    @property
    def ghost_forks(self) -> List[tuple]:
        return self.state.ghost_forks

    @property
    def refreshed_segments(self) -> list:
        return self.state.refreshed_segments

    @property
    def candidate_tk(self) -> Optional[TurningK]:
        return self.state.candidate_tk

    @property
    def trend_state(self) -> Optional[Direction]:
        return self.state.trend_state

    @property
    def trend_high(self) -> Optional[float]:
        return self.state.trend_high

    @property
    def trend_low(self) -> Optional[float]:
        return self.state.trend_low

    # -------------------------------------------------------------------------
    # 调试属性
    # -------------------------------------------------------------------------

    @property
    def _debug_rule_fail(self) -> dict:
        return self.state.debug_rule_fail

    @property
    def _debug_trigger_count(self) -> int:
        return self.state.debug_trigger_count

    @property
    def _debug_body_filter(self) -> int:
        return self.state.debug_body_filter

    def _check_actual_perfection(self, tk_start: Optional[TurningK], tk_end: TurningK) -> bool:
        """执行实时的结构完善性检查（Rule 3 的 Live 版本，供宏观审判层调用）

        判定标准与 FractalEngine._validate_four_rules 中的 Rule 3 完全对齐：
        A. 已固化中枢（all_centers + potential_centers）：起点落在段内即保护。
        B. 活跃中枢（State 2）：须"名分已立 AND 黑K已过"才构成保护。
           -- is_visible 定性不在此处判断，等固化后由 MooreCenter.is_visible 决定。
        """
        if tk_start is None or tk_end is None:
            return False

        s = self.state
        start_idx = tk_start.k_index
        end_idx   = tk_end.k_index

        # A. 扫描已存在的各种中枢
        all_c = s.all_centers + s.potential_centers
        for c in all_c:
            if start_idx <= c.start_k_index <= end_idx and not getattr(c, 'is_ghost', False):
                return True

        # B. 扫描正在孵化的活动中枢（仅限已确权：名分 + 黑K）
        # 时间截止锚：中枢确认K必须严格早于被评估的候选终点，防止跨时序污染
        if s.center_state >= 2 and s.center_line_k:
            is_confirmed = (s.center_method_found is not None and s.center_black_k_pass)
            if is_confirmed and s.center_line_k_index <= end_idx:
                c_start = s.center_start_k_index
                if start_idx <= c_start <= end_idx:
                    return True

        return False
