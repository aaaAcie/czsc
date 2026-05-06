# -*- coding: utf-8 -*-
"""日线级别线段分析器。"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Sequence

from czsc.py.enum import Direction

from ..objects import MooreSegment
from .center_algo import find_center
from .objects import DailySegment, DailySegmentCenter
from .state import DailySegmentState
from .utils import (
    build_sma_array,
    clone_completed_segments_snapshot,
    collect_bars_by_index,
    safe_ma_value,
    seg_end_index,
    seg_end_price,
    seg_start_index,
    seg_start_price,
    slice_segments_from_anchor,
)


class DailySegmentAnalyzer:
    """消费 30F 宏观线段构造日线级别线段与中枢。"""

    def __init__(self, segments: Optional[Sequence[MooreSegment]] = None):
        self.state = DailySegmentState()
        if segments:
            self.update(list(segments))

    def update(self, segments: Sequence[MooreSegment]):
        s = self.state
        sig = tuple((seg.sdt, seg.edt, bool(seg.cache.get("is_macro_swallow"))) for seg in segments)
        if sig == s.last_sig:
            return
        s.last_sig = sig
        s.base_segments = list(segments)
        self._rebuild()

    @property
    def daily_segments(self) -> List[DailySegment]:
      # 末段未终结 Higher 线段的运行态输出
      # return self.state.completed_segments
        res = list(self.state.completed_segments)
        if self.state.current_segments:
            s = self.state
            running_seg = DailySegment(
                symbol=s.current_segments[0].symbol,
                direction=s.current_segments[0].direction,
                start_seg=s.current_segments[0],
                end_seg=s.current_segments[-1],
                segments=list(s.current_segments),
                centers=[c for c in s.candidates if c.is_active],
                cache={"is_live": True}
            )
            res.append(running_seg)
        return res

    @property
    def current_segments(self) -> List[MooreSegment]:
        return self.state.current_segments

    @property
    def active_center(self) -> Optional[DailySegmentCenter]:
        return self.state.active_center

    @property
    def archived_centers(self) -> List[DailySegmentCenter]:
        return self.state.archived_centers

    @property
    def candidates(self) -> List[DailySegmentCenter]:
        return self.state.candidates

    # 兼容旧命名
    @property
    def higher_segments(self) -> List[DailySegment]:
        return self.daily_segments

    def _rebuild(self):
        s = self.state
        bars_by_index = collect_bars_by_index(s.base_segments)
        s.ma34 = build_sma_array(bars_by_index, window=34)
        s.ma170 = build_sma_array(bars_by_index, window=170)

        danger, should_fallback = slice_segments_from_anchor(
            s.base_segments, s.anchor_k_index, s.anchor_dt
        )
        if s.anchor_k_index is None or should_fallback or danger is None:
            self._full_rebuild_from_scratch()
            return

        self._restore_from_anchor_snapshot()
        for seg in danger:
            self._process_new_segment(seg)

    def _full_rebuild_from_scratch(self):
        s = self.state
        s.current_segments = []
        s.completed_segments = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []
        s.pending_break = None
        s.anchor_k_index = None
        s.anchor_dt = None
        s.anchor_completed_segments = []
        s.pending_anchor_snapshot = False

        for seg in s.base_segments:
            self._process_new_segment(seg)

    def _restore_from_anchor_snapshot(self):
        s = self.state
        s.completed_segments = clone_completed_segments_snapshot(s.anchor_completed_segments)
        s.current_segments = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []
        s.pending_break = None

    def _reset_runtime_state(self):
        s = self.state
        s.current_segments = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []
        s.pending_break = None

    def _process_new_segment(self, new_seg: MooreSegment):
        s = self.state

        if new_seg.cache.get("is_macro_swallow"):
            if s.current_segments:
                self._commit_current_running_epoch_if_needed()
            self._commit_swallow_segment_directly(new_seg)
            return

        if not s.current_segments and s.pending_anchor_snapshot:
            self._advance_anchor_snapshot(new_seg)

        if s.pending_break:
            if self._verify_independence(new_seg):
                self._commit_and_reset_system(new_seg)
                return
            s.pending_break = None

        if self._is_trend_broken_by(new_seg):
            self._commit_and_reset_system(new_seg)
            return

        s.current_segments.append(new_seg)

        if len(s.current_segments) == 1 and s.anchor_k_index is None:
            self._advance_anchor_snapshot(new_seg)

        if s.active_center and len(s.current_segments) >= 2:
            daily_dir = s.current_segments[0].direction
            if new_seg.direction != daily_dir:
                self._evaluate_rebound_and_escape(new_seg)
                if s.pending_break:
                    return

        self._try_spawn_new_candidate()
        self._update_candidates_and_resolve_conflicts()

    def _is_trend_broken_by(self, new_seg: MooreSegment) -> bool:
        s = self.state
        if not s.current_segments:
            return False
        daily_dir = s.current_segments[0].direction
        start_price = seg_start_price(s.current_segments[0])
        end_price = seg_end_price(new_seg)

        if daily_dir == Direction.Up and new_seg.direction == Direction.Down:
            return end_price < start_price
        if daily_dir == Direction.Down and new_seg.direction == Direction.Up:
            return end_price > start_price
        return False

    def _verify_independence(self, new_seg: MooreSegment) -> bool:
        s = self.state
        if not s.pending_break:
            return False
        target_dir = s.pending_break["expected_direction"]
        extreme_price = s.pending_break["extreme_price"]
        if new_seg.direction != target_dir:
            return False
        end_price = seg_end_price(new_seg)
        if target_dir == Direction.Down:
            return end_price < extreme_price
        return end_price > extreme_price

    def _evaluate_rebound_and_escape(self, rebound_seg: MooreSegment):
        s = self.state
        if not s.active_center:
            return

        target_center = s.active_center
        center_a = s.archived_centers[-1] if s.archived_centers else None
        current_ma170 = safe_ma_value(s.ma170, seg_end_index(rebound_seg))

        if center_a and current_ma170 is not None:
            if rebound_seg.direction == Direction.Up and seg_end_price(rebound_seg) >= current_ma170:
                target_center = center_a
            elif rebound_seg.direction == Direction.Down and seg_end_price(rebound_seg) <= current_ma170:
                target_center = center_a

        if len(s.current_segments) < 2:
            return

        prev_extreme = seg_end_price(s.current_segments[-2])
        rebound_end = seg_end_price(rebound_seg)

        if rebound_seg.direction == Direction.Up and rebound_end < target_center.low:
            s.pending_break = {
                "expected_direction": Direction.Down,
                "extreme_price": prev_extreme,
                "target_center": target_center,
            }
        elif rebound_seg.direction == Direction.Down and rebound_end > target_center.high:
            s.pending_break = {
                "expected_direction": Direction.Up,
                "extreme_price": prev_extreme,
                "target_center": target_center,
            }

    def _update_candidates_and_resolve_conflicts(self):
        s = self.state
        actives = [c for c in s.candidates if c.is_active]
        for c_a in actives:
            for c_b in actives:
                if c_a is c_b:
                    continue
                if c_b.overlap_type == 3 and c_a.overlap_type == 1 and c_a.check_segment_overlap(c_b):
                    c_a.is_active = False

        s.candidates = [c for c in s.candidates if c.is_active]
        type3 = [c for c in s.candidates if c.overlap_type == 3]
        if not type3:
            return

        type3.sort(key=lambda x: (x.start_index, x.end_index))
        chosen = type3[-1]
        if s.active_center and s.active_center is not chosen:
            if s.active_center not in s.archived_centers:
                s.archived_centers.append(s.active_center)
        s.active_center = chosen

    def _try_spawn_new_candidate(self):
        s = self.state
        if len(s.current_segments) < 4:
            return

        result = find_center(s.current_segments, s.ma34)
        if not result:
            return

        high = result["high"]
        low = result["low"]
        overlap_type = result["overlap_type"]
        status = result["status"]
        seg_slice = result["segments"]
        key = (
            seg_start_index(seg_slice[0]),
            seg_end_index(seg_slice[-1]),
            round(high, 8),
            round(low, 8),
        )

        for c in s.candidates:
            if c.cache.get("identity_key") == key:
                if c.status == "TEMPORARY" and status == "FINAL":
                    c.status = status
                    c.high = high
                    c.low = low
                    c.overlap_type = overlap_type
                    c.points = result["points"]
                    c.segments = list(seg_slice)
                return

        s.candidates.append(
            DailySegmentCenter(
                segments=list(seg_slice),
                high=high,
                low=low,
                overlap_type=overlap_type,
                status=status,
                points=result["points"],
                cache={"identity_key": key},
            )
        )

    def _append_completed_segment(self, segment: DailySegment):
        s = self.state
        s.completed_segments.append(segment)
        s.anchor_completed_segments = clone_completed_segments_snapshot(s.completed_segments)

    def _advance_anchor_snapshot(self, next_start_seg: MooreSegment):
        s = self.state
        s.anchor_k_index = next_start_seg.start_k.k_index
        s.anchor_dt = next_start_seg.start_k.dt
        s.anchor_completed_segments = clone_completed_segments_snapshot(s.completed_segments)
        s.pending_anchor_snapshot = False

    def _commit_segments_if_valid(self, segments: Sequence[MooreSegment]) -> bool:
        if (
            len(segments) >= 3
            and self._check_global_trend_relationship(segments)
            and self._check_ma_cross_correlation(segments, self.state.ma34, self.state.ma170)
        ):
            centers = [
                c
                for c in self.state.candidates
                if c.is_active
                and c.start_index >= seg_start_index(segments[0])
                and c.end_index <= seg_end_index(segments[-1])
            ]
            self._append_completed_segment(
                DailySegment(
                    symbol=segments[0].symbol,
                    direction=segments[0].direction,
                    start_seg=segments[0],
                    end_seg=segments[-1],
                    segments=list(segments),
                    centers=centers,
                )
            )
            return True
        return False

    def _commit_current_running_epoch_if_needed(self) -> bool:
        s = self.state
        segments = list(s.current_segments)
        committed = False
        if segments:
            committed = self._commit_segments_if_valid(segments)
        self._reset_runtime_state()
        s.anchor_k_index = None
        s.anchor_dt = None
        s.pending_anchor_snapshot = True
        return committed

    def _commit_and_reset_system(self, break_seg: MooreSegment):
        self._commit_segments_if_valid(list(self.state.current_segments))
        self._reset_runtime_state()
        self.state.current_segments = [break_seg]
        self._advance_anchor_snapshot(break_seg)

    def _commit_swallow_segment_directly(self, new_seg: MooreSegment):
        direct_seg = DailySegment(
            symbol=new_seg.symbol,
            direction=new_seg.direction,
            start_seg=new_seg,
            end_seg=new_seg,
            segments=[new_seg],
            centers=[],
            cache={"from_macro_swallow": True},
        )
        self._append_completed_segment(direct_seg)
        self._reset_runtime_state()
        self.state.anchor_k_index = None
        self.state.anchor_dt = None
        self.state.pending_anchor_snapshot = True

    def _check_global_trend_relationship(self, segments: Sequence[MooreSegment]) -> bool:
        if not segments:
            return False
        daily_dir = segments[0].direction
        start_p = seg_start_price(segments[0])
        end_p = seg_end_price(segments[-1])
        endpoints = []
        for seg in segments:
            endpoints.extend([seg_start_price(seg), seg_end_price(seg)])
        global_max = max(endpoints)
        global_min = min(endpoints)
        if daily_dir == Direction.Up:
            return start_p == global_min and end_p == global_max
        if daily_dir == Direction.Down:
            return start_p == global_max and end_p == global_min
        return False

    def _check_ma_cross_correlation(
        self,
        segments: Sequence[MooreSegment],
        ma_fast,
        ma_slow,
    ) -> bool:
        if not segments:
            return False

        start_idx = seg_start_index(segments[0])
        end_idx = seg_end_index(segments[-1])
        initial_state = 0

        for i in range(start_idx, end_idx + 1):
            if i >= len(ma_fast) or i >= len(ma_slow):
                break
            fast = ma_fast[i]
            slow = ma_slow[i]
            if fast is None or slow is None:
                continue
            if fast > slow:
                initial_state = 1
                break
            if fast < slow:
                initial_state = -1
                break

        if initial_state == 0:
            return False

        for i in range(start_idx, end_idx + 1):
            if i >= len(ma_fast) or i >= len(ma_slow):
                break
            fast = ma_fast[i]
            slow = ma_slow[i]
            if fast is None or slow is None:
                continue
            if fast > slow:
                current_state = 1
            elif fast < slow:
                current_state = -1
            else:
                current_state = 0
            if current_state != 0 and current_state != initial_state:
                return True
        return False


# 兼容旧命名
HigherAnalyzer = DailySegmentAnalyzer
HigherCenter = DailySegmentCenter
HigherSegment = DailySegment
HigherState = DailySegmentState
