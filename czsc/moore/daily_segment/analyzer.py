# -*- coding: utf-8 -*-
"""日线级别线段分析器。"""
from __future__ import annotations

from typing import List, Optional, Sequence

from czsc.py.objects import RawBar

from ..objects import MooreSegment
from .centers.algo import find_center
from .helpers.commit import (
    CommitDecision,
    find_delayed_commit_decision,
    is_valid_regular_window,
    select_valid_daily_window,
    should_commit_leading_swallow,
)
from .helpers.continuity import check_daily_segment_continuity
from .helpers.cold_start import find_cold_start_decision
from .helpers.extension import is_extension_same_trend
from .helpers.ma_cross import (
    check_ma_cross_correlation,
    has_ma_cross_between,
    ma_relation_state_at,
    ma_relation_state_for_tk,
    ma_relation_state_from_values,
    ma_reverses_against_window,
    turning_index,
)
from .helpers.non_same import try_non_same_candidate
from .helpers.trend import check_global_trend_relationship
from .objects import DailySegment, DailySegmentCenter
from .state import DailySegmentState
from .utils import (
    build_sma_array,
    clone_completed_segments_snapshot,
    collect_bars_by_index,
    seg_end_index,
    seg_start_index,
    slice_segments_from_anchor,
)


class DailySegmentAnalyzer:
    """消费 30F 宏观线段构造日线级别线段与中枢。"""

    def __init__(self, segments: Optional[Sequence[MooreSegment]] = None, bars: Optional[Sequence[RawBar]] = None):
        self.state = DailySegmentState()
        if bars:
            self.state.bars_raw = list(bars)
        if segments:
            self.update(list(segments))

    def update(self, segments: Sequence[MooreSegment], bars: Optional[Sequence[RawBar]] = None):
        s = self.state
        sig = tuple(
            (
                seg.start_k.k_index,
                seg.end_k.k_index,
                turning_index(seg.start_k),
                turning_index(seg.end_k),
                bool(seg.cache.get("is_macro_swallow")),
            )
            for seg in segments
        )
        if bars is not None:
            s.bars_raw = list(bars)
        if sig == s.last_sig and bars is None:
            return
        s.last_sig = sig
        s.base_segments = list(segments)
        self._rebuild()

    @property
    def daily_segments(self) -> List[DailySegment]:
        return self.state.completed_segments

    @property
    def active_center(self) -> Optional[DailySegmentCenter]:
        return self.state.active_center

    @property
    def daily_centers(self) -> List[DailySegmentCenter]:
        return self.state.daily_centers

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
        bars_by_index = {i: bar for i, bar in enumerate(s.bars_raw)} if s.bars_raw else collect_bars_by_index(s.base_segments)
        s.ma34 = build_sma_array(bars_by_index, window=34)
        s.ma170 = build_sma_array(bars_by_index, window=170)

        danger, should_fallback = slice_segments_from_anchor(
            s.base_segments, s.anchor_k_index, s.anchor_dt
        )
        if s.anchor_k_index is None or should_fallback or danger is None:
            self._full_rebuild_from_scratch()
            self._rebuild_daily_centers()
            return

        self._restore_from_anchor_snapshot()
        for seg in danger:
            self._process_new_segment(seg)
        self._rebuild_daily_centers()

    def _full_rebuild_from_scratch(self):
        s = self.state
        s.current_segments = []
        s.pending_daily_segments = []
        s.completed_segments = []
        s.daily_centers = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []
        s.anchor_k_index = None
        s.anchor_dt = None
        s.anchor_completed_segments = []
        s.pending_anchor_snapshot = False
        s.continuity_broken = False

        for seg in s.base_segments:
            self._process_new_segment(seg)

    def _is_excluded_from_daily_center(self, seg: MooreSegment) -> bool:
        """Hook for daily-center source filtering.

        Today the only default exclusion is the macro swallow segment that has
        already been promoted into daily-segment evidence.  Callers can extend
        the mouth by setting ``exclude_from_daily_center`` in segment cache.
        """
        return bool(seg.cache.get("exclude_from_daily_center") or seg.cache.get("is_macro_swallow"))

    def _daily_center_source_segments(self) -> List[MooreSegment]:
        return [seg for seg in self.state.base_segments if not self._is_excluded_from_daily_center(seg)]

    def _daily_segment_center_source_segments(self, daily_segment: DailySegment) -> List[MooreSegment]:
        return [seg for seg in daily_segment.segments if not self._is_excluded_from_daily_center(seg)]

    @staticmethod
    def _center_segment_keys(center: DailySegmentCenter) -> set:
        return {(seg_start_index(seg), seg_end_index(seg)) for seg in center.segments}

    @staticmethod
    def _center_generation_rank(center: DailySegmentCenter) -> tuple:
        if center.overlap_type == 3 and center.status == "FINAL":
            center_type_rank = 0
        elif center.overlap_type == 3:
            center_type_rank = 1
        else:
            center_type_rank = 2
        point_indices = [point[0] for point in center.points.values()]
        generation_index = center.cache.get("third_entry_index")
        if generation_index is None:
            generation_index = max(point_indices) if point_indices else center.end_index
        c_index = center.points.get("C", center.points.get("B", (center.start_index, None)))[0]
        return (center_type_rank, generation_index, c_index, center.start_index, center.low, center.high)

    @staticmethod
    def _third_segment_entry_index(result: dict, ma_array) -> Optional[int]:
        if result.get("overlap_type") != 3:
            return None
        points = result.get("points") or {}
        if not {"A", "B", "C", "D"} <= set(points):
            return None
        a_val = points["A"][1]
        b_val = points["B"][1]
        c_idx = points["C"][0]
        d_idx = points["D"][0]
        low = min(a_val, b_val)
        high = max(a_val, b_val)
        for idx in range(c_idx, d_idx + 1):
            if idx >= len(ma_array):
                break
            ma_val = ma_array[idx]
            if ma_val is not None and low < ma_val < high:
                return idx
        return d_idx

    def _dedupe_overlapping_daily_centers(self, centers: Sequence[DailySegmentCenter]) -> List[DailySegmentCenter]:
        selected: List[DailySegmentCenter] = []
        used_keys = set()
        for center in sorted(centers, key=self._center_generation_rank):
            keys = self._center_segment_keys(center)
            if keys & used_keys:
                continue
            selected.append(center)
            used_keys.update(keys)
        return selected

    def _rebuild_daily_centers(self):
        s = self.state
        centers: List[DailySegmentCenter] = []
        seen = set()
        for daily_segment in s.completed_segments:
            source_segments = self._daily_segment_center_source_segments(daily_segment)
            for start in range(max(0, len(source_segments) - 3)):
                result = find_center(source_segments[start:], s.ma34, trend_direction=daily_segment.direction)
                if not result:
                    continue
                seg_slice = result["segments"]
                key = (
                    seg_start_index(seg_slice[0]),
                    seg_end_index(seg_slice[-1]),
                    round(result["high"], 8),
                    round(result["low"], 8),
                    result["overlap_type"],
                    result["status"],
                )
                if key in seen:
                    continue
                seen.add(key)
                centers.append(
                    DailySegmentCenter(
                        segments=list(seg_slice),
                        high=result["high"],
                        low=result["low"],
                        overlap_type=result["overlap_type"],
                        status=result["status"],
                        points=result["points"],
                        cache={
                            "identity_key": key,
                            "source": "daily_segment_internal",
                            "daily_segment_direction": daily_segment.direction.value,
                            "generation_index": max(point[0] for point in result["points"].values()),
                            "third_entry_index": self._third_segment_entry_index(result, s.ma34),
                            "excluded_reasons": ("is_macro_swallow", "exclude_from_daily_center"),
                        },
                    )
                )

        type3 = [c for c in centers if c.overlap_type == 3]
        for c in centers:
            if c.overlap_type == 1 and any(c.check_segment_overlap(other) for other in type3):
                c.is_active = False
        s.daily_centers = self._dedupe_overlapping_daily_centers([c for c in centers if c.is_active])

    def _restore_from_anchor_snapshot(self):
        s = self.state
        s.completed_segments = clone_completed_segments_snapshot(s.anchor_completed_segments)
        s.current_segments = []
        s.pending_daily_segments = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []
        s.continuity_broken = False

    def _reset_runtime_state(self):
        s = self.state
        s.current_segments = []
        s.pending_daily_segments = []
        s.active_center = None
        s.archived_centers = []
        s.candidates = []

    def _process_new_segment(self, new_seg: MooreSegment):
        s = self.state

        if not s.current_segments and s.pending_anchor_snapshot:
            self._advance_anchor_snapshot(new_seg)

        s.current_segments.append(new_seg)

        if len(s.current_segments) == 1 and s.anchor_k_index is None:
            self._advance_anchor_snapshot(new_seg)

        self._try_spawn_new_candidate()
        self._update_candidates_and_resolve_conflicts()
        self._commit_ready_daily_segments()

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
        self._upsert_center_candidate(result)

    def _upsert_center_candidate(self, result: dict):
        s = self.state
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
        valid_segments = self._select_valid_daily_window(segments)
        if valid_segments:
            self._append_daily_segment(valid_segments)
            return True
        return False

    def _append_daily_segment(self, segments: Sequence[MooreSegment]):
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
                cache={"from_macro_swallow": True} if len(segments) == 1 and segments[0].cache.get("is_macro_swallow") else {},
            )
        )
        self.state.continuity_broken = False

    def _select_valid_daily_window(
        self,
        segments: Sequence[MooreSegment],
        previous_end_k=None,
    ) -> List[MooreSegment]:
        return select_valid_daily_window(
            segments,
            self.state.completed_segments,
            self.state.ma34,
            self.state.ma170,
            continuity_broken=self.state.continuity_broken,
            previous_end_k=previous_end_k,
        )

    def _is_valid_daily_window(
        self,
        window: Sequence[MooreSegment],
        next_seg: Optional[MooreSegment] = None,
        previous_end_k=None,
    ) -> bool:
        return is_valid_regular_window(
            window,
            self.state.ma34,
            self.state.ma170,
            lag_segment=next_seg,
            completed_segments=self.state.completed_segments,
            previous_end_k=previous_end_k,
        )

    def _commit_ready_daily_segments(self):
        s = self.state
        while True:
            decision = self._find_commit_decision(s.current_segments)
            if not decision:
                return
            if not decision.segments:
                s.pending_daily_segments = decision.pending_segments
                return

            tail = list(s.current_segments[decision.next_tail_offset:])
            self._append_daily_segment(decision.segments)
            s.current_segments = tail
            s.pending_daily_segments = []
            self._reset_daily_center_state()
            if s.current_segments:
                self._advance_anchor_snapshot(s.current_segments[0])
                self._rebuild_candidates_for_current_segments()
            else:
                s.anchor_k_index = None
                s.anchor_dt = None
                s.pending_anchor_snapshot = True

    def _find_commit_decision(self, segments: Sequence[MooreSegment]) -> Optional[CommitDecision]:
        if self.state.continuity_broken:
            return None
        if not self.state.completed_segments and len(segments) >= 3:
            return find_cold_start_decision(segments, self.state.ma34, self.state.ma170)
        if self.state.completed_segments and should_commit_leading_swallow(
            segments,
            self.state.completed_segments,
            self.state.ma34,
            self.state.ma170,
        ):
            return CommitDecision(0, 1, [segments[0]], [])
        if len(segments) < 3:
            return None
        return find_delayed_commit_decision(
            segments,
            self.state.completed_segments,
            self.state.ma34,
            self.state.ma170,
            continuity_broken=self.state.continuity_broken,
            allow_cold_start=False,
        )

    def _find_confirmed_daily_window(self, segments: Sequence[MooreSegment]) -> List[MooreSegment]:
        decision = self._find_commit_decision(segments)
        if not decision:
            self.state.pending_daily_segments = []
            return []
        self.state.pending_daily_segments = decision.pending_segments
        return decision.segments

    def _try_non_same_processing(self, segments: Sequence[MooreSegment]) -> List[MooreSegment]:
        """反向趋势以虚线 30F 线段开头时，尝试 idx-1 到 idx+2 直连。"""
        if len(segments) < 3 or not self.state.completed_segments:
            return []
        if not self._check_daily_segment_continuity(segments[:3]):
            return []
        return try_non_same_candidate(segments, self.state.completed_segments[-1].direction, self.state.ma34)

    def _ma34_reverses_against_window(self, window: Sequence[MooreSegment]) -> bool:
        return ma_reverses_against_window(window, self.state.ma34)

    @staticmethod
    def _is_extension_same_trend(window: Sequence[MooreSegment], next_two: Sequence[MooreSegment]) -> bool:
        return is_extension_same_trend(window, next_two)

    def _reset_daily_center_state(self):
        s = self.state
        s.active_center = None
        s.archived_centers = []
        s.candidates = []

    def _rebuild_candidates_for_current_segments(self):
        self.state.candidates = []
        for i in range(4, len(self.state.current_segments) + 1):
            result = find_center(self.state.current_segments[:i], self.state.ma34)
            if not result:
                continue
            self._upsert_center_candidate(result)
        self._update_candidates_and_resolve_conflicts()

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
        if segments and not committed and s.completed_segments:
            s.continuity_broken = True
        return committed

    def _commit_swallow_segment_directly(self, new_seg: MooreSegment):
        if not self._check_daily_segment_continuity([new_seg]):
            if self.state.completed_segments:
                self.state.continuity_broken = True
            self._reset_runtime_state()
            return
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
        self.state.continuity_broken = False
        self._reset_runtime_state()
        self.state.anchor_k_index = None
        self.state.anchor_dt = None
        self.state.pending_anchor_snapshot = True

    def _check_daily_segment_continuity(self, segments: Sequence[MooreSegment], previous_end_k=None) -> bool:
        return check_daily_segment_continuity(
            segments,
            self.state.completed_segments,
            previous_end_k=previous_end_k,
        )

    def _check_global_trend_relationship(self, segments: Sequence[MooreSegment]) -> bool:
        return check_global_trend_relationship(segments)

    def _check_ma_cross_correlation(
        self,
        segments: Sequence[MooreSegment],
        ma_fast,
        ma_slow,
        lag_segment: Optional[MooreSegment] = None,
    ) -> bool:
        return check_ma_cross_correlation(segments, ma_fast, ma_slow, lag_segment)

    @classmethod
    def _has_ma_cross_between(cls, start_idx: int, end_idx: int, ma_fast, ma_slow) -> bool:
        return has_ma_cross_between(start_idx, end_idx, ma_fast, ma_slow)

    @staticmethod
    def _turning_index(tk) -> int:
        return turning_index(tk)

    @classmethod
    def _ma_relation_state_for_tk(cls, tk, ma_fast, ma_slow) -> int:
        return ma_relation_state_for_tk(tk, ma_fast, ma_slow)

    @staticmethod
    def _ma_relation_state_at(idx: int, ma_fast, ma_slow) -> int:
        return ma_relation_state_at(idx, ma_fast, ma_slow)

    @staticmethod
    def _ma_relation_state_from_values(fast, slow) -> int:
        return ma_relation_state_from_values(fast, slow)


# 兼容旧命名
HigherAnalyzer = DailySegmentAnalyzer
HigherCenter = DailySegmentCenter
HigherSegment = DailySegment
HigherState = DailySegmentState
