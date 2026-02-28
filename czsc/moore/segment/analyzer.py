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
    penetration_level: int = 2  # 1=保守 / 2=常规 / 3=激进

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
    last_center_end_idx: int                = -1     # 记录上一个固化中枢的破窗 K 线索引
    escape_bars: list                       = field(default_factory=list) # 记录连续脱离中枢结界的K线缓存（脱轨缓冲区）


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

    def __init__(self, bars: List[RawBar], max_segments: int = 500, penetration_level: int = 2):
        # 共享状态容器
        self.state = SegmentState(
            max_segments=max_segments,
            penetration_level=penetration_level,
        )

        # MA 滑窗（仅由 SegmentAnalyzer 维护，不放入 state）
        self._ma5_q  = collections.deque(maxlen=5)
        self._ma34_q = collections.deque(maxlen=34)

        # 三个子引擎（共享同一个 state）
        self._trend_engine  = TrendEngine(self.state)
        self._center_engine = CenterEngine(self.state)
        self._fractal_engine = FractalEngine(self.state, self._trend_engine, self._center_engine)

        # 批量喂入历史数据
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

        # 喂入均线管道
        self._ma5_q.append(bar.close)
        self._ma34_q.append(bar.close)

        # 冷启动判断：MA34 未充盈时，仅累积数据，所有状态机静默
        if len(self._ma34_q) < 34:
            return

        current_ma5  = sum(self._ma5_q)  / 5
        current_ma34 = sum(self._ma34_q) / 34

        # 将指标写入 bar 的缓存，方便引擎回溯读取
        bar.cache['ma5']  = current_ma5
        bar.cache['ma34'] = current_ma34

        # --- 2. 中枢引擎（先于顶底引擎，采集潜在中枢）---
        self._center_engine.update(bar, k_index)

        # --- 3. 顶底确立游标引擎 ---
        old_ghost_len = len(s.ghost_forks)
        old_tk_len = len(s.turning_ks)
        old_last_tk_dt = s.turning_ks[-1].dt if s.turning_ks else None

        self._fractal_engine.update(bar, k_index, current_ma5)

        new_tk_len = len(s.turning_ks)
        new_last_tk_dt = s.turning_ks[-1].dt if s.turning_ks else None

        # 触发重播的条件：
        # 1. 发生了趋势穿透吞噬（ghost_forks 增加）
        # 2. 发生了同向刷新（长度不变，但最后一个 tk 变了）
        # 3. 新确立了反向线段（长度增加。确立时，以这整个新线段的起点进行绝对重播，清理掉期间基于假 candidate_tk 产生的相反中枢）
        is_ghost_added = (len(s.ghost_forks) > old_ghost_len)
        is_same_refresh = (old_tk_len > 0 and new_tk_len == old_tk_len and old_last_tk_dt != new_last_tk_dt)
        is_new_segment = (new_tk_len > old_tk_len and new_tk_len >= 2)

        if is_ghost_added or is_same_refresh or is_new_segment:
            if len(s.turning_ks) >= 2:
                # 重播的起点：当前最新线段的极值点与转折确认点
                tk_start = s.turning_ks[-2]
                real_start_idx = tk_start.k_index
                real_trig_idx = tk_start.trigger_k_index if tk_start.trigger_k_index is not None else tk_start.k_index
                correct_direction = Direction.Up if s.turning_ks[-1].mark == Mark.G else Direction.Down
                self._replay_center_engine_for_segment(real_start_idx, real_trig_idx, k_index, correct_direction)

        # 游标步进
        s.last_ma5 = current_ma5

    # =========================================================================
    # 属性代理（让外部访问 state 里的结果，同时保持接口简洁）
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
    # 调试属性（与旧 MooreCZSC 保持兼容）
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

    @property
    def penetration_level(self) -> int:
        return self.state.penetration_level

    @penetration_level.setter
    def penetration_level(self, value: int):
        self.state.penetration_level = value

    def _replay_center_engine_for_segment(self, start_ext_idx: int, start_trig_idx: int, current_end_idx: int, correct_direction: Direction):
        """发生线段吞噬时，回滚并重播正确的方向，找回被遗漏的中枢，冻结逆势幽灵中枢"""
        s = self.state

        # Step 1: 精准清理（拔根与留种）
        new_potential = []
        for center in s.potential_centers:
            if center.start_k_index >= start_ext_idx:
                if center.direction != correct_direction:
                    center.is_ghost = True
                    new_potential.append(center)
                else:
                    pass
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
                    pass
            else:
                new_all.append(center)
        s.all_centers = new_all

        # Step 2: 状态机洗盘重置
        self._center_engine.rollback()

        # 重置叹息之墙: 找到最后一个在 start_ext_idx 之前且 is_ghost == False 的中枢
        last_valid_end_idx = -1
        combined_centers = s.all_centers + s.potential_centers
        for center in reversed(combined_centers):
            if center.start_k_index < start_ext_idx and not getattr(center, 'is_ghost', False):
                last_valid_end_idx = center.end_k_index
                break
        s.last_center_end_idx = last_valid_end_idx

        # Step 3: 开启时空重播（找回被忽略的真中枢）
        # 线段的真正的物理分水岭（山顶/山谷）
        segment_boundary_idx = s.turning_ks[-1].k_index

        for i in range(start_ext_idx, current_end_idx + 1):
            bar = s.bars_raw[i]
            
            if i <= segment_boundary_idx:
                # 1. 确定性的线段时空：强制干预方向和锚点
                self._center_engine.update(bar, i, force_direction=correct_direction, 
                                          force_anchor_idx=start_ext_idx, force_trigger_idx=start_trig_idx)
                
                # 2. 到达分水岭：执行物理截断
                if i == segment_boundary_idx:
                    self._center_engine.seal_on_boundary()
            else:
                # 3. 尚未确定的未来时空（尾部）：恢复自由身
                self._center_engine.update(bar, i)
        
        return
