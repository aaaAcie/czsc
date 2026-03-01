# -*- coding: utf-8 -*-
"""
摩尔缠论（Moore CZSC）顶层门面

MooreCZSC 是对外的统一入口，随能力增长持续组合新的子分析器。
当前实现：
  - segment_analyzer：30分钟线段级分析（SegmentAnalyzer）

未来扩展：
  - higher_analyzer：高级别线段中枢分析（消费 segments，独立逻辑）

外部调用者（如 sz500_moore_plot.py）通过属性代理访问结果，
无需感知内部由哪个子分析器提供数据。
"""
from typing import List, Optional
from czsc.py.objects import RawBar
from czsc.py.enum import Direction

from .segment import SegmentAnalyzer
from .objects import TurningK, MooreCenter, MooreSegment


class MooreCZSC:
    """摩尔缠论顶层门面

    组合各级别子分析器，对外暴露统一、稳定的访问接口。
    """

    def __init__(self, bars: List[RawBar], max_segments: int = 500):
        # --- 30分钟线段级 ---
        self.segment_analyzer = SegmentAnalyzer(
            bars=bars,
            max_segments=max_segments,
        )

        # --- 高级别（未来）---
        # self.higher_analyzer = HigherAnalyzer(self.segment_analyzer)

    def update(self, bar: RawBar):
        """流式喂入一根新 K 线，驱动所有子分析器"""
        self.segment_analyzer.update(bar)
        # 未来：self.higher_analyzer.update(self.segment_analyzer.segments)

    # =========================================================================
    # 属性代理：将 segment_analyzer 的结果透传，维持对外接口不变
    # =========================================================================

    @property
    def turning_ks(self) -> List[TurningK]:
        return self.segment_analyzer.turning_ks

    @property
    def segments(self) -> List[MooreSegment]:
        return self.segment_analyzer.segments

    @property
    def all_centers(self) -> List[MooreCenter]:
        """包含历史、潜在、以及当前正在生长的所有活性中枢"""
        return self.segment_analyzer.all_available_centers

    @property
    def potential_centers(self) -> List[MooreCenter]:
        return self.segment_analyzer.potential_centers

    @property
    def ghost_forks(self) -> List[tuple]:
        return self.segment_analyzer.ghost_forks

    @property
    def refreshed_segments(self) -> list:
        return self.segment_analyzer.refreshed_segments

    @property
    def candidate_tk(self) -> Optional[TurningK]:
        return self.segment_analyzer.candidate_tk

    @property
    def trend_state(self) -> Optional[Direction]:
        return self.segment_analyzer.trend_state

    @property
    def trend_high(self) -> Optional[float]:
        return self.segment_analyzer.trend_high

    @property
    def trend_low(self) -> Optional[float]:
        return self.segment_analyzer.trend_low

    # -------------------------------------------------------------------------
    # 调试属性（与旧 MooreCZSC 保持向后兼容）
    # -------------------------------------------------------------------------

    @property
    def _debug_rule_fail(self) -> dict:
        return self.segment_analyzer._debug_rule_fail

    @property
    def _debug_trigger_count(self) -> int:
        return self.segment_analyzer._debug_trigger_count

    @property
    def _debug_body_filter(self) -> int:
        return self.segment_analyzer._debug_body_filter
