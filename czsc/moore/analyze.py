# -*- coding: utf-8 -*-
"""
摩尔缠论（Moore CZSC）顶层门面

MooreCZSC 是对外的统一入口，随能力增长持续组合新的子分析器。
当前实现：
  - segment_analyzer：30分钟线段级分析（SegmentAnalyzer）
  - daily_segment_analyzer：日线级别线段分析（消费宏观 segments，独立逻辑）

外部调用者（如 sz500_moore_plot.py）通过属性代理访问结果，
无需感知内部由哪个子分析器提供数据。
"""
from typing import List, Optional
from czsc.py.objects import RawBar
from czsc.py.enum import Direction

from .segment import SegmentAnalyzer
from .daily_segment import DailySegmentAnalyzer, DailySegmentCenter, DailySegment
from .objects import TurningK, MooreCenter, MooreSegment


class MooreCZSC:
    """摩尔缠论顶层门面

    组合各级别子分析器，对外暴露统一、稳定的访问接口。
    """

    def __init__(
        self,
        bars: List[RawBar],
        max_segments: int = 500,
        use_left_3k_locator: bool = True,
        ma34_cross_as_valid_gate: bool = True,
        ma34_cross_expand_one_k: bool = True,
        audit_link_rounds: int = 5,
        enable_macro_audit: bool = True,
        enable_pre_round: bool = True,
        replay_centers_after_macro_swallow: bool = True,
    ):
        # --- 30分钟线段级 ---
        self.segment_analyzer = SegmentAnalyzer(
            bars=bars,
            max_segments=max_segments,
            use_left_3k_locator=use_left_3k_locator,
            ma34_cross_as_valid_gate=ma34_cross_as_valid_gate,
            ma34_cross_expand_one_k=ma34_cross_expand_one_k,
            audit_link_rounds=audit_link_rounds,
            enable_macro_audit=enable_macro_audit,
            enable_pre_round=enable_pre_round,
            replay_centers_after_macro_swallow=replay_centers_after_macro_swallow,
        )

        # --- 日线级别线段 ---
        self.daily_segment_analyzer = DailySegmentAnalyzer(
            self.segment_analyzer.segments,
            bars=self.segment_analyzer.state.bars_raw,
            micro_segments=self.segment_analyzer.micro_segments,
        )

    def update(self, bar: RawBar):
        """流式喂入一根新 K 线，驱动所有子分析器"""
        self.segment_analyzer.update(bar)
        self.daily_segment_analyzer.update(
            self.segment_analyzer.segments,
            bars=self.segment_analyzer.state.bars_raw,
            micro_segments=self.segment_analyzer.micro_segments,
        )

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
    def micro_turning_ks(self) -> List[TurningK]:
        return self.segment_analyzer.micro_turning_ks

    @property
    def micro_segments(self) -> List[MooreSegment]:
        return self.segment_analyzer.micro_segments

    @property
    def all_centers(self) -> List[MooreCenter]:
        return self.segment_analyzer.all_centers

    @property
    def potential_centers(self) -> List[MooreCenter]:
        return self.segment_analyzer.potential_centers

    @property
    def micro_centers(self) -> List[MooreCenter]:
        return self.segment_analyzer.micro_centers

    @property
    def macro_centers(self) -> List[MooreCenter]:
        return self.segment_analyzer.macro_centers

    @property
    def ghost_centers(self) -> List[MooreCenter]:
        return self.segment_analyzer.ghost_centers

    @property
    def ghost_forks(self) -> List[tuple]:
        return self.segment_analyzer.ghost_forks

    @property
    def micro_ghost_forks(self) -> List[tuple]:
        return self.segment_analyzer.micro_ghost_forks

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

    @property
    def last_ma5(self) -> Optional[float]:
        return self.segment_analyzer.state.last_ma5

    @property
    def daily_segments(self) -> List[DailySegment]:
        return self.daily_segment_analyzer.daily_segments

    @property
    def daily_pending_segments(self) -> List[DailySegment]:
        return self.daily_segment_analyzer.daily_pending_segments

    @property
    def daily_non_same_segments(self) -> List[DailySegment]:
        return self.daily_segment_analyzer.daily_non_same_segments

    @property
    def daily_active_center(self) -> Optional[DailySegmentCenter]:
        return self.daily_segment_analyzer.active_center

    @property
    def daily_centers(self) -> List[DailySegmentCenter]:
        return self.daily_segment_analyzer.daily_centers

    @property
    def daily_pending_centers(self) -> List[DailySegmentCenter]:
        return self.daily_segment_analyzer.daily_pending_centers

    @property
    def daily_center_source_segments(self) -> List[MooreSegment]:
        return self.daily_segment_analyzer.daily_center_source_segments

    @property
    def daily_refined_segments(self) -> List[MooreSegment]:
        return self.daily_segment_analyzer.refined_segments + self.daily_segment_analyzer.pending_refined_segments

    @property
    def daily_archived_centers(self) -> List[DailySegmentCenter]:
        return self.daily_segment_analyzer.archived_centers

    @property
    def daily_candidates(self) -> List[DailySegmentCenter]:
        return self.daily_segment_analyzer.candidates

    # -------------------------------------------------------------------------
    # 兼容旧命名：higher_* 继续转发到 daily_*
    # -------------------------------------------------------------------------

    @property
    def higher_analyzer(self) -> DailySegmentAnalyzer:
        return self.daily_segment_analyzer

    @property
    def higher_segments(self) -> List[DailySegment]:
        return self.daily_segments

    @property
    def higher_active_center(self) -> Optional[DailySegmentCenter]:
        return self.daily_active_center

    @property
    def higher_archived_centers(self) -> List[DailySegmentCenter]:
        return self.daily_archived_centers

    @property
    def higher_candidates(self) -> List[DailySegmentCenter]:
        return self.daily_candidates

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

    @property
    def _debug_pending_judgements(self) -> list:
        return self.segment_analyzer._debug_pending_judgements
